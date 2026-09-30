"""Monotonic trace recording and independent resource/causality reconstruction.

This module implements only the deterministic M0–M2 synthetic measurement fixture.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

LAYERS = ("host", "pinned", "gpu")
IDENTITY_FIELDS = ("sample_id", "occurrence_id", "step_id", "microbatch_id")
PER_OCCURRENCE_EVENTS = {"operator_begin", "operator_end", "dependency_complete", "ready", "queue_admission_request",
                         "queue_block_start", "queue_block_end", "allocation", "queue_admission", "queue_dequeue",
                         "consumer_wait_start", "consumer_wait_end", "consumer_start", "consumer_end", "release",
                         "queue_credit_release", "ownership_transfer"}


def _event_ids(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in (*IDENTITY_FIELDS, "worker")}


class TraceRecorder:
    """Thread-safe recorder. Every timestamp and observed duration uses perf_counter_ns."""

    def __init__(self, run_id: str, plan_d: str = "synthetic", plan_t: str = "consumer", enabled: bool = True,
                 run_context: dict[str, Any] | None = None) -> None:
        self.run_id, self.plan_d, self.plan_t = run_id, plan_d, plan_t
        self.enabled, self.run_context = enabled, run_context or {}
        self._lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self._sequence = 0

    def emit(self, event_type: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            event = {
                "event_id": f"{self.run_id}:{self._sequence}", "run_id": self.run_id,
                "ts_ns": time.perf_counter_ns(), "event_type": event_type,
                "plan_d": self.plan_d, "plan_t": self.plan_t,
                "input_plan_id": self.run_context.get("input_plan_id", self.plan_d),
                "training_plan_id": self.run_context.get("training_plan_id", self.plan_t),
                "run_context_hash": self.run_context.get("run_context_hash"),
                "manifest_hash": self.run_context.get("manifest_hash"),
                "worker": None, "bytes_in": None, "bytes_out": None, "ready_time_ns": None,
                "duration_ns": None, **{k: None for k in IDENTITY_FIELDS},
                "object_id": None, "operator": None, "device": None, "bytes": None,
                "host_live_bytes": None, "pinned_live_bytes": None, "gpu_live_bytes": None,
                "reported_live_bytes": None, "reason": None, "metadata": {},
            }
            metadata = fields.pop("metadata", None)
            if metadata:
                event["metadata"].update(metadata)
            event.update(fields)
            if event.get("duration_ns") is None and event["metadata"].get("observed_duration_ns") is not None:
                event["duration_ns"] = event["metadata"]["observed_duration_ns"]
            if self.enabled:
                self.events.append(event)
            return event

    def begin_interval(self, event_type: str, **fields: Any) -> dict[str, Any]:
        event = self.emit(event_type, **fields)
        event["interval_id"] = event["event_id"]
        return event

    def end_interval(self, event_type: str, start: dict[str, Any], **fields: Any) -> dict[str, Any]:
        end = self.emit(event_type, interval_id=start["interval_id"], **fields)
        duration = end["ts_ns"] - start["ts_ns"]
        end["duration_ns"] = duration
        end["metadata"]["observed_duration_ns"] = duration
        end["metadata"]["duration_ns"] = duration
        return end

    def write_jsonl(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="\n") as stream:
            for event in self.events:
                stream.write(json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")


class LiveByteLedger:
    """Resource-state ledger with permanent object identity and explicit terminal release."""

    def __init__(self, trace: TraceRecorder) -> None:
        self.trace = trace
        self.live: dict[str, dict[str, Any]] = {}
        self.history: dict[str, dict[str, Any]] = {}
        self.totals = {layer: 0 for layer in LAYERS}
        self._lock = threading.RLock()

    def allocate(self, object_id: str, nbytes: int, layer: str, owner: str, **ids: Any) -> None:
        with self._lock:
            if not isinstance(object_id, str) or not object_id or object_id in self.history:
                raise ValueError(f"object identity must be unique: {object_id}")
            if layer not in LAYERS or not isinstance(nbytes, int) or isinstance(nbytes, bool) or nbytes <= 0:
                raise ValueError("allocation requires known layer and positive integer bytes")
            if not isinstance(owner, str) or not owner:
                raise ValueError("allocation owner must be non-empty")
            item = {"bytes": nbytes, "layer": layer, "owner": owner, **ids}
            self.live[object_id] = item
            self.history[object_id] = item
            self.totals[layer] += nbytes
            self._emit("allocation", object_id, item)

    def transfer(self, object_id: str, new_owner: str, new_worker: str | None = None) -> dict[str, Any]:
        with self._lock:
            item = self._get(object_id)
            if not isinstance(new_owner, str) or not new_owner:
                raise ValueError("new owner must be non-empty")
            old_owner = item["owner"]
            item["owner"] = new_owner
            if new_worker is not None:
                item["worker"] = new_worker
            return self._emit("ownership_transfer", object_id, item,
                              {"old_owner": old_owner, "new_owner": new_owner})

    def release(self, object_id: str, owner: str, nbytes: int | None = None, layer: str | None = None) -> None:
        with self._lock:
            item = self._get(object_id)
            if item["owner"] != owner:
                raise ValueError(f"incorrect ownership transfer/release for {object_id}: owned by {item['owner']}, not {owner}")
            if nbytes is not None and nbytes != item["bytes"]:
                raise ValueError(f"release bytes mismatch for {object_id}")
            if layer is not None and layer != item["layer"]:
                raise ValueError(f"release layer mismatch for {object_id}")
            item["released"] = True
            del self.live[object_id]
            self.totals[item["layer"]] -= item["bytes"]
            if self.totals[item["layer"]] < 0:
                raise ValueError(f"negative live bytes for {item['layer']}")
            self._emit("release", object_id, item)

    def _get(self, object_id: str) -> dict[str, Any]:
        if object_id not in self.live:
            raise ValueError(f"unknown or already released object: {object_id}")
        return self.live[object_id]

    def _emit(self, typ: str, oid: str, item: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
        metadata = {"owner": item["owner"], "object_role": item.get("object_role", "input"), **(extra or {})}
        snapshots = dict(self.totals)
        return self.trace.emit(typ, object_id=oid, bytes=item["bytes"], device=item["layer"],
                               reported_live_bytes=snapshots, **{f"{k}_live_bytes": v for k, v in snapshots.items()},
                               metadata=metadata, **_event_ids(item))


class ByteCreditQueue:
    """Byte-credit bounded queue; credit lasts until the object's terminal release."""

    def __init__(self, capacity_bytes: int, trace: TraceRecorder, ledger: LiveByteLedger) -> None:
        if not isinstance(capacity_bytes, int) or capacity_bytes <= 0:
            raise ValueError("queue capacity must be positive")
        self.capacity_bytes, self.trace, self.ledger = capacity_bytes, trace, ledger
        self.items: deque[dict[str, Any]] = deque()
        self.credits: dict[str, int] = {}
        self.dequeued: set[str] = set()
        self.queued_bytes = 0
        self.item_bytes = 0
        self._condition = threading.Condition()
        self.closed = False

    def put(self, item: dict[str, Any]) -> None:
        nbytes, oid = item["bytes"], item["object_id"]
        if not isinstance(nbytes, int) or nbytes <= 0 or nbytes > self.capacity_bytes:
            raise ValueError("object bytes must be positive and no larger than capacity")
        with self._condition:
            self.trace.emit("queue_admission_request", **_event_ids(item), object_id=oid, bytes=nbytes,
                            metadata={"reported_credit_bytes": self.queued_bytes, "current_queue_bytes": self.queued_bytes,
                                      "current_item_bytes": self.item_bytes,
                                      "capacity_basis": "unreleased_object_credits", "capacity_bytes": self.capacity_bytes})
            start = None
            while self.queued_bytes + nbytes > self.capacity_bytes:
                if self.closed:
                    raise RuntimeError("queue closed while producer was blocked")
                if start is None:
                    start = self.trace.begin_interval("queue_block_start", **_event_ids(item), object_id=oid,
                                                      bytes=nbytes, reason="queue_byte_capacity",
                                                      metadata={"attempted_credit_bytes": self.queued_bytes + nbytes,
                                                                "reported_credit_bytes": self.queued_bytes,
                                                                "current_queue_bytes": self.queued_bytes,
                                                                "current_item_bytes": self.item_bytes,
                                                                "capacity_basis": "unreleased_object_credits",
                                                                "capacity_bytes": self.capacity_bytes})
                self._condition.wait()
            if self.closed:
                raise RuntimeError("queue is closed")
            if oid in self.credits:
                raise ValueError(f"duplicate queue admission: {oid}")
            if start:
                self.trace.end_interval("queue_block_end", start, **_event_ids(item), object_id=oid,
                                        bytes=nbytes, reason="queue_byte_capacity",
                                        metadata={"reported_credit_bytes": self.queued_bytes,
                                                  "current_queue_bytes": self.queued_bytes,
                                                  "current_item_bytes": self.item_bytes,
                                                  "capacity_basis": "unreleased_object_credits",
                                                  "capacity_bytes": self.capacity_bytes})
            self.ledger.allocate(oid, nbytes, "host", "queue", object_role="input", **_event_ids(item))
            self.credits[oid] = nbytes
            self.queued_bytes += nbytes
            self.item_bytes += nbytes
            self.items.append(dict(item))
            self.trace.emit("queue_admission", **_event_ids(item), object_id=oid, bytes=nbytes,
                            metadata={"reported_credit_bytes": self.queued_bytes, "current_queue_bytes": self.queued_bytes,
                                      "current_item_bytes": self.item_bytes, "capacity_basis": "unreleased_object_credits",
                                      "capacity_bytes": self.capacity_bytes})
            self._condition.notify_all()

    def get(self, expected_occurrence_id: str | None = None, expected_record: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._condition:
            wait = None
            def find_index() -> int | None:
                if expected_occurrence_id is None:
                    return 0 if self.items else None
                for i, value in enumerate(self.items):
                    if value.get("occurrence_id") == expected_occurrence_id:
                        return i
                return None
            index = find_index()
            while index is None and not self.closed:
                if wait is None:
                    if self.trace.enabled:
                        wait = self.trace.begin_interval(
                            "consumer_wait_start", **(_event_ids(expected_record) if expected_record else {}),
                            reason="input_unavailable",
                            metadata={"critical_dependency": "input_admission"})
                self._condition.wait()
                index = find_index()
            if wait:
                matching = next((e for e in self.trace.events if e["event_type"] == "queue_admission" and
                                 e.get("occurrence_id") == expected_occurrence_id and e["ts_ns"] >= wait["ts_ns"]), None)
                if matching is None:
                    raise RuntimeError("wait ended without relevant input admission")
                self.trace.end_interval("consumer_wait_end", wait, **(_event_ids(expected_record) if expected_record else {}),
                                        reason="input_unavailable",
                                        metadata={"critical_dependency": "input_admission",
                                                  "availability_event_id": matching["event_id"]})
            if index is None:
                return None
            self.items.rotate(-index)
            item = self.items.popleft()
            self.items.rotate(index)
            oid = item["object_id"]
            self.item_bytes -= item["bytes"]
            self.dequeued.add(oid)
            item["worker"] = "consumer-0"
            self.trace.emit("queue_dequeue", **_event_ids(item), object_id=oid, bytes=item["bytes"],
                            metadata={"reported_credit_bytes": self.queued_bytes, "current_queue_bytes": self.queued_bytes,
                                      "current_item_bytes": self.item_bytes, "capacity_basis": "unreleased_object_credits",
                                      "capacity_bytes": self.capacity_bytes})
            transfer = self.ledger.transfer(oid, "consumer", "consumer-0")
            transfer["metadata"].update({
                "reported_credit_bytes": self.queued_bytes,
                "current_queue_bytes": self.queued_bytes,
                "current_item_bytes": self.item_bytes,
                "capacity_basis": "unreleased_object_credits",
                "capacity_bytes": self.capacity_bytes,
            })
            self._condition.notify_all()
            return item

    def release_credit(self, item: dict[str, Any]) -> None:
        with self._condition:
            oid, nbytes = item["object_id"], item["bytes"]
            if oid not in self.credits:
                raise ValueError(f"unknown or duplicate credit return: {oid}")
            if oid not in self.dequeued:
                raise ValueError(f"credit return before dequeue/terminal release: {oid}")
            ledger_item = self.ledger.history.get(oid)
            if ledger_item is None or "released" not in ledger_item:
                raise ValueError(f"credit return before terminal release: {oid}")
            if self.credits[oid] != nbytes:
                raise ValueError(f"credit return bytes mismatch for {oid}")
            del self.credits[oid]
            self.dequeued.remove(oid)
            self.queued_bytes -= nbytes
            self.trace.emit("queue_credit_release", **_event_ids(item), object_id=oid, bytes=nbytes,
                            metadata={"reported_credit_bytes": self.queued_bytes, "current_queue_bytes": self.queued_bytes,
                                      "current_item_bytes": self.item_bytes, "capacity_basis": "unreleased_object_credits",
                                      "capacity_bytes": self.capacity_bytes})
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self.closed = True
            self._condition.notify_all()


def run_synthetic_pipeline(config: dict[str, Any], records: list[dict[str, Any]], run_id: str = "m2-synthetic-smoke",
                           tracing: bool = True, run_context: dict[str, Any] | None = None,
                           producer_order: list[str] | None = None) -> dict[str, Any]:
    """Run a concurrent producer fixture while preserving frozen consumer/update order."""
    from cspp.config import validate_config_manifest
    from cspp.legality import build_execution_context
    validate_config_manifest(config, records)
    context = run_context or build_execution_context(records, config, run_id)
    fixture = config["fixture"]
    trace = TraceRecorder(run_id, config["execution"]["input_plan_id"], config["execution"]["training_plan_id"], tracing, context)
    ledger = LiveByteLedger(trace)
    queue = ByteCreditQueue(config["resources"]["queue_byte_budget"], trace, ledger)
    failures: list[BaseException] = []
    producer_ids = producer_order or [r["occurrence_id"] for r in records]
    by_occurrence = {r["occurrence_id"]: (i, r) for i, r in enumerate(records)}
    if set(producer_ids) != set(by_occurrence) or len(producer_ids) != len(records):
        raise ValueError("producer order must contain each frozen occurrence exactly once")

    def producer() -> None:
        try:
            for occurrence in producer_ids:
                i, record = by_occurrence[occurrence]
                ids = _event_ids(record); ids["worker"] = "producer-0"
                oid, nbytes = f"obj-{occurrence}", fixture["object_sizes"][i]
                dep = f"dep-{occurrence}-decoded"
                dep_meta = {"dependency_id": dep, "dependency_kind": "consumer_input"}
                op = trace.begin_interval("operator_begin", **ids, object_id=oid, operator="fixture_source",
                                          metadata=dep_meta)
                delay = fixture["producer_delays_ms"][i]
                if delay:
                    time.sleep(delay / 1000)
                op_end = trace.end_interval("operator_end", op, **ids, object_id=oid, operator="fixture_source",
                                            bytes_out=nbytes, metadata=dep_meta)
                trace.emit("dependency_complete", **ids, object_id=oid, operator="fixture_source",
                           metadata={"dependency_id": dep, "dependency_kind": "consumer_input", "completion_event_id": op_end["event_id"]})
                ready = trace.emit("ready", **ids, object_id=oid, bytes=nbytes,
                                   metadata={"required_dependency_ids": [dep],
                                             "ready_definition": "all declared consumer-required inputs are available"})
                ready["ready_time_ns"] = ready["ts_ns"]
                queue.put({**ids, "object_id": oid, "bytes": nbytes, "ready_time_ns": ready["ts_ns"],
                           "dependency_id": dep, "record": record})
        except BaseException as exc:
            failures.append(exc)
        finally:
            queue.close()

    def consumer() -> None:
        try:
            for record in sorted(records, key=lambda r: (r["step_id"], r["sample_order_baseline"], r["occurrence_id"])):
                item = queue.get(record["occurrence_id"], record)
                if item is None:
                    raise RuntimeError(f"producer closed before {record['occurrence_id']} became available")
                ids = _event_ids(record); ids["worker"] = "consumer-0"
                start = trace.begin_interval("consumer_start", **ids, object_id=item["object_id"],
                                             operator="synthetic_consumer", bytes_in=item["bytes"],
                                             ready_time_ns=item["ready_time_ns"],
                                             metadata={"ready_time_ns": item["ready_time_ns"], "input_object_ids": [item["object_id"]], "logical_execution": True,
                                                       "frame_ids": record["frame_ids"],
                                                       "frame_selection_spec": record["frame_selection_spec"],
                                                       "draw_purpose": "augmentation",
                                                       "augmentation_draw_u64": __import__("cspp.manifest", fromlist=["keyed_u64"]).keyed_u64(config["experiment"]["seed"], record)})
                delay = fixture["consumer_delays_ms"][by_occurrence[record["occurrence_id"]][0]]
                if delay:
                    time.sleep(delay / 1000)
                end = trace.end_interval("consumer_end", start, **ids, object_id=item["object_id"],
                                         operator="synthetic_consumer", bytes_in=item["bytes"])
                end["metadata"].update({"input_object_ids": [item["object_id"]], "logical_execution": True})
                ledger.release(item["object_id"], "consumer")
                queue.release_credit(item)
        except BaseException as exc:
            failures.append(exc)
            queue.close()

    consumer_thread = threading.Thread(target=consumer, name="cspp-consumer")
    producer_thread = threading.Thread(target=producer, name="cspp-producer")
    consumer_thread.start(); producer_thread.start()
    producer_thread.join(); consumer_thread.join()
    if failures:
        raise RuntimeError(f"synthetic pipeline worker failed: {failures[0]}") from failures[0]
    trace.emit("pipeline_end", metadata={"reported_live_bytes": dict(ledger.totals),
                                         "reported_credit_bytes": queue.queued_bytes,
                                         "reported_item_bytes": queue.item_bytes})
    return {"events": trace.events, "ledger": ledger, "queue": queue, "run_id": run_id, "run_context": context}


def validate_trace(events: list[dict[str, Any]], capacity_bytes: int,
                   expected_live_bytes: dict[str, int] | None = None,
                   expected_occurrences: dict[str, dict[str, Any]] | None = None,
                   expected_run_id: str | None = None,
                   expected_run_context: dict[str, Any] | None = None) -> list[str]:
    """Validate one complete run using the independent explicit state machine."""
    from cspp.tracing.validator import validate_full_run

    return validate_full_run(events, capacity_bytes, expected_live_bytes,
                             expected_occurrences, expected_run_id, expected_run_context)
