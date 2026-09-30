"""Monotonic event tracing, byte-credit queue, ownership ledger, and fixture validation."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

REQUIRED_TRACE_FIELDS = {"run_id", "ts_ns", "event_type", "plan_d", "plan_t"}


class TraceRecorder:
    """Thread-safe event collector; `ts_ns` is sampled from the monotonic perf counter."""

    def __init__(self, run_id: str, plan_d: str = "synthetic", plan_t: str = "consumer", enabled: bool = True) -> None:
        self.run_id, self.plan_d, self.plan_t = run_id, plan_d, plan_t
        self.enabled = enabled
        self._lock = threading.Lock()
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, **fields: Any) -> dict[str, Any]:
        if not self.enabled:
            return {"run_id": self.run_id, "ts_ns": time.perf_counter_ns(), "event_type": event_type,
                    "plan_d": self.plan_d, "plan_t": self.plan_t, "worker": None, "bytes_in": None,
                    "bytes_out": None, "ready_time_ns": None, "duration_ns": None, **fields}
        with self._lock:
            event = {"run_id": self.run_id, "ts_ns": time.perf_counter_ns(),
                     "event_type": event_type, "plan_d": self.plan_d, "plan_t": self.plan_t,
                     "worker": None, "bytes_in": None, "bytes_out": None,
                     "ready_time_ns": None, "duration_ns": None,
                     "step_id": None, "sample_id": None, "occurrence_id": None,
                     "microbatch_id": None, "object_id": None, "operator": None,
                     "device": None, "bytes": None, "host_live_bytes": None,
                     "pinned_live_bytes": None, "gpu_live_bytes": None, "reason": None,
                     "metadata": {}}
            metadata = fields.pop("metadata", None)
            if metadata:
                event["metadata"].update(metadata)
            event["duration_ns"] = event["metadata"].get("duration_ns")
            event.update(fields)
            self.events.append(event)
            return event

    def write_jsonl(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
            for event in self.events:
                stream.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


class LiveByteLedger:
    """Track object bytes across resource layers and explicit ownership transfers."""

    LAYERS = {"host", "pinned", "gpu"}

    def __init__(self, trace: TraceRecorder) -> None:
        self.trace = trace
        self.live: dict[str, dict[str, Any]] = {}
        self.totals = {layer: 0 for layer in self.LAYERS}
        self._lock = threading.RLock()

    def allocate(self, object_id: str, nbytes: int, layer: str, owner: str, **ids: Any) -> None:
        with self._lock:
            if layer not in self.LAYERS or nbytes <= 0:
                raise ValueError("allocation requires a known layer and positive bytes")
            if object_id in self.live:
                raise ValueError(f"duplicate allocation: {object_id}")
            self.live[object_id] = {"bytes": nbytes, "layer": layer, "owner": owner, **ids}
            self.totals[layer] += nbytes
            self._emit("allocation", object_id, nbytes, layer, owner, **ids)

    def transfer(self, object_id: str, new_owner: str, new_worker: str | None = None) -> None:
        with self._lock:
            item = self._get(object_id)
            old_owner = item["owner"]
            item["owner"] = new_owner
            if new_worker is not None:
                item["worker"] = new_worker
            self._emit("ownership_transfer", object_id, item["bytes"], item["layer"], new_owner,
                       metadata={"old_owner": old_owner, "new_owner": new_owner}, **_ids(item))

    def release(self, object_id: str, owner: str) -> None:
        with self._lock:
            item = self._get(object_id)
            if item["owner"] != owner:
                raise ValueError(f"incorrect ownership transfer/release for {object_id}: owned by {item['owner']}, not {owner}")
            del self.live[object_id]
            self.totals[item["layer"]] -= item["bytes"]
            if self.totals[item["layer"]] < 0:
                raise ValueError(f"negative live bytes for {item['layer']}")
            self._emit("release", object_id, item["bytes"], item["layer"], owner, **_ids(item))

    def _get(self, object_id: str) -> dict[str, Any]:
        try:
            return self.live[object_id]
        except KeyError as exc:
            raise ValueError(f"unknown or already released object: {object_id}") from exc

    def _emit(self, event_type: str, object_id: str, nbytes: int, layer: str, owner: str,
              metadata: dict[str, Any] | None = None, **ids: Any) -> None:
        live_fields = {"host_live_bytes": self.totals["host"], "pinned_live_bytes": self.totals["pinned"],
                       "gpu_live_bytes": self.totals["gpu"]}
        self.trace.emit(event_type, object_id=object_id, bytes=nbytes, device=layer,
                        metadata={"owner": owner, **(metadata or {})}, **live_fields, **ids)


def _ids(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in ("step_id", "sample_id", "occurrence_id", "microbatch_id", "worker")}


class ByteCreditQueue:
    """Condition-backed queue whose admission is limited by queued object bytes."""

    def __init__(self, capacity_bytes: int, trace: TraceRecorder, ledger: LiveByteLedger) -> None:
        if capacity_bytes <= 0:
            raise ValueError("queue capacity must be positive")
        self.capacity_bytes, self.trace, self.ledger = capacity_bytes, trace, ledger
        self.items: deque[dict[str, Any]] = deque()
        self.queued_bytes = 0
        self._condition = threading.Condition()
        self.closed = False

    def put(self, item: dict[str, Any]) -> None:
        nbytes = item["bytes"]
        if nbytes > self.capacity_bytes:
            raise ValueError(f"object {item['object_id']} ({nbytes}) exceeds queue capacity {self.capacity_bytes}")
        self.trace.emit("queue_admission_request", **_event_ids(item), object_id=item["object_id"], bytes=nbytes,
                        metadata={"current_queue_bytes": self.queued_bytes, "capacity_bytes": self.capacity_bytes})
        with self._condition:
            blocked_start: int | None = None
            while self.queued_bytes + nbytes > self.capacity_bytes:
                if blocked_start is None:
                    blocked_start = time.perf_counter_ns()
                    self.trace.emit("queue_block_start", **_event_ids(item), object_id=item["object_id"], bytes=nbytes,
                                    reason="queue_byte_capacity", metadata={"current_queue_bytes": self.queued_bytes,
                                                                               "attempted_queue_bytes": self.queued_bytes + nbytes,
                                                                               "capacity_bytes": self.capacity_bytes})
                self._condition.wait()
            if blocked_start is not None:
                end = time.perf_counter_ns()
                self.trace.emit("queue_block_end", **_event_ids(item), object_id=item["object_id"], bytes=nbytes,
                                reason="queue_byte_capacity", metadata={"duration_ns": end - blocked_start})
            self.ledger.allocate(item["object_id"], nbytes, "host", "queue", **_event_ids(item))
            self.items.append(item)
            self.queued_bytes += nbytes
            self.trace.emit("queue_admission", **_event_ids(item), object_id=item["object_id"], bytes=nbytes,
                            host_live_bytes=self.ledger.totals["host"], pinned_live_bytes=self.ledger.totals["pinned"],
                            gpu_live_bytes=self.ledger.totals["gpu"],
                            metadata={"current_queue_bytes": self.queued_bytes, "capacity_bytes": self.capacity_bytes})
            self._condition.notify_all()

    def get(self) -> dict[str, Any] | None:
        with self._condition:
            wait_start: int | None = None
            while not self.items and not self.closed:
                if wait_start is None:
                    wait_start = time.perf_counter_ns()
                    self.trace.emit("consumer_wait_start", reason="input_unavailable",
                                    metadata={"legal_work_available": False, "critical_dependency": "input_readiness"})
                self._condition.wait()
            if wait_start is not None:
                end = time.perf_counter_ns()
                self.trace.emit("consumer_wait_end", reason="input_unavailable",
                                metadata={"duration_ns": end - wait_start, "legal_work_available": False,
                                          "critical_dependency": "input_readiness"})
            if not self.items:
                return None
            item = self.items.popleft()
            self.ledger.transfer(item["object_id"], "consumer", "consumer-0")
            item["worker"] = "consumer-0"
            self.trace.emit("queue_dequeue", **_event_ids(item), object_id=item["object_id"], bytes=item["bytes"],
                            metadata={"current_queue_bytes": self.queued_bytes, "capacity_bytes": self.capacity_bytes})
            return item

    def release_credit(self, item: dict[str, Any]) -> None:
        """Return admission credit only when the consumer releases the admitted object."""
        with self._condition:
            self.queued_bytes -= item["bytes"]
            if self.queued_bytes < 0:
                raise ValueError("negative byte-credit queue occupancy")
            self.trace.emit("queue_credit_release", **_event_ids(item), object_id=item["object_id"],
                            bytes=item["bytes"], metadata={"current_queue_bytes": self.queued_bytes,
                                                            "capacity_bytes": self.capacity_bytes})
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self.closed = True
            self._condition.notify_all()


def _event_ids(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in ("step_id", "sample_id", "occurrence_id", "microbatch_id", "worker")}


def run_synthetic_pipeline(config: dict[str, Any], records: list[dict[str, Any]], run_id: str = "m2-synthetic-smoke",
                          tracing: bool = True) -> dict[str, Any]:
    """Run a small concurrent producer-consumer fixture and return trace plus accounting."""
    fixture = config["fixture"]
    if len(records) != len(fixture["object_sizes"]):
        raise ValueError("fixture object count must match the frozen manifest occurrence count")
    trace = TraceRecorder(run_id, enabled=tracing)
    ledger = LiveByteLedger(trace)
    queue = ByteCreditQueue(config["resources"]["queue_byte_budget"], trace, ledger)
    failures: list[BaseException] = []
    producer_done = threading.Event()

    def producer() -> None:
        try:
            for i, (record, nbytes, delay_ms) in enumerate(zip(records, fixture["object_sizes"], fixture["producer_delays_ms"])):
                if delay_ms:
                    time.sleep(delay_ms / 1000)
                ids = _event_ids(record)
                ids["worker"] = "producer-0"
                obj = {**ids, "object_id": f"obj-{record['occurrence_id']}", "bytes": nbytes}
                trace.emit("operator_begin", **ids, object_id=obj["object_id"], operator="synthetic_producer")
                trace.emit("operator_end", **ids, object_id=obj["object_id"], operator="synthetic_producer",
                           bytes_out=nbytes, metadata={"duration_ns": 0, "bytes_out": nbytes})
                ready_event = trace.emit("ready", **ids, object_id=obj["object_id"], bytes=nbytes, operator="consumer_input_complete",
                                         metadata={"ready_definition": "all declared consumer input dependencies are available"})
                obj["ready_time_ns"] = ready_event["ts_ns"]
                ready_event["ready_time_ns"] = ready_event["ts_ns"]
                queue.put(obj)
        except BaseException as exc:  # propagate failures from worker thread to caller
            failures.append(exc)
        finally:
            producer_done.set()
            queue.close()

    def consumer() -> None:
        try:
            while True:
                item = queue.get()
                if item is None:
                    return
                ids = _event_ids(item)
                trace.emit("consumer_start", **ids, object_id=item["object_id"], operator="synthetic_consumer",
                           bytes_in=item["bytes"], metadata={"ready_time_ns": item["ready_time_ns"]})
                index = next(i for i, r in enumerate(records) if r["occurrence_id"] == item["occurrence_id"])
                delay = fixture["consumer_delays_ms"][index]
                if delay:
                    time.sleep(delay / 1000)
                trace.emit("consumer_end", **ids, object_id=item["object_id"], operator="synthetic_consumer",
                           bytes_in=item["bytes"])
                ledger.release(item["object_id"], "consumer")
                queue.release_credit(item)
        except BaseException as exc:
            failures.append(exc)
            queue.close()

    producer_thread = threading.Thread(target=producer, name="cspp-producer")
    consumer_thread = threading.Thread(target=consumer, name="cspp-consumer")
    consumer_thread.start()
    producer_thread.start()
    producer_thread.join()
    consumer_thread.join()
    if failures:
        raise RuntimeError(f"synthetic pipeline worker failed: {failures[0]}") from failures[0]
    trace.emit("pipeline_end", metadata={"live_bytes": dict(ledger.totals), "queue_bytes": queue.queued_bytes})
    return {"events": trace.events, "ledger": ledger, "queue": queue, "run_id": run_id}


def validate_trace(events: list[dict[str, Any]], capacity_bytes: int, expected_live_bytes: dict[str, int] | None = None,
                   expected_occurrences: dict[str, dict[str, Any]] | None = None,
                   expected_run_id: str | None = None) -> list[str]:
    errors: list[str] = []
    last_ts = -1
    live: dict[str, dict[str, Any]] = {}
    totals = {"host": 0, "pinned": 0, "gpu": 0}
    queue_bytes = 0
    blocked: set[str] = set()
    queue_admitted: set[str] = set()
    ready: set[str] = set()
    producer_complete: set[str] = set()
    consumer_started: set[str] = set()
    consumer_ended: set[str] = set()
    terminal_occurrences: set[str] = set()
    occurrence_allocations: dict[str, int] = {}
    wait_starts = 0
    wait_ends = 0
    for i, event in enumerate(events):
        missing = REQUIRED_TRACE_FIELDS - event.keys()
        if missing:
            errors.append(f"event[{i}] missing required fields {sorted(missing)}")
            continue
        if not isinstance(event["run_id"], str) or not isinstance(event["event_type"], str):
            errors.append(f"event[{i}] run_id and event_type must be strings")
        if event.get("worker") is not None and not isinstance(event.get("worker"), str):
            errors.append(f"event[{i}] worker must be a string or null")
        if not isinstance(event["ts_ns"], int) or event["ts_ns"] < 0 or event["ts_ns"] < last_ts:
            errors.append(f"event[{i}] timestamp ordering violation")
        last_ts = max(last_ts, event["ts_ns"] if isinstance(event["ts_ns"], int) else last_ts)
        if event.get("bytes") is not None and event["bytes"] < 0:
            errors.append(f"event[{i}] negative byte value")
        for field in ("bytes_in", "bytes_out"):
            if event.get(field) is not None and (not isinstance(event[field], int) or event[field] < 0):
                errors.append(f"event[{i}] invalid {field}")
        for field in ("host_live_bytes", "pinned_live_bytes", "gpu_live_bytes"):
            if event.get(field) is not None and event[field] < 0:
                errors.append(f"event[{i}] negative live bytes in {field}")
        typ, oid = event["event_type"], event.get("object_id")
        meta = event.get("metadata", {})
        if expected_run_id is not None and event["run_id"] != expected_run_id:
            errors.append(f"event[{i}] run_id does not match expected run")
        occurrence_id = event.get("occurrence_id")
        if expected_occurrences is not None and occurrence_id is not None:
            if occurrence_id not in expected_occurrences:
                errors.append(f"event[{i}] unknown occurrence_id: {occurrence_id}")
            else:
                frozen = expected_occurrences[occurrence_id]
                for field in ("sample_id", "step_id", "microbatch_id"):
                    if event.get(field) is not None and event.get(field) != frozen.get(field):
                        errors.append(f"event[{i}] {field} does not match manifest for {occurrence_id}")
        duration = meta.get("duration_ns")
        if duration is not None and (not isinstance(duration, int) or duration < 0):
            errors.append(f"event[{i}] negative or invalid duration")
        if event.get("duration_ns") is not None and (not isinstance(event["duration_ns"], int) or event["duration_ns"] < 0):
            errors.append(f"event[{i}] negative or invalid duration_ns")
        if typ == "operator_end" and event.get("operator") == "synthetic_producer":
            producer_complete.add(oid)
        elif typ == "ready":
            if oid not in producer_complete:
                errors.append(f"ready precedes producer completion for {oid}")
            if event.get("ready_time_ns") != event.get("ts_ns"):
                errors.append(f"ready timestamp is missing or inconsistent for {oid}")
            ready.add(oid)
        elif typ == "consumer_start" and oid not in ready:
            errors.append(f"consumer starts before input readiness for {oid}")
        elif typ == "consumer_start":
            if oid in consumer_started:
                errors.append(f"duplicate consumer start for {oid}")
            consumer_started.add(oid)
            ready_time = meta.get("ready_time_ns")
            if not isinstance(ready_time, int) or ready_time > event["ts_ns"]:
                errors.append(f"consumer start has invalid ready_time for {oid}")
        elif typ == "consumer_end":
            if oid not in consumer_started:
                errors.append(f"consumer end without start for {oid}")
            consumer_ended.add(oid)
        elif typ == "consumer_wait_start":
            wait_starts += 1
            if meta.get("legal_work_available") is not False or meta.get("critical_dependency") != "input_readiness":
                errors.append("consumer wait is not proven to be exposed input waiting")
        elif typ == "consumer_wait_end":
            wait_ends += 1
            if not isinstance(meta.get("duration_ns"), int) or meta["duration_ns"] < 0:
                errors.append("consumer wait has invalid duration")
        elif typ == "allocation":
            if oid in live:
                errors.append(f"duplicate allocation for {oid}")
            elif event.get("device") not in totals or not isinstance(event.get("bytes"), int):
                errors.append(f"invalid allocation for {oid}")
            else:
                layer = event["device"]
                live[oid] = {"bytes": event["bytes"], "layer": layer, "owner": meta.get("owner")}
                totals[layer] += event["bytes"]
                if occurrence_id is not None:
                    occurrence_allocations[occurrence_id] = occurrence_allocations.get(occurrence_id, 0) + 1
        elif typ == "ownership_transfer":
            if oid not in live:
                errors.append(f"ownership transfer for non-live object {oid}")
            elif live[oid]["owner"] != meta.get("old_owner"):
                errors.append(f"incorrect ownership transfer for {oid}")
            else:
                live[oid]["owner"] = meta.get("new_owner")
        elif typ == "release":
            if oid not in live:
                errors.append(f"release for non-live object {oid} (double/missing allocation)")
            elif live[oid]["owner"] != meta.get("owner"):
                errors.append(f"release by non-owner for {oid}")
            else:
                item = live.pop(oid)
                totals[item["layer"]] -= item["bytes"]
                if occurrence_id is not None:
                    terminal_occurrences.add(occurrence_id)
        elif typ == "queue_admission":
            expected_queue = queue_bytes + (event.get("bytes") or 0)
            reported_queue = meta.get("current_queue_bytes", -1)
            if reported_queue != expected_queue:
                errors.append(f"queue byte accounting mismatch at admission for {oid}: expected {expected_queue}, got {reported_queue}")
            queue_bytes = reported_queue
            queue_admitted.add(oid)
            if queue_bytes < 0 or queue_bytes > capacity_bytes:
                errors.append(f"queue capacity exceeded at admission for {oid}: {queue_bytes}>{capacity_bytes}")
        elif typ == "queue_block_start":
            if meta.get("attempted_queue_bytes", 0) <= capacity_bytes:
                errors.append(f"blocked queue attempt does not exceed capacity for {oid}")
            blocked.add(oid)
        elif typ == "queue_block_end":
            if oid not in blocked:
                errors.append(f"queue block end without block start for {oid}")
            blocked.discard(oid)
        elif typ == "queue_dequeue":
            queue_bytes = meta.get("current_queue_bytes", -1)
            if queue_bytes < 0 or queue_bytes > capacity_bytes:
                errors.append(f"invalid queue bytes after dequeue for {oid}")
        elif typ == "queue_credit_release":
            expected_queue = queue_bytes - (event.get("bytes") or 0)
            reported_queue = meta.get("current_queue_bytes", -1)
            if oid not in queue_admitted:
                errors.append(f"queue credit released for unadmitted object {oid}")
            if reported_queue != expected_queue:
                errors.append(f"queue byte accounting mismatch at release for {oid}: expected {expected_queue}, got {reported_queue}")
            queue_bytes = reported_queue
            queue_admitted.discard(oid)
            if queue_bytes < 0 or queue_bytes > capacity_bytes:
                errors.append(f"invalid queue bytes after credit release for {oid}")
    if blocked:
        errors.append(f"unclosed queue block intervals: {sorted(blocked)}")
    if consumer_started - consumer_ended:
        errors.append(f"consumer intervals lack end events: {sorted(consumer_started - consumer_ended)}")
    if queue_admitted or queue_bytes != 0:
        errors.append(f"queue credits remain live: objects={sorted(queue_admitted)}, bytes={queue_bytes}")
    if expected_occurrences is not None:
        expected_ids = set(expected_occurrences)
        for occurrence_id in sorted(expected_ids - terminal_occurrences):
            errors.append(f"manifest occurrence has no terminal object release: {occurrence_id}")
        for occurrence_id, count in occurrence_allocations.items():
            if count != 1:
                errors.append(f"manifest occurrence allocated {count} times: {occurrence_id}")
    if wait_starts != wait_ends:
        errors.append(f"unclosed consumer wait intervals: starts={wait_starts}, ends={wait_ends}")
    if live:
        errors.append(f"leaked live objects: {sorted(live)}")
    expected = expected_live_bytes or {"host": 0, "pinned": 0, "gpu": 0}
    if totals != {**{"host": 0, "pinned": 0, "gpu": 0}, **expected}:
        errors.append(f"live-byte reconciliation failed: final={totals}, expected={expected}")
    if any(value < 0 for value in totals.values()):
        errors.append(f"negative live-byte total: {totals}")
    return errors
