"""C1 oracle: hand-authored events, independent of runtime/state-machine helpers."""

from copy import deepcopy
import hashlib
import hmac
import json

import pytest

from cspp.legality import build_execution_context
from cspp.tracing.trace import validate_trace


def hand_built_c1(config, records, input_count=1, shared_dependency=False):
    record = records[0]
    context = build_execution_context([record], config, "c1-independent")
    expected = {record["occurrence_id"]: record}
    inputs = [f"c1-input-{i}" for i in range(input_count)]
    deps = [f"c1-dependency-{i}" for i in range(1 if shared_dependency else input_count)]
    bindings = {obj: [deps[0] if shared_dependency else deps[i]] for i, obj in enumerate(inputs)}
    events = []
    live = 0
    credits = 0
    queued = 0

    def emit(kind, *, metadata=None, **fields):
        index = len(events)
        event = {
            "event_id": f"c1-event-{index}", "event_type": kind, "ts_ns": 100 + index * 10,
            "run_id": context["run_id"], "run_context_hash": context["run_context_hash"],
            "manifest_hash": context["manifest_hash"], "input_plan_id": context["input_plan_id"],
            "training_plan_id": context["training_plan_id"],
            "plan_d": context["input_plan_id"], "plan_t": context["training_plan_id"],
            "sample_id": record["sample_id"], "occurrence_id": record["occurrence_id"],
            "step_id": record["step_id"], "microbatch_id": record["microbatch_id"],
            "worker": "c1-worker", "object_id": None, "operator": None, "device": None,
            "bytes": None, "duration_ns": None, "ready_time_ns": None,
            "reported_live_bytes": {"host": live, "pinned": 0, "gpu": 0},
            "host_live_bytes": live, "pinned_live_bytes": 0, "gpu_live_bytes": 0,
            "metadata": dict(metadata or {}),
        }
        event.update(fields)
        events.append(event)
        return event

    def queue_snapshot():
        return {"reported_credit_bytes": credits, "current_queue_bytes": credits,
                "current_item_bytes": queued, "capacity_basis": "unreleased_object_credits",
                "capacity_bytes": 100}

    # These buffers consume real accounting credit for live host bytes before
    # producer completion; queue admission credit is independently still zero.
    for obj in inputs:
        live += 10
        emit("allocation", object_id=obj, device="host", bytes=10,
             metadata={"owner": "producer", "object_role": "input",
                       "required_dependency_ids": bindings[obj]})
    for i, dep in enumerate(deps):
        root_obj = inputs[i]
        meta = {"dependency_id": dep, "dependency_kind": "c1-output"}
        begin = emit("operator_begin", object_id=root_obj, operator="independent_producer", metadata=meta)
        begin["interval_id"] = f"c1-operator-{i}"
        end = emit("operator_end", object_id=root_obj, operator="independent_producer",
                   interval_id=begin["interval_id"], duration_ns=10, metadata=meta)
        emit("dependency_complete", object_id=root_obj,
             metadata={**meta, "completion_event_id": end["event_id"]})
    ready = emit("ready", object_id=inputs[0], metadata={
        "required_dependency_ids": deps,
        "required_inputs": [{"object_id": obj, "dependency_ids": bindings[obj]} for obj in inputs],
    })
    ready["ready_time_ns"] = ready["ts_ns"]
    for obj in inputs:
        emit("ownership_transfer", object_id=obj, device="host", bytes=10,
             metadata={"old_owner": "producer", "new_owner": "queue", "owner": "queue",
                       "object_role": "input", "required_dependency_ids": bindings[obj], **queue_snapshot()})
        emit("queue_admission_request", object_id=obj, bytes=10, metadata=queue_snapshot())
        credits += 10
        queued += 10
        emit("queue_admission", object_id=obj, bytes=10, metadata=queue_snapshot())
        queued -= 10
        emit("queue_dequeue", object_id=obj, bytes=10, metadata=queue_snapshot())
        emit("ownership_transfer", object_id=obj, device="host", bytes=10,
             metadata={"old_owner": "queue", "new_owner": "consumer", "owner": "consumer",
                       "object_role": "input", "required_dependency_ids": bindings[obj], **queue_snapshot()})
    # Draw evidence uses an independent structured encoding and HMAC oracle.
    message = json.dumps([record["rng_scheme_version"], context["seed"], record["workload_version"],
                          record["sample_id"], record["occurrence_id"], record["augmentation_key"],
                          "augmentation"], separators=(",", ":"), ensure_ascii=False).encode()
    draw = int.from_bytes(hmac.new(b"cspp-keyed-rng-v2", message, hashlib.sha256).digest()[:8], "big")
    begin = emit("consumer_start", object_id=inputs[0], ready_time_ns=ready["ts_ns"],
                 interval_id="c1-consumer", metadata={"input_object_ids": inputs,
                 "ready_time_ns": ready["ts_ns"], "frame_ids": record["frame_ids"],
                 "frame_selection_spec": record["frame_selection_spec"], "draw_purpose": "augmentation",
                 "augmentation_draw_u64": draw})
    emit("consumer_end", object_id=inputs[0], interval_id=begin["interval_id"], duration_ns=10,
         metadata={"input_object_ids": inputs})
    for obj in inputs:
        live -= 10
        emit("release", object_id=obj, bytes=10, device="host", metadata={"owner": "consumer"})
        credits -= 10
        emit("queue_credit_release", object_id=obj, bytes=10, metadata=queue_snapshot())
    emit("pipeline_end", sample_id=None, occurrence_id=None, step_id=None, microbatch_id=None,
         worker=None, metadata={"reported_live_bytes": {"host": 0, "pinned": 0, "gpu": 0},
                                "reported_credit_bytes": 0, "reported_item_bytes": 0})
    return events, expected, context


