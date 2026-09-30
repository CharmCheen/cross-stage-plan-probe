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

    def transfer(self, object_id: str, new_owner: str, new_worker: str | None = None) -> None:
        with self._lock:
            item = self._get(object_id)
            if not isinstance(new_owner, str) or not new_owner:
                raise ValueError("new owner must be non-empty")
            old_owner = item["owner"]
            item["owner"] = new_owner
            if new_worker is not None:
                item["worker"] = new_worker
            self._emit("ownership_transfer", object_id, item, {"old_owner": old_owner, "new_owner": new_owner})

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
                        wait = self.trace.begin_interval("consumer_wait_start", **(_event_ids(expected_record) if expected_record else {}),
                                                         reason="input_unavailable",
                                                         metadata={"critical_dependency": "input_readiness"})
                self._condition.wait()
                index = find_index()
            if wait:
                matching = next((e for e in self.trace.events if e["event_type"] == "queue_admission" and
                                 e.get("occurrence_id") == expected_occurrence_id and e["ts_ns"] >= wait["ts_ns"]), None)
                if matching is None:
                    raise RuntimeError("wait ended without relevant input admission")
                self.trace.end_interval("consumer_wait_end", wait, **(_event_ids(expected_record) if expected_record else {}),
                                        reason="input_unavailable",
                                        metadata={"critical_dependency": "input_readiness",
                                                  "availability_event_id": matching["event_id"]})
            if index is None:
                return None
            self.items.rotate(-index)
            item = self.items.popleft()
            self.items.rotate(index)
            oid = item["object_id"]
            self.item_bytes -= item["bytes"]
            self.dequeued.add(oid)
            self.ledger.transfer(oid, "consumer", "consumer-0")
            item["worker"] = "consumer-0"
            self.trace.emit("queue_dequeue", **_event_ids(item), object_id=oid, bytes=item["bytes"],
                            metadata={"reported_credit_bytes": self.queued_bytes, "current_queue_bytes": self.queued_bytes,
                                      "current_item_bytes": self.item_bytes, "capacity_basis": "unreleased_object_credits",
                                      "capacity_bytes": self.capacity_bytes})
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
                op = trace.begin_interval("operator_begin", **ids, object_id=oid, operator="fixture_source")
                delay = fixture["producer_delays_ms"][i]
                if delay:
                    time.sleep(delay / 1000)
                op_end = trace.end_interval("operator_end", op, **ids, object_id=oid, operator="fixture_source", bytes_out=nbytes)
                dep = f"dep-{occurrence}-decoded"
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
    """Rebuild logical completion, queue credits and resource bytes from events."""
    from cspp.legality import validate_execution_context
    from cspp.manifest import keyed_u64, manifest_hash, validate_manifest
    errors: list[str] = []
    if not isinstance(events, list) or not events:
        return ["trace must contain events"]
    if expected_run_context is not None and not isinstance(expected_run_context, dict):
        errors.append("expected run context must be an object")
        expected_run_context = None
    if expected_occurrences is not None and not isinstance(expected_occurrences, dict):
        errors.append("expected occurrences must be an object keyed by occurrence_id")
        expected_occurrences = None
    if expected_run_context is None:
        errors.append("expected run context is required for provenance-bound trace validation")
    if expected_occurrences is not None and expected_run_context is None:
        errors.append("expected run context is required for manifest-bound validation")
    if expected_run_context is not None:
        errors.extend(validate_execution_context(expected_run_context))
        if expected_occurrences is not None:
            manifest_errors = validate_manifest(list(expected_occurrences.values()))
            errors.extend(manifest_errors)
            if not manifest_errors:
                if expected_run_context.get("manifest_hash") != manifest_hash(list(expected_occurrences.values())):
                    errors.append("expected run context manifest hash does not match expected occurrences")
            if not manifest_errors:
                from cspp.legality import _hash_json
                try:
                    expected_draws = {oid: keyed_u64(expected_run_context.get("seed"), record)
                                      for oid, record in expected_occurrences.items()}
                    expected_frames = {oid: record.get("frame_ids") for oid, record in expected_occurrences.items()}
                    if expected_run_context.get("draws_hash") != _hash_json(expected_draws):
                        errors.append("run context RNG/draw identity does not match frozen manifest and seed")
                    if expected_run_context.get("frame_ids_hash") != _hash_json(expected_frames):
                        errors.append("run context frame-selection identity does not match frozen manifest")
                except (KeyError, TypeError, ValueError) as exc:
                    errors.append(f"cannot recompute run context draws: {exc}")
        if expected_run_id is not None and expected_run_context.get("run_id") != expected_run_id:
            errors.append("expected run id does not match run context")
        if expected_occurrences is not None and all(isinstance(record, dict) for record in expected_occurrences.values()):
            versions = {record.get("workload_version") for record in expected_occurrences.values()}
            if versions != {expected_run_context.get("workload_version")}:
                errors.append("run context workload version does not match frozen manifest")
        resource_config = expected_run_context.get("resources", {})
        if isinstance(resource_config, dict) and resource_config.get("queue_byte_budget") != capacity_bytes:
            errors.append("validator queue capacity does not match run context resources")
    if not isinstance(capacity_bytes, int) or isinstance(capacity_bytes, bool) or capacity_bytes <= 0:
        return errors + ["capacity_bytes must be positive integer"]
    totals = {layer: 0 for layer in LAYERS}
    live: dict[str, dict[str, Any]] = {}
    used_objects: set[str] = set()
    object_identity: dict[str, dict[str, Any]] = {}
    admitted: dict[str, int] = {}
    requests: dict[str, dict[str, Any]] = {}
    dequeued: set[str] = set()
    item_queue: dict[str, int] = {}
    dependencies: dict[str, set[str]] = {}
    completed_dependency_ids: set[tuple[str, str]] = set()
    ready: dict[str, int] = {}
    consumer_start: dict[str, dict[str, Any]] = {}
    consumer_end: dict[str, dict[str, Any]] = {}
    consumed_inputs: dict[str, set[str]] = {}
    occurrence_exec_order: list[str] = []
    intervals: dict[str, dict[str, Any]] = {}
    block_open: dict[str, dict[str, Any]] = {}
    wait_open: dict[str, dict[str, Any]] = {}
    event_ids: set[str] = set()
    last_ts = -1
    credit_total = 0
    last_reported: dict[str, int] | None = None
    if expected_occurrences:
        frozen_records = list(expected_occurrences.values())
        if all(isinstance(r, dict) and isinstance(r.get("step_id"), int) and isinstance(r.get("sample_order_baseline"), int)
               and isinstance(r.get("occurrence_id"), str) for r in frozen_records):
            expected_order = [r["occurrence_id"] for r in sorted(frozen_records, key=lambda r: (r["step_id"], r["sample_order_baseline"], r["occurrence_id"]))]
        else:
            expected_order = []
    else:
        expected_order = []

    def check_ids(event: dict[str, Any], index: int) -> str | None:
        occurrence = event.get("occurrence_id")
        if occurrence is not None and (not isinstance(occurrence, str) or not occurrence):
            errors.append(f"event[{index}] occurrence_id must be a non-empty string")
            return None
        if expected_occurrences is None:
            return occurrence
        if occurrence is None and event.get("event_type") not in PER_OCCURRENCE_EVENTS:
            return None
        if event["event_type"] in PER_OCCURRENCE_EVENTS:
            for key in ("run_id", *IDENTITY_FIELDS):
                value = event.get(key)
                if not isinstance(value, (str, int)) or isinstance(value, bool) or (isinstance(value, str) and not value.strip()):
                    errors.append(f"event[{index}] required identity {key} missing/null")
        if occurrence not in expected_occurrences:
            errors.append(f"event[{index}] unknown occurrence_id: {occurrence}")
            return None
        frozen = expected_occurrences[occurrence]
        if not isinstance(frozen, dict):
            errors.append(f"expected manifest record for {occurrence} must be an object")
            return None
        for key in IDENTITY_FIELDS:
            if event.get(key) != frozen.get(key):
                errors.append(f"event[{index}] {key} mismatch for {occurrence}")
        return occurrence

    def compare_resource_snapshot(event: dict[str, Any], index: int) -> None:
        snapshot = event.get("reported_live_bytes")
        legacy = {layer: event.get(f"{layer}_live_bytes") for layer in LAYERS}
        if snapshot is None and all(v is None for v in legacy.values()):
            return
        if not isinstance(snapshot, dict) or set(snapshot) != set(LAYERS):
            errors.append(f"event[{index}] reported_live_bytes must include host/pinned/gpu")
            return
        for layer in LAYERS:
            if (not isinstance(snapshot[layer], int) or isinstance(snapshot[layer], bool) or snapshot[layer] < 0 or
                    not isinstance(legacy[layer], int) or isinstance(legacy[layer], bool) or legacy[layer] < 0):
                errors.append(f"event[{index}] reported {layer} live bytes must be non-negative integers")
            if snapshot[layer] != totals[layer] or legacy[layer] != totals[layer]:
                errors.append(f"event[{index}] reported {layer} live bytes mismatch: reconstructed={totals[layer]}, reported={snapshot[layer]}/{legacy[layer]}")

    def compare_credit_snapshot(event: dict[str, Any], index: int, items: bool = True) -> None:
        meta = event.get("metadata", {})
        for key in ("reported_credit_bytes", "current_queue_bytes", "current_item_bytes", "capacity_basis", "capacity_bytes"):
            if key not in meta:
                errors.append(f"event[{index}] queue event missing {key} snapshot/contract")
        for key in ("reported_credit_bytes", "current_queue_bytes", "current_item_bytes", "capacity_bytes"):
            if key in meta and (not isinstance(meta[key], int) or isinstance(meta[key], bool) or meta[key] < 0):
                errors.append(f"event[{index}] {key} must be a non-negative integer")
        if meta.get("capacity_basis") not in (None, "unreleased_object_credits"):
            errors.append(f"event[{index}] unsupported queue capacity basis")
        if "capacity_bytes" in meta and meta["capacity_bytes"] != capacity_bytes:
            errors.append(f"event[{index}] queue capacity differs from run config")
        for key in ("reported_credit_bytes", "current_queue_bytes"):
            if key in meta and meta[key] != credit_total:
                errors.append(f"event[{index}] {key} mismatch: reconstructed={credit_total}, reported={meta[key]}")
        if "current_item_bytes" in meta and meta["current_item_bytes"] != sum(item_queue.values()):
            errors.append(f"event[{index}] current_item_bytes mismatch")

    for i, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"event[{i}] must be an object"); continue
        for required in ("event_id", "run_id", "ts_ns", "event_type", "input_plan_id", "training_plan_id", "run_context_hash", "manifest_hash", "metadata"):
            if required not in event:
                errors.append(f"event[{i}] missing required field {required}")
        for key in ("event_id", "run_id", "event_type", "input_plan_id", "training_plan_id", "run_context_hash", "manifest_hash", "plan_d", "plan_t"):
            if not isinstance(event.get(key), str) or not event[key]:
                errors.append(f"event[{i}] invalid or empty required field {key}")
        ts, typ = event.get("ts_ns"), event.get("event_type")
        if not isinstance(typ, str) or not typ:
            errors.append(f"event[{i}] event_type must be a non-empty string")
            continue
        if not isinstance(ts, int) or isinstance(ts, bool) or ts < 0 or ts < last_ts:
            errors.append(f"event[{i}] timestamp ordering violation")
        elif isinstance(ts, int):
            last_ts = ts
        eid = event.get("event_id")
        if not isinstance(eid, str) or not eid or eid in event_ids:
            errors.append(f"event[{i}] missing or duplicate event_id")
        else:
            event_ids.add(eid)
        if not isinstance(event.get("metadata"), dict):
            errors.append(f"event[{i}] metadata must be an object"); continue
        if expected_run_id is not None and event.get("run_id") != expected_run_id:
            errors.append(f"event[{i}] run_id mismatch")
        if expected_run_context is not None:
            for key, context_key in (("manifest_hash", "manifest_hash"), ("run_context_hash", "run_context_hash"),
                                     ("input_plan_id", "input_plan_id"), ("training_plan_id", "training_plan_id")):
                if event.get(key) != expected_run_context.get(context_key):
                    errors.append(f"event[{i}] {key} does not match execution context")
            if event.get("plan_d") != expected_run_context.get("input_plan_id") or event.get("plan_t") != expected_run_context.get("training_plan_id"):
                errors.append(f"event[{i}] legacy plan IDs do not match run context")
        occurrence = check_ids(event, i)
        meta = event["metadata"]
        object_id = event.get("object_id")
        if object_id is not None and (not isinstance(object_id, str) or not object_id):
            errors.append(f"event[{i}] object_id must be a non-empty string or null")
            object_id = None
        if object_id in object_identity and typ in {"ownership_transfer", "release", "queue_admission", "queue_dequeue", "queue_credit_release"}:
            identity = object_identity[object_id]
            if any(event.get(key) != identity.get(key) for key in (*IDENTITY_FIELDS, "run_id")):
                errors.append(f"event[{i}] physical object identity differs from its allocation")
        if event.get("bytes") is not None and (not isinstance(event["bytes"], int) or isinstance(event["bytes"], bool) or event["bytes"] < 0):
            errors.append(f"event[{i}] invalid bytes")
        for layer in LAYERS:
            reported = event.get(f"{layer}_live_bytes")
            if reported is not None and (not isinstance(reported, int) or reported < 0):
                errors.append(f"event[{i}] negative live bytes or invalid value for {layer}")
        if event.get("duration_ns") is not None and (not isinstance(event["duration_ns"], int) or event["duration_ns"] < 0):
            errors.append(f"event[{i}] negative or invalid duration_ns")
        for duration_field in (meta.get("observed_duration_ns"), meta.get("duration_ns")):
            if duration_field is not None and (not isinstance(duration_field, int) or isinstance(duration_field, bool) or duration_field < 0):
                errors.append(f"event[{i}] invalid observed duration")

        if typ in {"operator_begin", "consumer_start", "consumer_wait_start", "queue_block_start"}:
            interval_id = event.get("interval_id") or eid
            if not isinstance(interval_id, str) or not interval_id:
                errors.append(f"event[{i}] interval_id must be a non-empty string")
                interval_id = eid if isinstance(eid, str) and eid else f"invalid-interval-{i}"
            if interval_id in intervals:
                errors.append(f"event[{i}] duplicate interval start")
            intervals[interval_id] = event
            if typ == "queue_block_start": block_open[interval_id] = event
            if typ == "consumer_wait_start": wait_open[interval_id] = event
            if typ == "consumer_start" and occurrence:
                if occurrence in consumer_start:
                    errors.append(f"duplicate consumer execution for {occurrence}")
                consumer_start[occurrence] = event; occurrence_exec_order.append(occurrence)
        elif typ in {"operator_end", "consumer_end", "consumer_wait_end", "queue_block_end"}:
            interval_id = event.get("interval_id")
            if not isinstance(interval_id, str) or not interval_id:
                errors.append(f"event[{i}] interval end has invalid interval_id")
                interval_id = None
            start = intervals.pop(interval_id, None)
            if start is None:
                errors.append(f"event[{i}] unmatched interval end: {typ}")
            else:
                duration = ts - start["ts_ns"] if isinstance(ts, int) and isinstance(start.get("ts_ns"), int) else -1
                duration_reports = [v for v in (event.get("duration_ns"), meta.get("observed_duration_ns"), meta.get("duration_ns")) if v is not None]
                if duration < 0 or not duration_reports or any(v != duration for v in duration_reports):
                    errors.append(f"event[{i}] observed duration does not match interval")
            if typ == "queue_block_end": block_open.pop(interval_id, None)
            if typ == "consumer_wait_end":
                waiting = wait_open.pop(interval_id, None)
                if waiting:
                    availability = meta.get("availability_event_id")
                    target = next((e for e in events if isinstance(e, dict) and e.get("event_id") == availability), None)
                    target_ts = target.get("ts_ns") if isinstance(target, dict) else None
                    wait_start_ts = waiting.get("ts_ns")
                    if (not target or target.get("event_type") != "queue_admission" or target.get("occurrence_id") != occurrence or
                            not isinstance(target_ts, int) or isinstance(target_ts, bool) or
                            not isinstance(wait_start_ts, int) or isinstance(wait_start_ts, bool) or target_ts < wait_start_ts):
                        errors.append(f"event[{i}] wait has no causally relevant input availability")
                    elif isinstance(ts, int) and target_ts > ts:
                        errors.append(f"event[{i}] wait ends before its declared input becomes available")
                    if any(s["event_type"] == "consumer_start" for s in intervals.values()):
                        errors.append(f"event[{i}] exposed wait overlaps consumer compute")
            if typ == "consumer_end" and occurrence:
                if occurrence not in consumer_start:
                    errors.append(f"consumer end without start for {occurrence}")
                else:
                    started = consumer_start[occurrence]
                    if event.get("object_id") != started.get("object_id"):
                        errors.append(f"consumer physical object changed within execution for {occurrence}")
                    if meta.get("input_object_ids") != started.get("metadata", {}).get("input_object_ids"):
                        errors.append(f"consumer input object membership changed within execution for {occurrence}")
                consumer_end[occurrence] = event

        if typ == "dependency_complete":
            dep = meta.get("dependency_id")
            dep_kind = meta.get("dependency_kind")
            completion = meta.get("completion_event_id")
            source = next((e for e in events if isinstance(e, dict) and e.get("event_id") == completion), None)
            source_ts = source.get("ts_ns") if isinstance(source, dict) else None
            if (not isinstance(dep, str) or not dep or not isinstance(dep_kind, str) or not dep_kind or not source or
                    source.get("event_type") not in {"operator_end", "dependency_complete"} or source.get("occurrence_id") != occurrence or
                    not isinstance(source_ts, int) or isinstance(source_ts, bool) or not isinstance(ts, int) or isinstance(ts, bool) or source_ts > ts):
                errors.append(f"event[{i}] dependency completion lacks a completed declared event")
            elif occurrence:
                key = (occurrence, dep)
                if key in completed_dependency_ids:
                    errors.append(f"event[{i}] duplicate dependency completion {dep}")
                completed_dependency_ids.add(key)
                dependencies.setdefault(occurrence, set()).add(dep)
        elif typ == "ready":
            required = meta.get("required_dependency_ids")
            if (not isinstance(required, list) or not required or
                    any(not isinstance(v, str) or not v for v in required) or occurrence is None or
                    not set(required) <= dependencies.get(occurrence, set())):
                errors.append(f"event[{i}] ready has incomplete declared dependencies")
            if occurrence in ready:
                errors.append(f"event[{i}] duplicate ready event for {occurrence}")
            if event.get("ready_time_ns") != ts:
                errors.append(f"event[{i}] ready_time does not match readiness event timestamp")
            if occurrence:
                ready[occurrence] = ts
        elif typ == "allocation":
            if not isinstance(event.get("reported_live_bytes"), dict) or any(event.get(f"{layer}_live_bytes") is None for layer in LAYERS):
                errors.append(f"event[{i}] resource lifecycle event missing live-byte snapshot")
            if not isinstance(object_id, str) or not object_id or object_id in used_objects:
                errors.append(f"event[{i}] duplicate or invalid object identity")
            elif event.get("device") not in LAYERS or not isinstance(event.get("bytes"), int) or event["bytes"] <= 0:
                errors.append(f"event[{i}] invalid allocation")
            else:
                owner = meta.get("owner")
                if not isinstance(owner, str) or not owner:
                    errors.append(f"event[{i}] allocation owner missing")
                role = meta.get("object_role", "input")
                if role not in ("input", "temporary"):
                    errors.append(f"event[{i}] unsupported object role")
                used_objects.add(object_id)
                live[object_id] = {"bytes": event["bytes"], "layer": event["device"], "owner": owner,
                                   "occurrence_id": occurrence, "role": role}
                object_identity[object_id] = {key: event.get(key) for key in (*IDENTITY_FIELDS, "run_id")}
                totals[event["device"]] += event["bytes"]
                compare_resource_snapshot(event, i)
        elif typ == "ownership_transfer":
            if not isinstance(event.get("reported_live_bytes"), dict) or any(event.get(f"{layer}_live_bytes") is None for layer in LAYERS):
                errors.append(f"event[{i}] resource lifecycle event missing live-byte snapshot")
            item = live.get(object_id)
            if (not item or not isinstance(meta.get("old_owner"), str) or not isinstance(meta.get("new_owner"), str) or
                    not meta.get("new_owner") or item["owner"] != meta.get("old_owner")):
                errors.append(f"event[{i}] ownership transfer does not match live owner")
            else:
                item["owner"] = meta["new_owner"]
                compare_resource_snapshot(event, i)
        elif typ == "release":
            if not isinstance(event.get("reported_live_bytes"), dict) or any(event.get(f"{layer}_live_bytes") is None for layer in LAYERS):
                errors.append(f"event[{i}] resource lifecycle event missing live-byte snapshot")
            item = live.get(object_id)
            if not item:
                errors.append(f"event[{i}] release for unknown/already released object {object_id}")
            else:
                if not isinstance(meta.get("owner"), str) or not meta.get("owner"):
                    errors.append(f"event[{i}] release owner missing")
                if item["owner"] != meta.get("owner"):
                    errors.append(f"event[{i}] release owner mismatch")
                if item["bytes"] != event.get("bytes"):
                    errors.append(f"event[{i}] release bytes mismatch")
                if item["layer"] != event.get("device"):
                    errors.append(f"event[{i}] release layer mismatch")
                if item["role"] == "input":
                    logical = item["occurrence_id"]
                    cend = consumer_end.get(logical)
                    if cend is None or cend.get("ts_ns", 0) > ts:
                        errors.append(f"event[{i}] input release before consumer completion")
                totals[item["layer"]] -= item["bytes"]
                del live[object_id]
                compare_resource_snapshot(event, i)
        elif typ == "queue_admission_request":
            if object_id in requests:
                errors.append(f"event[{i}] duplicate admission request for {object_id}")
            else:
                requests[object_id] = event
            if "reported_credit_bytes" not in meta:
                errors.append(f"event[{i}] queue request missing credit snapshot")
            compare_credit_snapshot(event, i, items=False)
        elif typ == "queue_block_start":
            if object_id not in requests:
                errors.append(f"event[{i}] blocked object has no admission request")
            if meta.get("attempted_credit_bytes") != credit_total + (event.get("bytes") or 0):
                errors.append(f"event[{i}] blocked attempt credit snapshot mismatch")
            if credit_total + (event.get("bytes") or 0) <= capacity_bytes:
                errors.append(f"event[{i}] block requested without exhausting byte credits")
            if "reported_credit_bytes" not in meta:
                errors.append(f"event[{i}] blocked request missing credit snapshot")
            compare_credit_snapshot(event, i)
        elif typ == "queue_block_end":
            compare_credit_snapshot(event, i)
        elif typ == "consumer_wait_start":
            if occurrence in ready and any(live.get(oid, {}).get("occurrence_id") == occurrence for oid in item_queue):
                errors.append(f"event[{i}] input wait began although required input was already ready")
        elif typ == "queue_admission":
            if object_id not in requests:
                errors.append(f"event[{i}] admission has no preceding request")
            else:
                requests.pop(object_id, None)
            if object_id in admitted or object_id not in live or live[object_id]["role"] != "input":
                errors.append(f"event[{i}] admission lacks unique live input allocation")
            amount = event.get("bytes") or 0
            if object_id in live and live[object_id]["bytes"] != amount:
                errors.append(f"event[{i}] admission bytes differ from allocated object")
            if amount <= 0 or credit_total + amount > capacity_bytes:
                errors.append(f"event[{i}] queue byte capacity exceeded")
            if occurrence and occurrence not in ready:
                errors.append(f"event[{i}] admission before readiness")
            admitted[object_id] = amount; credit_total += amount; item_queue[object_id] = amount
            compare_credit_snapshot(event, i)
        elif typ == "queue_dequeue":
            if object_id not in admitted or object_id not in item_queue or object_id in dequeued:
                errors.append(f"event[{i}] dequeue without one queued admission")
            else:
                if item_queue[object_id] != event.get("bytes"):
                    errors.append(f"event[{i}] dequeue object bytes mismatch")
                del item_queue[object_id]; dequeued.add(object_id)
            compare_credit_snapshot(event, i)
        elif typ == "consumer_start":
            if occurrence not in ready or event.get("ts_ns", 0) < ready.get(occurrence, 0):
                errors.append(f"event[{i}] consumer starts before ready")
            input_ids = meta.get("input_object_ids")
            if expected_occurrences is not None and expected_run_context is not None and occurrence in expected_occurrences:
                frozen = expected_occurrences[occurrence]
                if meta.get("frame_ids") != frozen.get("frame_ids") or meta.get("frame_selection_spec") != frozen.get("frame_selection_spec"):
                    errors.append(f"event[{i}] execution frame-selection identity mismatch")
                try:
                    if meta.get("draw_purpose") != "augmentation":
                        errors.append(f"event[{i}] unsupported RNG draw purpose")
                    draw = keyed_u64(expected_run_context["seed"], frozen, meta.get("draw_purpose"))
                    if meta.get("augmentation_draw_u64") != draw:
                        errors.append(f"event[{i}] actual RNG draw differs from frozen seed/occurrence")
                except (KeyError, TypeError, ValueError) as exc:
                    errors.append(f"event[{i}] cannot verify actual RNG draw: {exc}")
            if not isinstance(input_ids, list) or not input_ids:
                errors.append(f"event[{i}] consumer execution lacks input objects")
            else:
                for oid in input_ids:
                    if not isinstance(oid, str) or not oid:
                        errors.append(f"event[{i}] consumer input object ID is invalid")
                        continue
                    if oid not in dequeued or live.get(oid, {}).get("occurrence_id") != occurrence:
                        errors.append(f"event[{i}] consumer input object not legally dequeued")
                    elif event.get("object_id") != oid:
                        errors.append(f"event[{i}] consumer input object identity mismatch")
                    else:
                        consumed_inputs.setdefault(occurrence, set()).add(oid)
        elif typ == "queue_credit_release":
            if object_id not in admitted or object_id not in dequeued:
                errors.append(f"event[{i}] credit return for unknown/not dequeued object")
            elif object_id in live:
                errors.append(f"event[{i}] credit returned before terminal release")
            elif admitted[object_id] != event.get("bytes"):
                errors.append(f"event[{i}] credit return bytes mismatch")
            else:
                credit_total -= admitted.pop(object_id); dequeued.remove(object_id)
            compare_credit_snapshot(event, i)
        elif typ == "pipeline_end":
            if meta.get("reported_credit_bytes") != credit_total or meta.get("reported_item_bytes") != sum(item_queue.values()):
                errors.append(f"event[{i}] pipeline-end queue snapshots mismatch")
            if meta.get("reported_live_bytes") != totals:
                errors.append(f"event[{i}] pipeline-end live-byte snapshot mismatch")

        compare_resource_snapshot(event, i)
        if expected_run_context is not None:
            resource_config = expected_run_context.get("resources", {})
            if isinstance(resource_config, dict):
                for layer, budget_field in (("host", "host_memory_budget_bytes"),
                                            ("pinned", "pinned_memory_budget_bytes"),
                                            ("gpu", "gpu_memory_budget_bytes")):
                    budget = resource_config.get(budget_field)
                    if isinstance(budget, int) and not isinstance(budget, bool) and totals[layer] > budget:
                        errors.append(f"event[{i}] reconstructed {layer} live bytes exceed configured budget")
        if typ in {"queue_admission_request", "queue_admission", "queue_dequeue", "queue_credit_release"}:
            compare_credit_snapshot(event, i)

    if intervals:
        errors.append(f"unmatched intervals: {sorted(intervals)}")
    if block_open:
        errors.append(f"unclosed producer blocked intervals: {sorted(block_open)}")
    if wait_open:
        errors.append(f"unclosed consumer wait intervals: {sorted(wait_open)}")
    if requests:
        errors.append(f"admission requests never admitted: {sorted(requests)}")
    if live:
        errors.append(f"leaked live objects: {sorted(live)}")
    if admitted or credit_total != 0 or item_queue:
        errors.append(f"queue state not terminal: credits={admitted}, total={credit_total}, items={item_queue}")
    if any(v < 0 for v in totals.values()):
        errors.append(f"negative live-byte total: {totals}")
    expected = {layer: 0 for layer in LAYERS}
    expected.update(expected_live_bytes or {})
    if totals != expected:
        errors.append(f"live-byte reconciliation failed: final={totals}, expected={expected}")
    if expected_occurrences is not None:
        for oid in expected_order:
            if oid not in consumer_start or oid not in consumer_end:
                errors.append(f"required logical occurrence lacks exactly one consumer execution: {oid}")
            if sum(1 for e in events if e.get("event_type") == "queue_admission" and e.get("occurrence_id") == oid) < 1:
                errors.append(f"required occurrence lacks input admission: {oid}")
        if occurrence_exec_order != expected_order:
            errors.append("consumer execution violates frozen step/order semantics")
    return list(dict.fromkeys(errors))
