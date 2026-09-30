"""Fail-closed M0–M2 full-run trace state machine.

The validator rebuilds logical work, dependency, interval, queue, ownership, and
resource states from the serialized event sequence. It intentionally has no
dependency on the runtime queue or resource ledger implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LAYERS = ("host", "pinned", "gpu")
IDENTITY = ("run_id", "sample_id", "occurrence_id", "step_id", "microbatch_id")
PAIRS = {
    "operator_begin": "operator_end",
    "consumer_start": "consumer_end",
    "consumer_wait_start": "consumer_wait_end",
    "queue_block_start": "queue_block_end",
}
END_TO_START = {end: start for start, end in PAIRS.items()}
SUPPORTED_EVENTS = {
    *PAIRS, *END_TO_START, "dependency_complete", "ready", "allocation",
    "ownership_transfer", "release", "queue_admission_request", "queue_admission",
    "queue_dequeue", "queue_credit_release", "pipeline_end",
}


@dataclass
class LogicalOccurrenceState:
    """One frozen training occurrence, independent of its physical buffers."""

    record: dict[str, Any]
    phase: str = "PENDING"
    dependencies: set[str] = field(default_factory=set)
    ready_ns: int | None = None
    required_inputs: dict[str, tuple[str, ...]] = field(default_factory=dict)
    admitted_objects: set[str] = field(default_factory=set)
    dequeued_objects: set[str] = field(default_factory=set)
    consumer_start: dict[str, Any] | None = None
    consumer_end: dict[str, Any] | None = None
    active_input_objects: set[str] = field(default_factory=set)
    violations: list[str] = field(default_factory=list)

    def advance(self, phase: str) -> None:
        allowed = {
            "PENDING": {"PENDING", "DEPENDENCIES_SATISFIED"},
            "DEPENDENCIES_SATISFIED": {"DEPENDENCIES_SATISFIED", "READY"},
            "READY": {"READY", "ADMITTED"},
            "ADMITTED": {"ADMITTED", "DEQUEUED"},
            "DEQUEUED": {"DEQUEUED", "EXECUTING"},
            "EXECUTING": {"EXECUTING", "COMPLETED"},
            "COMPLETED": {"COMPLETED"},
        }
        if phase not in allowed.get(self.phase, set()):
            self.violations.append(f"illegal logical occurrence transition {self.phase} -> {phase}")
            return
        self.phase = phase


@dataclass(frozen=True)
class DependencyState:
    occurrence_id: str
    dependency_id: str
    dependency_kind: str
    root_event_id: str
    object_id: str | None


@dataclass
class PhysicalObjectState:
    """A physical allocation with immutable identity/size/layer/role/work owner."""

    object_id: str
    nbytes: int
    layer: str
    occurrence_id: str | None
    role: str
    owner: str
    status: str = "ALLOCATED"
    readable_by: set[str] = field(default_factory=set)
    required_dependency_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntervalState:
    interval_id: str
    start_type: str
    start: dict[str, Any]
    end: dict[str, Any]


@dataclass
class ConsumerTimelineState:
    """Closed consumer intervals are compared globally after event replay."""

    compute: list[tuple[int, int, str]] = field(default_factory=list)
    waits: list[tuple[int, int, str, dict[str, Any], dict[str, Any]]] = field(default_factory=list)


class TraceStateMachine:
    """Reconstruct and validate a complete frozen-work run."""

    def __init__(self, capacity_bytes: int, expected_live_bytes: dict[str, int] | None,
                 expected_occurrences: dict[str, dict[str, Any]] | None,
                 expected_run_id: str | None, run_context: dict[str, Any] | None) -> None:
        self.capacity = capacity_bytes
        self.expected_live = {layer: 0 for layer in LAYERS}
        if isinstance(expected_live_bytes, dict):
            self.expected_live.update(expected_live_bytes)
        self.context = run_context
        self.explicit_run_id = expected_run_id
        self.errors: list[str] = []
        self.occurrences: dict[str, LogicalOccurrenceState] = {}
        self.order: list[str] = []
        self.dependencies: dict[tuple[str, str], DependencyState] = {}
        self.validated_completions: dict[str, dict[str, Any]] = {}
        self.objects: dict[str, PhysicalObjectState] = {}
        self.used_object_ids: set[str] = set()
        self.totals = {layer: 0 for layer in LAYERS}
        self.queue_items: dict[str, int] = {}
        self.admission_requests: dict[str, dict[str, Any]] = {}
        self.credit: dict[str, int] = {}
        self.credit_total = 0
        self.used_interval_ids: set[str] = set()
        self.open_intervals: dict[str, tuple[str, dict[str, Any]]] = {}
        self.closed_intervals: list[IntervalState] = []
        self.timeline = ConsumerTimelineState()
        self.event_ids: set[str] = set()
        self.event_by_id: dict[str, dict[str, Any]] = {}
        self.last_ts = -1
        self.pipeline_ends: list[dict[str, Any]] = []
        self.admitted_event_by_object: dict[str, dict[str, Any]] = {}
        self.ready_events: dict[str, dict[str, Any]] = {}
        self.wait_phase_at_start: dict[str, str] = {}
        self.event_indices: dict[str, int] = {}
        self.run_id: str | None = None
        self._prepare_context(expected_occurrences)

    def error(self, index: int | str, message: str) -> None:
        self.errors.append(f"event[{index}] {message}")

    def _prepare_context(self, expected_occurrences: dict[str, dict[str, Any]] | None) -> None:
        from cspp.legality import validate_execution_context
        from cspp.manifest import keyed_u64, manifest_hash, validate_manifest

        if not isinstance(self.context, dict):
            self.errors.append("full-run validation requires expected_run_context")
            self.context = None
        else:
            self.errors.extend(validate_execution_context(self.context))
            context_run = self.context.get("run_id")
            if self.explicit_run_id is not None and self.explicit_run_id != context_run:
                self.errors.append("expected run id does not match run context")
            if not isinstance(context_run, str) or not context_run:
                self.errors.append("run context must contain a non-empty run_id")
            else:
                self.run_id = context_run

        if not isinstance(expected_occurrences, dict) or not expected_occurrences:
            self.errors.append("full-run validation requires a non-empty frozen expected_occurrences manifest")
            return
        records = list(expected_occurrences.values())
        self.errors.extend(validate_manifest(records))
        record_ids = [r.get("occurrence_id") for r in records if isinstance(r, dict)]
        valid_record_ids = [value for value in record_ids if isinstance(value, str)]
        if set(valid_record_ids) != set(expected_occurrences):
            self.errors.append("expected occurrence mapping keys do not match frozen occurrence IDs")
        if len(valid_record_ids) != len(records) or len(set(valid_record_ids)) != len(valid_record_ids):
            self.errors.append("frozen expected occurrences contain duplicate identities")
        for oid, record in expected_occurrences.items():
            if not isinstance(oid, str) or not isinstance(record, dict):
                continue
            self.occurrences[oid] = LogicalOccurrenceState(record)
        if len(self.occurrences) != len(expected_occurrences):
            return
        if all(isinstance(state.record.get("step_id"), int) and not isinstance(state.record.get("step_id"), bool)
               and isinstance(state.record.get("sample_order_baseline"), int) and not isinstance(state.record.get("sample_order_baseline"), bool)
               for state in self.occurrences.values()):
            self.order = sorted(self.occurrences, key=lambda oid: (
                self.occurrences[oid].record["step_id"],
                self.occurrences[oid].record["sample_order_baseline"], oid))
        else:
            self.errors.append("frozen occurrences lack valid step/order fields")

        if self.context is not None:
            if not self.errors and len(self.occurrences) == len(expected_occurrences):
                try:
                    if self.context.get("manifest_hash") != manifest_hash(records):
                        self.errors.append("expected run context manifest hash does not match expected occurrences")
                    if {r.get("workload_version") for r in records} != {self.context.get("workload_version")}:
                        self.errors.append("run context workload version does not match frozen manifest")
                    from cspp.legality import _hash_json
                    draws = {r["occurrence_id"]: keyed_u64(self.context["seed"], r) for r in records}
                    frames = {r["occurrence_id"]: r["frame_ids"] for r in records}
                    if self.context.get("draws_hash") != _hash_json(draws):
                        self.errors.append("run context RNG/draw identity does not match frozen manifest and seed")
                    if self.context.get("frame_ids_hash") != _hash_json(frames):
                        self.errors.append("run context frame-selection identity does not match frozen manifest")
                    resources = self.context.get("resources")
                    if not isinstance(resources, dict) or resources.get("queue_byte_budget") != self.capacity:
                        self.errors.append("validator queue capacity does not match run context resources")
                except (KeyError, TypeError, ValueError) as exc:
                    self.errors.append(f"cannot recompute run context workload identity: {exc}")

    def _identity(self, event: dict[str, Any], index: int, required: bool = True) -> str | None:
        oid = event.get("occurrence_id")
        if not required and oid is None:
            return None
        for key in ("run_id", "sample_id", "occurrence_id", "step_id", "microbatch_id"):
            value = event.get(key)
            if (not isinstance(value, (str, int)) or isinstance(value, bool) or
                    (isinstance(value, str) and not value.strip())):
                self.error(index, f"required identity {key} missing/null or invalid")
        if not isinstance(oid, str) or oid not in self.occurrences:
            if not self.occurrences and isinstance(oid, str) and oid:
                return oid
            self.error(index, f"unknown or missing frozen occurrence_id: {oid}")
            return None
        record = self.occurrences[oid].record
        for key in ("sample_id", "occurrence_id", "step_id", "microbatch_id"):
            if event.get(key) != record.get(key):
                self.error(index, f"{key} differs from frozen occurrence {oid}")
        return oid

    def _context(self, event: dict[str, Any], index: int) -> None:
        if self.context is None:
            return
        expected = {
            "run_id": self.run_id,
            "run_context_hash": self.context.get("run_context_hash"),
            "manifest_hash": self.context.get("manifest_hash"),
            "input_plan_id": self.context.get("input_plan_id"),
            "training_plan_id": self.context.get("training_plan_id"),
            "plan_d": self.context.get("input_plan_id"),
            "plan_t": self.context.get("training_plan_id"),
        }
        for key, value in expected.items():
            if event.get(key) != value:
                self.error(index, f"{key} does not match full run context")

    def _snapshot_live(self, event: dict[str, Any], index: int, required: bool = False) -> None:
        snapshot = event.get("reported_live_bytes")
        legacy = {layer: event.get(f"{layer}_live_bytes") for layer in LAYERS}
        if not required and snapshot is None and all(value is None for value in legacy.values()):
            return
        if not isinstance(snapshot, dict) or set(snapshot) != set(LAYERS):
            self.error(index, "reported_live_bytes must contain host/pinned/gpu")
            return
        for layer in LAYERS:
            if ((isinstance(snapshot.get(layer), int) and not isinstance(snapshot.get(layer), bool) and snapshot[layer] < 0) or
                    (isinstance(legacy.get(layer), int) and not isinstance(legacy.get(layer), bool) and legacy[layer] < 0)):
                self.error(index, f"negative live bytes reported for {layer}")
            if (not isinstance(snapshot.get(layer), int) or isinstance(snapshot.get(layer), bool) or
                    not isinstance(legacy.get(layer), int) or isinstance(legacy.get(layer), bool) or
                    snapshot.get(layer) != self.totals[layer] or legacy.get(layer) != self.totals[layer]):
                self.error(index, f"reported {layer} live bytes mismatch: reconstructed={self.totals[layer]}, reported={snapshot.get(layer)}/{legacy.get(layer)}")

    def _snapshot_credit(self, event: dict[str, Any], index: int, *, item_required: bool = True) -> None:
        meta = event["metadata"]
        required = ["reported_credit_bytes", "current_queue_bytes", "capacity_basis", "capacity_bytes"]
        if item_required:
            required.append("current_item_bytes")
        for key in required:
            if key not in meta:
                self.error(index, f"queue event missing {key}")
        for key in ("reported_credit_bytes", "current_queue_bytes"):
            if key in meta and (not isinstance(meta[key], int) or isinstance(meta[key], bool) or meta[key] != self.credit_total):
                self.error(index, f"{key} mismatch with reconstructed unreleased credits")
        if "current_item_bytes" in meta:
            item_bytes = meta["current_item_bytes"]
            if not isinstance(item_bytes, int) or isinstance(item_bytes, bool) or item_bytes != sum(self.queue_items.values()):
                self.error(index, "current_item_bytes mismatch with reconstructed queue items")
        if meta.get("capacity_basis") != "unreleased_object_credits":
            self.error(index, "unsupported queue capacity basis")
        if meta.get("capacity_bytes") != self.capacity:
            self.error(index, "reported queue capacity differs from validator capacity")

    def _interval_start(self, event: dict[str, Any], index: int, oid: str | None) -> None:
        interval_id = event.get("interval_id")
        if not isinstance(interval_id, str) or not interval_id:
            self.error(index, "interval start requires a non-empty interval_id")
            return
        if interval_id in self.used_interval_ids:
            self.error(index, f"interval_id reused: {interval_id}")
            return
        self.used_interval_ids.add(interval_id)
        self.open_intervals[interval_id] = (event["event_type"], event)
        if event["event_type"] == "consumer_start" and oid in self.occurrences:
            state = self.occurrences[oid]
            if state.consumer_start is not None or state.phase == "COMPLETED":
                self.error(index, f"logical occurrence executed more than once: {oid}")
            expected_index = sum(1 for item in self.occurrences.values() if item.consumer_start is not None)
            if expected_index >= len(self.order) or self.order[expected_index] != oid:
                self.error(index, "consumer execution violates frozen step/order semantics")
            state.consumer_start = event
        if event["event_type"] == "consumer_wait_start" and oid in self.occurrences:
            state = self.occurrences[oid]
            next_oid = next((key for key in self.order if self.occurrences[key].phase != "COMPLETED"), None)
            if oid != next_oid:
                self.error(index, "input wait is not for the next frozen legal consumer occurrence")
            if state.phase in {"ADMITTED", "DEQUEUED", "EXECUTING", "COMPLETED"}:
                self.error(index, "input wait began after legal input work was available")
            if event.get("reason") != "input_unavailable" or event["metadata"].get("critical_dependency") != "input_admission":
                self.error(index, "input wait does not identify the declared input_admission dependency")
            interval_id = event.get("interval_id")
            if isinstance(interval_id, str):
                self.wait_phase_at_start[interval_id] = state.phase

    def _interval_end(self, event: dict[str, Any], index: int, oid: str | None) -> None:
        interval_id = event.get("interval_id")
        pair_start = END_TO_START[event["event_type"]]
        opened = self.open_intervals.pop(interval_id, None) if isinstance(interval_id, str) else None
        if opened is None:
            self.error(index, f"unmatched interval end: {event['event_type']}")
            return
        start_type, start = opened
        if start_type != pair_start:
            self.error(index, f"interval type mismatch: {start_type} cannot end with {event['event_type']}")
        for key in ("run_id", "sample_id", "occurrence_id", "step_id", "microbatch_id"):
            if start.get(key) != event.get(key):
                self.error(index, f"interval {interval_id} {key} differs across pair")
        for key in ("object_id", "worker", "operator", "device"):
            if start.get(key) != event.get(key):
                self.error(index, f"interval {interval_id} {key} differs across pair")
        if start_type == "operator_begin" and (not isinstance(start.get("operator"), str) or not start.get("operator")):
            self.error(index, "producer interval requires a non-empty declared operator")
        start_ts, end_ts = start.get("ts_ns"), event.get("ts_ns")
        duration = end_ts - start_ts if isinstance(end_ts, int) and isinstance(start_ts, int) else -1
        meta = event["metadata"]
        duration_values = [value for value in (event.get("duration_ns"), meta.get("observed_duration_ns"), meta.get("duration_ns")) if value is not None]
        if duration < 0 or not duration_values or any(value != duration for value in duration_values):
            self.error(index, "observed duration does not equal paired monotonic interval")
        interval = IntervalState(interval_id, start_type, start, event)
        self.closed_intervals.append(interval)
        if start_type == "operator_begin" and END_TO_START.get(event.get("event_type")) == "operator_begin" and duration >= 0:
            self.validated_completions[event.get("event_id", "")] = event
        if start_type == "consumer_start" and oid in self.occurrences:
            state = self.occurrences[oid]
            if state.consumer_start is None:
                self.error(index, f"consumer end without start for {oid}")
            else:
                start_event = state.consumer_start
                if start_event.get("metadata", {}).get("input_object_ids") != meta.get("input_object_ids"):
                    self.error(index, f"consumer input membership differs across execution for {oid}")
                state.consumer_end = event
                state.active_input_objects.clear()
                state.advance("COMPLETED")
                self.timeline.compute.append((start_ts, end_ts, oid))
        elif start_type == "consumer_wait_start":
            if start.get("metadata", {}).get("critical_dependency") != event.get("metadata", {}).get("critical_dependency"):
                self.error(index, "wait critical dependency differs across interval")
            self.timeline.waits.append((start_ts, end_ts, oid or "", start, event))

    def _process_dependency(self, event: dict[str, Any], index: int, oid: str | None) -> None:
        if oid not in self.occurrences:
            return
        meta = event["metadata"]
        dep_id, dep_kind, root_id = meta.get("dependency_id"), meta.get("dependency_kind"), meta.get("completion_event_id")
        if not isinstance(dep_id, str) or not dep_id or not isinstance(dep_kind, str) or not dep_kind:
            self.error(index, "dependency requires non-empty dependency_id and dependency_kind")
            return
        if not isinstance(root_id, str) or root_id == event.get("event_id"):
            self.error(index, "dependency completion has a self or invalid causal root")
            return
        root = self.validated_completions.get(root_id)
        if root is None:
            self.error(index, "dependency completion must reference a previously validated causal root")
            return
        if oid is None or root.get("occurrence_id") != oid:
            self.error(index, "dependency causal root belongs to a different occurrence")
            return
        for key in ("run_id", "sample_id", "step_id", "microbatch_id"):
            if root.get(key) != event.get(key):
                self.error(index, f"dependency causal root {key} mismatch")
        if root.get("object_id") != event.get("object_id"):
            self.error(index, "dependency causal root physical identity mismatch")
        root_meta = root.get("metadata", {})
        prior_id = root_meta.get("dependency_id")
        prior_kind = root_meta.get("dependency_kind")
        if root.get("event_type") == "operator_end":
            if prior_id != dep_id or prior_kind != dep_kind:
                self.error(index, "producer root identity does not match declared dependency")
                return
        elif root.get("event_type") == "dependency_complete":
            if (meta.get("source_dependency_id") != prior_id or
                    meta.get("source_dependency_kind") != prior_kind):
                self.error(index, "derived dependency source identity does not match validated dependency")
                return
        else:
            self.error(index, "dependency root is not a validated producer or dependency completion")
            return
        key = (oid, dep_id)
        if key in self.dependencies:
            self.error(index, f"duplicate dependency completion: {dep_id}")
            return
        state = DependencyState(oid, dep_id, dep_kind, root_id, event.get("object_id"))
        self.dependencies[key] = state
        self.occurrences[oid].dependencies.add(dep_id)
        self.occurrences[oid].advance("DEPENDENCIES_SATISFIED")
        self.validated_completions[event.get("event_id", "")] = event

    def _process_ready(self, event: dict[str, Any], index: int, oid: str | None) -> None:
        if oid not in self.occurrences:
            return
        state = self.occurrences[oid]
        required = event["metadata"].get("required_dependency_ids")
        valid_required = isinstance(required, list) and bool(required) and all(isinstance(value, str) and value for value in required)
        if (not valid_required or len(set(required)) != len(required) or not set(required).issubset(state.dependencies)):
            self.error(index, "ready has incomplete declared dependencies (missing, duplicate, or unsatisfied)")
            return
        if oid in self.ready_events:
            self.error(index, f"duplicate ready event for {oid}")
            return
        if event.get("ready_time_ns") != event.get("ts_ns"):
            self.error(index, "ready_time_ns differs from the readiness event timestamp")
        if "ready_time_ns" in event["metadata"] and event["metadata"].get("ready_time_ns") != event.get("ready_time_ns"):
            self.error(index, "top-level and metadata ready_time_ns disagree")
        declared_inputs = event["metadata"].get("required_inputs")
        if not isinstance(declared_inputs, list) or not declared_inputs:
            self.error(index, "ready requires a non-empty required_inputs binding")
            return
        input_bindings: dict[str, tuple[str, ...]] = {}
        for binding in declared_inputs:
            if not isinstance(binding, dict):
                self.error(index, "required_inputs entries must be objects")
                return
            input_id = binding.get("object_id")
            dependency_ids = binding.get("dependency_ids")
            if (not isinstance(input_id, str) or not input_id or input_id in input_bindings or
                    not isinstance(dependency_ids, list) or not dependency_ids or
                    any(not isinstance(value, str) or not value for value in dependency_ids) or
                    len(dependency_ids) != len(set(dependency_ids))):
                self.error(index, "required input binding has invalid or duplicate object/dependency identity")
                return
            if not set(dependency_ids).issubset(state.dependencies):
                self.error(index, f"required input {input_id} has an unsatisfied dependency binding")
            input_bindings[input_id] = tuple(dependency_ids)
        binding_dependency_ids = {dep for values in input_bindings.values() for dep in values}
        if binding_dependency_ids != set(required):
            self.error(index, "ready dependency set does not exactly cover required input bindings")
            return
        state.required_inputs = input_bindings
        self.ready_events[oid] = event
        state.ready_ns = event["ts_ns"]
        state.advance("READY")

    def _process_allocation(self, event: dict[str, Any], index: int, oid: str | None) -> None:
        object_id, nbytes, layer = event.get("object_id"), event.get("bytes"), event.get("device")
        meta = event["metadata"]
        role, owner = meta.get("object_role", "input"), meta.get("owner")
        if not isinstance(object_id, str) or not object_id or object_id in self.used_object_ids:
            self.error(index, "physical object allocation identity must be unique")
            return
        if layer not in LAYERS or not isinstance(nbytes, int) or isinstance(nbytes, bool) or nbytes <= 0:
            self.error(index, "allocation requires valid resource layer and positive integer bytes")
            return
        if not isinstance(role, str) or role not in {"input", "temporary"} or not isinstance(owner, str) or not owner:
            self.error(index, "allocation requires supported role and non-empty owner")
            return
        readable = meta.get("readable_by", [])
        if not isinstance(readable, list) or any(not isinstance(value, str) or not value for value in readable):
            self.error(index, "readable_by must be a list of non-empty access principals")
            return
        required_dependencies = meta.get("required_dependency_ids", [])
        if role == "input":
            logical = self.occurrences.get(oid)
            expected_dependencies = logical.required_inputs.get(object_id) if logical else None
            if expected_dependencies is None:
                self.error(index, "input allocation is not declared by the occurrence ready event")
                return
            if not isinstance(required_dependencies, list) or tuple(required_dependencies) != expected_dependencies:
                self.error(index, "input allocation dependency binding differs from ready declaration")
                return
        elif required_dependencies not in ([], None):
            self.error(index, "temporary object cannot claim consumer input dependencies")
            return
        state = PhysicalObjectState(object_id, nbytes, layer, oid, role, owner,
                                    readable_by=set(readable),
                                    required_dependency_ids=tuple(required_dependencies or ()))
        self.used_object_ids.add(object_id)
        self.objects[object_id] = state
        self.totals[layer] += nbytes
        self._snapshot_live(event, index, required=True)

    def _process_transfer(self, event: dict[str, Any], index: int) -> None:
        oid, obj_id, meta = event.get("occurrence_id"), event.get("object_id"), event["metadata"]
        obj = self.objects.get(obj_id)
        if obj is None or obj.status == "RELEASED":
            self.error(index, "ownership transfer requires a live physical object")
            return
        if obj.occurrence_id != oid:
            self.error(index, "ownership transfer occurrence differs from allocation")
        if event.get("bytes") != obj.nbytes or event.get("device") != obj.layer:
            self.error(index, "ownership-only transfer changed immutable bytes or resource layer")
        if meta.get("object_role") != obj.role:
            self.error(index, "ownership transfer changed immutable object role")
        if "required_dependency_ids" in meta:
            reported_dependencies = meta.get("required_dependency_ids")
            if (not isinstance(reported_dependencies, list) or
                    tuple(reported_dependencies) != obj.required_dependency_ids):
                self.error(index, "ownership transfer changed immutable input dependency binding")
        if meta.get("old_owner") != obj.owner:
            self.error(index, "ownership transfer does not match live owner")
        new_owner = meta.get("new_owner")
        if not isinstance(new_owner, str) or not new_owner:
            self.error(index, "ownership transfer requires a non-empty new_owner")
            return
        if meta.get("owner") != new_owner:
            self.error(index, "reported transfer owner differs from new_owner")
        if meta.get("readable_by") is not None:
            readable = meta["readable_by"]
            if (not isinstance(readable, list) or
                    any(not isinstance(value, str) or not value for value in readable) or
                    set(readable) != obj.readable_by):
                self.error(index, "ownership transfer changed immutable readable_by access")
        logical = self.occurrences.get(oid)
        if logical is not None and obj_id in logical.active_input_objects:
            if new_owner != "consumer" and "consumer" not in obj.readable_by:
                self.error(index, "ownership transfer revoked an active consumer input access lease")
        obj.owner = new_owner
        self._snapshot_live(event, index, required=True)
        self._snapshot_credit(event, index, item_required=True)

    def _process_release(self, event: dict[str, Any], index: int, oid: str | None) -> None:
        object_id, meta = event.get("object_id"), event["metadata"]
        obj = self.objects.get(object_id)
        if obj is None or obj.status == "RELEASED":
            self.error(index, f"release for unknown/already released object {object_id}")
            return
        if oid != obj.occurrence_id:
            self.error(index, "release occurrence differs from allocation")
        if event.get("bytes") != obj.nbytes:
            self.error(index, "release bytes mismatch with allocation")
        if event.get("device") != obj.layer:
            self.error(index, "release layer mismatch with allocation")
        if meta.get("owner") != obj.owner:
            self.error(index, "release owner differs from current owner")
        if obj.role == "input" and obj.occurrence_id in self.occurrences:
            logical = self.occurrences[obj.occurrence_id]
            if logical.phase != "COMPLETED" or logical.consumer_end is None:
                self.error(index, "input object released before its consumer completed")
        obj.status = "RELEASED"
        self.totals[obj.layer] -= obj.nbytes
        if self.totals[obj.layer] < 0:
            self.error(index, "resource live-byte total became negative")
        self._snapshot_live(event, index, required=True)

    def _process_queue(self, event: dict[str, Any], index: int, oid: str | None) -> None:
        typ, obj_id, nbytes, meta = event["event_type"], event.get("object_id"), event.get("bytes"), event["metadata"]
        if typ == "queue_admission_request":
            if oid is None or not isinstance(obj_id, str) or not isinstance(nbytes, int) or isinstance(nbytes, bool) or nbytes <= 0:
                self.error(index, "queue request requires occurrence, object ID, and positive bytes")
            elif obj_id in self.admission_requests:
                self.error(index, "duplicate queue admission request")
            else:
                self.admission_requests[obj_id] = event
            self._snapshot_credit(event, index, item_required=True)
        elif typ == "queue_block_start":
            attempted = self.credit_total + (nbytes if isinstance(nbytes, int) else 0)
            if obj_id not in self.admission_requests or attempted <= self.capacity or meta.get("attempted_credit_bytes") != attempted:
                self.error(index, "queue block lacks a valid over-capacity admission request")
            self._snapshot_credit(event, index, item_required=True)
        elif typ == "queue_block_end":
            self._snapshot_credit(event, index, item_required=True)
        elif typ == "queue_admission":
            obj = self.objects.get(obj_id)
            request = self.admission_requests.pop(obj_id, None)
            if request is None or obj is None or obj.status != "ALLOCATED" or obj.role != "input":
                self.error(index, "admission requires request and unique live input allocation")
            else:
                if obj.occurrence_id != oid or obj.nbytes != nbytes or request.get("bytes") != nbytes:
                    self.error(index, "admission identity or bytes differ from request/allocation")
                if obj.owner != "queue":
                    self.error(index, "input admission requires queue ownership")
            if oid not in self.occurrences or self.occurrences[oid].ready_ns is None:
                self.error(index, "input admitted before its logical occurrence was ready")
            elif obj_id not in self.occurrences[oid].required_inputs:
                self.error(index, "queue admission is not a declared required consumer input")
            if not isinstance(nbytes, int) or nbytes <= 0 or self.credit_total + nbytes > self.capacity:
                self.error(index, "queue byte-credit capacity exceeded")
            elif isinstance(obj_id, str) and obj_id not in self.credit:
                self.credit[obj_id] = nbytes
                self.credit_total += nbytes
                self.queue_items[obj_id] = nbytes
                self.admitted_event_by_object[obj_id] = event
                if oid in self.occurrences:
                    state = self.occurrences[oid]
                    state.admitted_objects.add(obj_id)
                    self._refresh_input_phase(state)
            self._snapshot_credit(event, index)
        elif typ == "queue_dequeue":
            obj = self.objects.get(obj_id)
            if obj is None or obj.status != "ALLOCATED" or obj_id not in self.queue_items:
                self.error(index, "dequeue requires an admitted live queued object")
            elif obj.occurrence_id != oid or self.queue_items[obj_id] != nbytes:
                self.error(index, "dequeue occurrence or bytes differ from admitted object")
            elif obj.owner != "queue":
                self.error(index, "dequeue requires current queue ownership")
            else:
                del self.queue_items[obj_id]
                if oid in self.occurrences:
                    state = self.occurrences[oid]
                    state.dequeued_objects.add(obj_id)
                    self._refresh_input_phase(state)
            self._snapshot_credit(event, index)
        elif typ == "queue_credit_release":
            obj = self.objects.get(obj_id)
            if obj_id not in self.credit or obj is None or obj.status != "RELEASED":
                self.error(index, "credit returned before terminal release or for unknown/not admitted object")
            elif self.credit[obj_id] != nbytes:
                self.error(index, "credit return bytes mismatch with original admitted bytes")
            else:
                self.credit_total -= self.credit.pop(obj_id)
                if oid in self.occurrences:
                    self.occurrences[oid].dequeued_objects.discard(obj_id)
            self._snapshot_credit(event, index)

    @staticmethod
    def _refresh_input_phase(state: LogicalOccurrenceState) -> None:
        required = set(state.required_inputs)
        if required and required.issubset(state.admitted_objects) and state.phase in {"READY", "ADMITTED"}:
            state.advance("ADMITTED")
        if required and required.issubset(state.dequeued_objects) and state.phase in {"ADMITTED", "DEQUEUED"}:
            state.advance("DEQUEUED")

    def _process_consumer_start(self, event: dict[str, Any], index: int, oid: str | None) -> None:
        if oid not in self.occurrences:
            return
        from cspp.manifest import keyed_u64
        state = self.occurrences[oid]
        meta = event["metadata"]
        ready = self.ready_events.get(oid)
        if state.phase != "DEQUEUED" or ready is None:
            self.error(index, "consumer start requires ready occurrence with admitted/dequeued input")
        if ("ready_time_ns" in event and "ready_time_ns" in meta and
                event.get("ready_time_ns") != meta.get("ready_time_ns")):
            self.error(index, "top-level and metadata ready_time_ns disagree")
        reported_ready = event.get("ready_time_ns", meta.get("ready_time_ns"))
        if ready is None or reported_ready != ready.get("ts_ns"):
            self.error(index, "consumer ready_time_ns differs from reconstructed ready event")
        ids = meta.get("input_object_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(value, str) or not value for value in ids):
            self.error(index, "consumer execution requires non-empty input_object_ids")
            return
        if len(ids) != len(set(ids)):
            self.error(index, "consumer input_object_ids must be unique")
        if set(ids) != set(state.required_inputs):
            self.error(index, "consumer input set differs from frozen ready required_inputs")
        primary = event.get("object_id")
        if primary is not None and primary not in ids:
            self.error(index, "consumer primary object must belong to input_object_ids")
        for obj_id in ids:
            obj = self.objects.get(obj_id)
            if obj is None or obj.status != "ALLOCATED":
                self.error(index, f"consumer input object is absent or released: {obj_id}")
                continue
            if obj.occurrence_id != oid or obj.role != "input":
                self.error(index, f"consumer input object has foreign occurrence or non-input role: {obj_id}")
            if obj_id not in state.admitted_objects or obj_id not in state.dequeued_objects:
                self.error(index, f"consumer input object was not admitted and dequeued: {obj_id}")
            if "consumer" not in obj.readable_by and obj.owner != "consumer":
                self.error(index, f"consumer lacks declared access to object {obj_id}")
            expected_dependencies = state.required_inputs.get(obj_id)
            if expected_dependencies is None or expected_dependencies != obj.required_dependency_ids:
                self.error(index, f"consumer input dependency binding differs from ready declaration: {obj_id}")
            elif not set(expected_dependencies).issubset(state.dependencies):
                self.error(index, f"consumer input has unsatisfied dependency: {obj_id}")
            state.active_input_objects.add(obj_id)
        if self.context is not None:
            record = state.record
            if meta.get("frame_ids") != record.get("frame_ids") or meta.get("frame_selection_spec") != record.get("frame_selection_spec"):
                self.error(index, "execution frame-selection identity differs from frozen manifest")
            try:
                purpose = meta.get("draw_purpose")
                if not isinstance(purpose, str) or not purpose:
                    raise ValueError("draw_purpose is required")
                actual = keyed_u64(self.context["seed"], record, purpose)
                if purpose != "augmentation" or meta.get("augmentation_draw_u64") != actual:
                    self.error(index, "actual RNG draw differs from frozen occurrence and seed")
            except (KeyError, TypeError, ValueError) as exc:
                self.error(index, f"cannot verify actual augmentation draw: {exc}")
        state.advance("EXECUTING")

    def _validate_wait(self, interval: IntervalState, admissions_by_id: dict[str, list[dict[str, Any]]]) -> None:
        start, end = interval.start, interval.end
        oid = start.get("occurrence_id")
        availability_id = end.get("metadata", {}).get("availability_event_id")
        availability = self.event_by_id.get(availability_id)
        index = self.event_indices.get(end.get("event_id"), "?")
        if availability is None or availability.get("event_type") != "queue_admission":
            self.error(index, "exposed input wait must end at a queue admission event")
            return
        logical = self.occurrences.get(oid)
        valid_admissions = admissions_by_id.get(oid, [])
        if (availability.get("occurrence_id") != oid or availability not in valid_admissions or
                logical is None or availability.get("object_id") not in logical.required_inputs):
            self.error(index, "wait availability must be a required input admission for the same occurrence")
        if not isinstance(availability.get("ts_ns"), int) or availability["ts_ns"] < start.get("ts_ns", 0):
            self.error(index, "wait availability precedes wait start")
        if availability["ts_ns"] > end.get("ts_ns", -1):
            self.error(index, "wait ends before legal input availability")
        availability_index = self.event_indices.get(availability.get("event_id"), len(self.event_indices))
        end_index = self.event_indices.get(end.get("event_id"), -1)
        if availability_index >= end_index:
            self.error(index, "wait availability must precede wait_end in event-stream order")
        if start.get("reason") != "input_unavailable" or start.get("metadata", {}).get("critical_dependency") != "input_admission":
            self.error(index, "wait critical dependency is not the declared input_admission")
        for compute_start, compute_end, _ in self.timeline.compute:
            if max(start["ts_ns"], compute_start) < min(end["ts_ns"], compute_end):
                self.error(index, "exposed wait overlaps consumer compute interval")
        if logical is None:
            self.error(index, "wait occurrence is not in frozen workload")
        else:
            position = self.order.index(oid)
            if any(self.occurrences[prior].phase != "COMPLETED" for prior in self.order[:position]):
                self.error(index, "consumer had earlier legal frozen-order work before input wait")
            phase_at_wait = self.wait_phase_at_start.get(interval.interval_id, "PENDING")
            if phase_at_wait in {"ADMITTED", "DEQUEUED", "EXECUTING", "COMPLETED"}:
                self.error(index, "wait started although required input was already available")

    def validate(self, events: list[dict[str, Any]]) -> list[str]:
        if not isinstance(events, list) or not events:
            return self.errors + ["full-run trace must contain events"]
        if not isinstance(self.capacity, int) or isinstance(self.capacity, bool) or self.capacity <= 0:
            return self.errors + ["capacity_bytes must be a positive integer"]

        admissions_by_id: dict[str, list[dict[str, Any]]] = {}
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                self.error(index, "event must be an object")
                continue
            event_type = event.get("event_type")
            event_id = event.get("event_id")
            timestamp = event.get("ts_ns")
            if not isinstance(event_type, str) or event_type not in SUPPORTED_EVENTS:
                self.error(index, f"unsupported event_type: {event_type}")
                continue
            for key in ("event_id", "run_id", "ts_ns", "input_plan_id", "training_plan_id", "run_context_hash", "manifest_hash", "metadata"):
                if key not in event:
                    self.error(index, f"missing required field {key}")
            if not isinstance(event_id, str) or not event_id or event_id in self.event_ids:
                self.error(index, "event_id must be unique and non-empty")
            else:
                self.event_ids.add(event_id)
                self.event_by_id[event_id] = event
                self.event_indices[event_id] = index
            if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0 or timestamp < self.last_ts:
                self.error(index, "monotonic timestamp ordering violation")
            else:
                self.last_ts = timestamp
            for duration in (event.get("duration_ns"), event.get("metadata", {}).get("observed_duration_ns")
                             if isinstance(event.get("metadata"), dict) else None,
                             event.get("metadata", {}).get("duration_ns") if isinstance(event.get("metadata"), dict) else None):
                if duration is not None and (not isinstance(duration, int) or isinstance(duration, bool) or duration < 0):
                    self.error(index, "negative or invalid duration_ns")
            if not isinstance(event.get("metadata"), dict):
                self.error(index, "metadata must be an object")
                event["metadata"] = {}
            for layer in LAYERS:
                value = event.get(f"{layer}_live_bytes")
                if isinstance(value, int) and not isinstance(value, bool) and value < 0:
                    self.error(index, f"negative live bytes reported for {layer}")
            if not isinstance(event.get("run_id"), str) or not event.get("run_id"):
                self.error(index, "run_id must be non-empty")
            self._context(event, index)
            is_global = event_type == "pipeline_end"
            oid = self._identity(event, index, required=not is_global)
            if event_type != "pipeline_end" and oid is None:
                # Continue replay to report independent violations, but it can never validate.
                pass
            if event_type == "pipeline_end":
                self.pipeline_ends.append(event)
                metadata = event["metadata"]
                if metadata.get("reported_live_bytes") != self.totals:
                    self.error(index, "pipeline-end reported live bytes differ from reconstructed totals")
                if metadata.get("reported_credit_bytes") != self.credit_total:
                    self.error(index, "pipeline-end reported credits differ from reconstructed total")
                if metadata.get("reported_item_bytes") != sum(self.queue_items.values()):
                    self.error(index, "pipeline-end reported items differ from reconstructed queue")
                continue

            if event_type in PAIRS:
                self._interval_start(event, index, oid)
            elif event_type in END_TO_START:
                self._interval_end(event, index, oid)

            if event_type == "dependency_complete":
                self._process_dependency(event, index, oid)
            elif event_type == "ready":
                self._process_ready(event, index, oid)
            elif event_type == "allocation":
                self._process_allocation(event, index, oid)
            elif event_type == "ownership_transfer":
                self._process_transfer(event, index)
            elif event_type == "release":
                self._process_release(event, index, oid)
            elif event_type in {"queue_admission_request", "queue_block_start", "queue_block_end", "queue_admission", "queue_dequeue", "queue_credit_release"}:
                self._process_queue(event, index, oid)
            elif event_type == "consumer_start":
                self._process_consumer_start(event, index, oid)
            elif event_type == "consumer_wait_end":
                pass
            if event_type == "queue_admission" and oid is not None:
                admissions_by_id.setdefault(oid, []).append(event)
            if self.context is not None:
                resources = self.context.get("resources")
                if isinstance(resources, dict):
                    for layer, budget_field in (("host", "host_memory_budget_bytes"),
                                                ("pinned", "pinned_memory_budget_bytes"),
                                                ("gpu", "gpu_memory_budget_bytes")):
                        budget = resources.get(budget_field)
                        if isinstance(budget, int) and not isinstance(budget, bool) and self.totals[layer] > budget:
                            self.error(index, f"reconstructed {layer} live bytes exceed run resource budget")
            self._snapshot_live(event, index)

        if self.open_intervals:
            self.errors.append(f"unclosed intervals: {sorted(self.open_intervals)}")
        if len(self.pipeline_ends) != 1 or (self.pipeline_ends and events[-1] is not self.pipeline_ends[0]):
            self.errors.append("full run requires exactly one terminal pipeline_end event")
        for interval in self.closed_intervals:
            if interval.start_type == "consumer_wait_start":
                self._validate_wait(interval, admissions_by_id)
        for i, (start, end, oid, _, _) in enumerate(self.timeline.waits):
            if any(max(start, other_start) < min(end, other_end)
                   for other_start, other_end, _, _, _ in self.timeline.waits[i + 1:]):
                self.errors.append(f"consumer wait intervals overlap: {oid}")
        for i, (start, end, oid) in enumerate(self.timeline.compute):
            if any(max(start, other_start) < min(end, other_end)
                   for other_start, other_end, other_oid in self.timeline.compute[i + 1:]):
                self.errors.append(f"consumer compute intervals overlap: {oid}")
        if self.timeline.compute:
            order = [oid for _, _, oid in self.timeline.compute]
            if order != self.order:
                self.errors.append("consumer completion intervals do not follow frozen occurrence order")
        for oid, state in self.occurrences.items():
            self.errors.extend(f"occurrence {oid}: {message}" for message in state.violations)
            if state.phase != "COMPLETED" or state.consumer_start is None or state.consumer_end is None:
                self.errors.append(f"required logical occurrence lacks exactly one consumer execution: {oid}")
            if not state.admitted_objects:
                self.errors.append(f"frozen occurrence lacks admitted input objects: {oid}")
        leaked = [oid for oid, obj in self.objects.items() if obj.status != "RELEASED"]
        if leaked:
            self.errors.append(f"leaked live objects not terminally released: {sorted(leaked)}")
        if any(value < 0 for value in self.totals.values()):
            self.errors.append(f"negative reconstructed live bytes: {self.totals}")
        if self.totals != self.expected_live:
            self.errors.append(f"live-byte reconciliation mismatch: {self.totals} != {self.expected_live}")
        if self.admission_requests:
            self.errors.append(f"unresolved queue admission requests: {sorted(self.admission_requests)}")
        if self.queue_items or self.credit or self.credit_total != 0:
            self.errors.append(f"non-terminal queue state: items={self.queue_items}, credits={self.credit}")
        return list(dict.fromkeys(self.errors))


def validate_full_run(events: list[dict[str, Any]], capacity_bytes: int,
                      expected_live_bytes: dict[str, int] | None,
                      expected_occurrences: dict[str, dict[str, Any]] | None,
                      expected_run_id: str | None,
                      expected_run_context: dict[str, Any] | None) -> list[str]:
    """Validate a full run. Frozen workload and run context are mandatory."""
    return TraceStateMachine(capacity_bytes, expected_live_bytes, expected_occurrences,
                             expected_run_id, expected_run_context).validate(events)