def errors(events, expected, context):
    return validate_trace(events, 100, expected_occurrences=expected, expected_run_context=context)


def test_c1_exact_independent_allocation_before_ready_replay(config, records):
    events, expected, context = hand_built_c1(config, records)
    assert [event["event_type"] for event in events] == [
        "allocation", "operator_begin", "operator_end", "dependency_complete", "ready",
        "ownership_transfer", "queue_admission_request", "queue_admission", "queue_dequeue",
        "ownership_transfer", "consumer_start", "consumer_end", "release", "queue_credit_release", "pipeline_end",
    ]
    assert errors(events, expected, context) == []
    assert events[0]["ts_ns"] < events[4]["ready_time_ns"]
    assert all(event["host_live_bytes"] == 10 for event in events[:12])
    assert events[12]["host_live_bytes"] == 0


@pytest.mark.parametrize("input_count,shared_dependency", [(1, False), (2, False), (2, True)],
                         ids=["producer-owned", "two-inputs-two-dependencies", "one-dependency-two-inputs"])
def test_c1_positive_controls(config, records, input_count, shared_dependency):
    events, expected, context = hand_built_c1(config, records, input_count, shared_dependency)
    assert errors(events, expected, context) == []
    ready = next(event for event in events if event["event_type"] == "ready")
    assert ready["host_live_bytes"] == input_count * 10
    assert all(event["ts_ns"] < ready["ts_ns"] for event in events if event["event_type"] == "allocation")


@pytest.mark.parametrize("case,error_text", [
    ("never-allocated", "ready input must already be allocated and alive"),
    ("binding-mismatch", "ready input dependency binding differs from allocation"),
    ("unsatisfied", "ready has incomplete declared dependencies"),
    ("foreign-occurrence", "ready input has foreign occurrence"),
    ("late-allocation", "ready input must already be allocated and alive"),
    ("released-before-ready", "ready input must already be allocated and alive"),
])
def test_c1_negative_controls(config, records, case, error_text):
    events, expected, context = hand_built_c1(config, records)
    events = deepcopy(events)
    alloc = events[0]
    ready = next(event for event in events if event["event_type"] == "ready")
    if case == "never-allocated":
        events.remove(alloc)
    elif case == "binding-mismatch":
        alloc["metadata"]["required_dependency_ids"] = ["different-dependency"]
    elif case == "unsatisfied":
        events = [event for event in events if event["event_type"] != "dependency_complete"]
    elif case == "foreign-occurrence":
        # This is a separately frozen, valid identity, rather than just an
        # invalid ID. Ready must still reject its foreign physical buffer.
        foreign = records[1]
        context = build_execution_context(records[:2], config, "c1-independent")
        expected = {record["occurrence_id"]: record for record in records[:2]}
        for event in events:
            event["run_context_hash"] = context["run_context_hash"]
            event["manifest_hash"] = context["manifest_hash"]
        for key in ("sample_id", "occurrence_id", "step_id", "microbatch_id"):
            alloc[key] = foreign[key]
    elif case == "late-allocation":
        events.remove(alloc)
        alloc["ts_ns"] = ready["ts_ns"] + 1
        events.insert(events.index(ready) + 1, alloc)
    else:
        release = deepcopy(next(event for event in events if event["event_type"] == "release"))
        release.update(event_id="c1-early-release", ts_ns=ready["ts_ns"] - 1)
        release["metadata"]["owner"] = "producer"
        events.insert(events.index(ready), release)
    assert any(error_text in error for error in errors(events, expected, context))
