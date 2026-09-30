"""Independent event dictionaries for full-run causal/state-machine attacks.

This module does not use run_synthetic_pipeline, ByteCreditQueue, or
LiveByteLedger to create either its positive traces or its mutations.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from copy import deepcopy

import pytest

from cspp.legality import build_execution_context
from cspp.tracing.trace import validate_trace


def _draw(seed, record, purpose="augmentation"):
    value = [record["rng_scheme_version"], seed, record["workload_version"], record["sample_id"],
             record["occurrence_id"], record["augmentation_key"], purpose]
    message = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hmac.new(b"cspp-keyed-rng-v2", message, hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big")


def _hand_trace(config, records, *, input_counts=None, dependencies=None,
                producer_order=None, operator="independent_fixture_decode", temporary=False):
    run_id = "hand-built-state-machine"
    context = build_execution_context(records, config, run_id)
    expected = {record["occurrence_id"]: record for record in records}
    input_counts = input_counts or {}
    dependencies = dependencies or {}
    out = []
    clock = 100
    serial = 0
    live = {"host": 0, "pinned": 0, "gpu": 0}
    credit = 0
    queue_items = 0
    object_ids = {oid: [f"manual-{oid}-input-{i}" for i in range(input_counts.get(oid, 1))]
                  for oid in expected}

    def emit(kind, record, *, ts=None, metadata=None, **fields):
        nonlocal clock, serial
        serial += 1
        event = {
            "event_id": f"manual-event-{serial}", "run_id": run_id,
            "ts_ns": clock if ts is None else ts, "event_type": kind,
            "input_plan_id": context["input_plan_id"], "training_plan_id": context["training_plan_id"],
            "plan_d": context["input_plan_id"], "plan_t": context["training_plan_id"],
            "run_context_hash": context["run_context_hash"], "manifest_hash": context["manifest_hash"],
            "sample_id": record["sample_id"], "occurrence_id": record["occurrence_id"],
            "step_id": record["step_id"], "microbatch_id": record["microbatch_id"],
            "worker": "manual-worker", "object_id": None, "operator": None, "device": None,
            "bytes": None, "duration_ns": None, "ready_time_ns": None,
            "reported_live_bytes": None, "host_live_bytes": None,
            "pinned_live_bytes": None, "gpu_live_bytes": None,
            "metadata": dict(metadata or {}),
        }
        event.update(fields)
        out.append(event)
        if ts is None:
            clock += 10
        return event

    def live_snapshot(event):
        event["reported_live_bytes"] = dict(live)
        for layer, value in live.items():
            event[f"{layer}_live_bytes"] = value

    def queue_snapshot(event, *, include_items=True):
        event["metadata"].update({"reported_credit_bytes": credit, "current_queue_bytes": credit,
                                  "capacity_basis": "unreleased_object_credits", "capacity_bytes": 100})
        if include_items:
            event["metadata"]["current_item_bytes"] = queue_items

    # Preprocessing is intentionally emitted in a caller-selected physical order.
    order = producer_order or list(expected)
    dep_ids = {}
    for oid in order:
        record = expected[oid]
        roots = []
        count = dependencies.get(oid, 1)
        for d in range(count):
            object_id = object_ids[oid][0]
            dependency_id = f"dep-{oid}-{d}"
            dependency_kind = f"input-{d}"
            dep_metadata = {"dependency_id": dependency_id, "dependency_kind": dependency_kind}
            begin = emit("operator_begin", record, worker="producer-manual", object_id=object_id,
                         operator=f"{operator}-{d}", metadata=dep_metadata)
            begin["interval_id"] = f"interval-{begin['event_id']}"
            end = emit("operator_end", record, worker="producer-manual", object_id=object_id,
                       operator=f"{operator}-{d}", interval_id=begin["interval_id"], duration_ns=10,
                       metadata=dep_metadata)
            end["ts_ns"] = begin["ts_ns"] + 10
            roots.append(end)
        dep_ids[oid] = []
        for d, root in enumerate(roots):
            dep = f"dep-{oid}-{d}"
            event = emit("dependency_complete", record, object_id=object_ids[oid][0],
                         metadata={"dependency_id": dep, "dependency_kind": f"input-{d}",
                                   "completion_event_id": root["event_id"]})
            dep_ids[oid].append((dep, event))
        ready = emit("ready", record, object_id=object_ids[oid][0],
                     metadata={"required_dependency_ids": [dep for dep, _ in dep_ids[oid]],
                               "ready_definition": "all consumer-required dependencies completed"})
        ready["ready_time_ns"] = ready["ts_ns"]

    # Physical queue admission is independently accounted per object identity.
    for oid in order:
        record = expected[oid]
        for object_id in object_ids[oid]:
            nbytes = 10
            req = emit("queue_admission_request", record, object_id=object_id, bytes=nbytes)
            queue_snapshot(req)
            alloc = emit("allocation", record, object_id=object_id, bytes=nbytes, device="host",
                         metadata={"owner": "queue", "object_role": "input"})
            live["host"] += nbytes
            live_snapshot(alloc)
            admission = emit("queue_admission", record, object_id=object_id, bytes=nbytes)
            credit += nbytes; queue_items += nbytes
            queue_snapshot(admission)
    for oid in order:
        record = expected[oid]
        for object_id in object_ids[oid]:
            dequeue = emit("queue_dequeue", record, object_id=object_id, bytes=10)
            queue_items -= 10
            queue_snapshot(dequeue)
            transfer = emit("ownership_transfer", record, object_id=object_id, bytes=10, device="host",
                            metadata={"old_owner": "queue", "new_owner": "consumer", "owner": "consumer",
                                      "object_role": "input"})
            live_snapshot(transfer); queue_snapshot(transfer)

    # Consumer/update execution always follows the frozen manifest order.
    for record in sorted(records, key=lambda r: (r["step_id"], r["sample_order_baseline"], r["occurrence_id"])):
        oid = record["occurrence_id"]
        inputs = object_ids[oid]
        start = emit("consumer_start", record, worker="consumer-manual", object_id=inputs[0],
                     ready_time_ns=next(e["ts_ns"] for e in out if e["event_type"] == "ready" and e["occurrence_id"] == oid),
                     metadata={"ready_time_ns": next(e["ts_ns"] for e in out if e["event_type"] == "ready" and e["occurrence_id"] == oid),
                               "input_object_ids": inputs, "logical_execution": True,
                               "frame_ids": record["frame_ids"], "frame_selection_spec": record["frame_selection_spec"],
                               "draw_purpose": "augmentation", "augmentation_draw_u64": _draw(context["seed"], record)})
        start["interval_id"] = f"interval-{start['event_id']}"
        if temporary:
            obj = f"temp-{oid}"
            alloc = emit("allocation", record, object_id=obj, bytes=3, device="host",
                         metadata={"owner": "consumer", "object_role": "temporary"})
            live["host"] += 3; live_snapshot(alloc)
            rel = emit("release", record, object_id=obj, bytes=3, device="host", metadata={"owner": "consumer"})
            live["host"] -= 3; live_snapshot(rel)
        end = emit("consumer_end", record, worker="consumer-manual", object_id=inputs[0],
                   interval_id=start["interval_id"], duration_ns=10,
                   metadata={"input_object_ids": inputs})
        end["duration_ns"] = end["ts_ns"] - start["ts_ns"]
        for object_id in inputs:
            release = emit("release", record, object_id=object_id, bytes=10, device="host",
                           metadata={"owner": "consumer"})
            live["host"] -= 10; live_snapshot(release)
            returned = emit("queue_credit_release", record, object_id=object_id, bytes=10)
            credit -= 10; queue_snapshot(returned)
    terminal = emit("pipeline_end", records[-1], metadata={"reported_live_bytes": dict(live),
                  "reported_credit_bytes": credit, "reported_item_bytes": queue_items})
    terminal["occurrence_id"] = None
    terminal["sample_id"] = None
    terminal["step_id"] = None
    terminal["microbatch_id"] = None
    terminal["worker"] = None
    return out, expected, context


def _errors(events, expected, context):
    return validate_trace(events, 100, expected_occurrences=expected,
                          expected_run_context=context)


def test_hand_built_positive_control_matrix(config, records):
    baseline, expected, context = _hand_trace(config, records)
    assert _errors(baseline, expected, context) == []
    variants = [
        _hand_trace(config, records, input_counts={records[0]["occurrence_id"]: 2}),
        _hand_trace(config, records, input_counts={records[0]["occurrence_id"]: 3}),
        _hand_trace(config, records, input_counts={records[0]["occurrence_id"]: 2}, temporary=True),
        _hand_trace(config, records, dependencies={records[0]["occurrence_id"]: 2}),
        _hand_trace(config, records, producer_order=list(reversed([r["occurrence_id"] for r in records]))),
        _hand_trace(config, records, operator="arbitrary_cpu_transform"),
    ]
    assert all(_errors(events, exp, ctx) == [] for events, exp, ctx in variants)


@pytest.mark.parametrize("attack", ["foreign_occurrence", "duplicate_id", "release_before_end",
                                     "not_dequeued", "wrong_role"])
def test_rf5_each_consumer_input_is_independently_validated(config, records, attack):
    events, expected, context = _hand_trace(config, records, input_counts={records[0]["occurrence_id"]: 2})
    changed = deepcopy(events)
    occurrence = records[0]["occurrence_id"]
    consumer = next(e for e in changed if e["event_type"] == "consumer_start" and e["occurrence_id"] == occurrence)
    ids = consumer["metadata"]["input_object_ids"]
    if attack == "foreign_occurrence":
        ids[1] = next(e["object_id"] for e in changed if e["event_type"] == "allocation" and e["occurrence_id"] == records[1]["occurrence_id"])
    elif attack == "duplicate_id":
        ids[1] = ids[0]
    elif attack == "release_before_end":
        target = ids[1]
        release = next(e for e in changed if e["event_type"] == "release" and e["object_id"] == target)
        end = next(e for e in changed if e["event_type"] == "consumer_end" and e["occurrence_id"] == occurrence)
        release["ts_ns"] = end["ts_ns"]
        changed.remove(release)
        changed.insert(changed.index(end), release)
    elif attack == "not_dequeued":
        target = ids[1]
        changed.remove(next(e for e in changed if e["event_type"] == "queue_dequeue" and e["object_id"] == target))
    else:
        target = ids[1]
        alloc = next(e for e in changed if e["event_type"] == "allocation" and e["object_id"] == target)
        alloc["metadata"]["object_role"] = "temporary"
    assert _errors(changed, expected, context)


@pytest.mark.parametrize("attack", ["self", "forward", "cycle", "missing_root", "foreign_occurrence", "wrong_object", "wrong_dependency"])
def test_rf1_dependencies_need_prior_validated_roots(config, records, attack):
    events, expected, context = _hand_trace(config, records, dependencies={records[0]["occurrence_id"]: 2})
    changed = deepcopy(events)
    deps = [e for e in changed if e["event_type"] == "dependency_complete"]
    roots = [e for e in changed if e["event_type"] == "operator_end"]
    if attack == "self":
        deps[0]["metadata"]["completion_event_id"] = deps[0]["event_id"]
    elif attack == "forward":
        deps[0]["metadata"]["completion_event_id"] = next(
            e["event_id"] for e in changed if e["event_type"] == "operator_end" and e["ts_ns"] > deps[0]["ts_ns"])
    elif attack == "cycle":
        deps[0]["metadata"]["completion_event_id"] = deps[1]["event_id"]
        deps[1]["metadata"]["completion_event_id"] = deps[0]["event_id"]
    elif attack == "missing_root":
        changed = [e for e in changed if e["event_type"] not in {"operator_begin", "operator_end"}]
        deps = [e for e in changed if e["event_type"] == "dependency_complete"]
        for event in deps:
            event["metadata"]["completion_event_id"] = event["event_id"]
    elif attack == "foreign_occurrence":
        deps[0]["metadata"]["completion_event_id"] = next(
            e["event_id"] for e in roots if e["occurrence_id"] == records[1]["occurrence_id"])
    elif attack == "wrong_object":
        deps[0]["object_id"] = "unrelated-physical-object"
    else:
        roots[0]["metadata"]["dependency_id"] = "different-dependency"
    assert _errors(changed, expected, context)


@pytest.mark.parametrize("attack", ["cross_type_consumer", "cross_type_operator", "cross_occurrence",
                                     "cross_step", "reuse_closed_id", "duplicate_start", "duplicate_end",
                                     "unmatched_end", "missing_end"])
def test_rf2_interval_pairs_are_typed_unique_and_identity_bound(config, records, attack):
    events, expected, context = _hand_trace(config, records)
    changed = deepcopy(events)
    start = next(e for e in changed if e["event_type"] == "consumer_start")
    end = next(e for e in changed if e["event_type"] == "consumer_end" and e["occurrence_id"] == start["occurrence_id"])
    op_start = next(e for e in changed if e["event_type"] == "operator_begin")
    op_end = next(e for e in changed if e["event_type"] == "operator_end" and e["occurrence_id"] == op_start["occurrence_id"])
    if attack == "cross_type_consumer":
        end["event_type"] = "operator_end"
    elif attack == "cross_type_operator":
        op_end["event_type"] = "consumer_end"
    elif attack == "cross_occurrence":
        end["occurrence_id"] = records[1]["occurrence_id"]
        end["sample_id"] = records[1]["sample_id"]
        end["step_id"] = records[1]["step_id"]
        end["microbatch_id"] = records[1]["microbatch_id"]
    elif attack == "cross_step":
        end["step_id"] += 1
    elif attack == "reuse_closed_id":
        later = next(e for e in changed if e["event_type"] == "operator_begin" and e["event_id"] != op_start["event_id"])
        later_end = next(e for e in changed if e["event_type"] == "operator_end" and e["interval_id"] == later["interval_id"])
        later["interval_id"] = op_start["interval_id"]
        later_end["interval_id"] = op_start["interval_id"]
    elif attack == "duplicate_start":
        duplicate = deepcopy(start); duplicate["event_id"] = "manual-duplicate-start"; duplicate["interval_id"] = "manual-duplicate-start"
        changed.append(duplicate)
    elif attack == "duplicate_end":
        duplicate = deepcopy(end); duplicate["event_id"] = "manual-duplicate-end"
        changed.append(duplicate)
    elif attack == "unmatched_end":
        end["interval_id"] = "missing-interval"
    else:
        changed.remove(end)
    changed.sort(key=lambda e: e["ts_ns"])
    assert _errors(changed, expected, context)


def test_rf3_wait_requires_real_availability_and_reconstructed_ready_time(config, records):
    events, expected, context = _hand_trace(config, records)
    changed = deepcopy(events)
    record = records[0]
    request = next(e for e in changed if e["event_type"] == "queue_admission_request" and e["occurrence_id"] == record["occurrence_id"])
    admission = next(e for e in changed if e["event_type"] == "queue_admission" and e["occurrence_id"] == record["occurrence_id"])
    serial = 500
    start = {**deepcopy(request), "event_id": "manual-exposed-wait-start", "event_type": "consumer_wait_start",
             "interval_id": "manual-exposed-wait-start", "ts_ns": request["ts_ns"] + 1,
             "reason": "input_unavailable", "object_id": None,
             "metadata": {"critical_dependency": "input_admission"}}
    end = {**deepcopy(request), "event_id": "manual-exposed-wait-end", "event_type": "consumer_wait_end",
           "interval_id": start["interval_id"], "ts_ns": admission["ts_ns"] + 1,
           "duration_ns": admission["ts_ns"] + 1 - start["ts_ns"], "reason": "input_unavailable", "object_id": None,
           "metadata": {"critical_dependency": "input_admission", "availability_event_id": admission["event_id"],
                        "duration_ns": admission["ts_ns"] + 1 - start["ts_ns"],
                        "observed_duration_ns": admission["ts_ns"] + 1 - start["ts_ns"]}}
    changed.extend([start, end]); changed.sort(key=lambda e: e["ts_ns"])
    assert _errors(changed, expected, context) == []

    for mutation in ("critical", "future_ready", "past_ready", "wrong_occurrence", "availability_before", "missing_ready"):
        changed = deepcopy(events)
        if mutation in {"future_ready", "past_ready"}:
            consumer = next(e for e in changed if e["event_type"] == "consumer_start")
            value = consumer["ready_time_ns"] + (10**9 if mutation == "future_ready" else -1)
            consumer["ready_time_ns"] = value; consumer["metadata"]["ready_time_ns"] = value
        elif mutation == "missing_ready":
            changed = [e for e in changed if not (e["event_type"] == "ready" and e["occurrence_id"] == record["occurrence_id"])]
        else:
            start2 = deepcopy(start); end2 = deepcopy(end)
            if mutation == "critical":
                start2["metadata"]["critical_dependency"] = "gpu_barrier"
                end2["metadata"]["critical_dependency"] = "gpu_barrier"
            elif mutation == "wrong_occurrence":
                end2["occurrence_id"] = records[1]["occurrence_id"]
                end2["metadata"]["availability_event_id"] = admission["event_id"]
            elif mutation == "availability_before":
                start2["ts_ns"] = admission["ts_ns"] + 5
                end2["ts_ns"] = admission["ts_ns"] + 10
                d = end2["ts_ns"] - start2["ts_ns"]
                end2["duration_ns"] = d; end2["metadata"].update(duration_ns=d, observed_duration_ns=d)
            changed.extend([start2, end2]); changed.sort(key=lambda e: e["ts_ns"])
        assert _errors(changed, expected, context)


def test_rf3_any_consumer_compute_wait_intersection_is_rejected(config, records):
    events, expected, context = _hand_trace(config, records)
    changed = deepcopy(events)
    compute_start = next(e for e in changed if e["event_type"] == "consumer_start")
    compute_end = next(e for e in changed if e["event_type"] == "consumer_end" and e["occurrence_id"] == compute_start["occurrence_id"])
    later_record = records[1]
    admission = next(e for e in changed if e["event_type"] == "queue_admission" and e["occurrence_id"] == later_record["occurrence_id"])
    wait_start = {**deepcopy(compute_start), "event_id": "overlap-wait-start", "interval_id": "overlap-wait-start",
                  "event_type": "consumer_wait_start", "occurrence_id": later_record["occurrence_id"],
                  "sample_id": later_record["sample_id"], "step_id": later_record["step_id"],
                  "microbatch_id": later_record["microbatch_id"], "ts_ns": compute_start["ts_ns"] + 1,
                  "object_id": None, "reason": "input_unavailable", "metadata": {"critical_dependency": "input_admission"}}
    wait_end = {**deepcopy(wait_start), "event_id": "overlap-wait-end", "event_type": "consumer_wait_end",
                "ts_ns": compute_end["ts_ns"] + 1, "interval_id": wait_start["interval_id"],
                "duration_ns": compute_end["ts_ns"] + 1 - wait_start["ts_ns"],
                "metadata": {"critical_dependency": "input_admission", "availability_event_id": admission["event_id"],
                             "duration_ns": compute_end["ts_ns"] + 1 - wait_start["ts_ns"],
                             "observed_duration_ns": compute_end["ts_ns"] + 1 - wait_start["ts_ns"]}}
    changed.extend([wait_start, wait_end]); changed.sort(key=lambda e: e["ts_ns"])
    assert any("overlaps consumer compute" in error for error in _errors(changed, expected, context))


def test_rf2_overlapping_consumer_compute_intervals_are_rejected(config, records):
    events, expected, context = _hand_trace(config, records)
    changed = deepcopy(events)
    starts = [e for e in changed if e["event_type"] == "consumer_start"][:2]
    ends = [next(e for e in changed if e["event_type"] == "consumer_end" and e["occurrence_id"] == start["occurrence_id"])
            for start in starts]
    first_release = next(e for e in changed if e["event_type"] == "release" and e["occurrence_id"] == starts[0]["occurrence_id"])
    first_credit = next(e for e in changed if e["event_type"] == "queue_credit_release" and e["occurrence_id"] == starts[0]["occurrence_id"])
    second_release = next(e for e in changed if e["event_type"] == "release" and e["occurrence_id"] == starts[1]["occurrence_id"])
    second_credit = next(e for e in changed if e["event_type"] == "queue_credit_release" and e["occurrence_id"] == starts[1]["occurrence_id"])
    # Hand-author a causal order with the second compute nested inside the first.
    for event in [*starts, *ends, first_release, first_credit, second_release, second_credit]:
        changed.remove(event)
    execution = [starts[0], starts[1], ends[1], ends[0], first_release, first_credit, second_release, second_credit]
    for offset, event in enumerate(execution):
        event["ts_ns"] = 1000 + offset * 10
        changed.append(event)
    starts[0]["ts_ns"] = 1000
    starts[1]["ts_ns"] = 1010
    ends[1]["ts_ns"] = 1020
    ends[0]["ts_ns"] = 1030
    ends[1]["duration_ns"] = ends[1]["ts_ns"] - starts[1]["ts_ns"]
    ends[1]["metadata"].update(duration_ns=ends[1]["duration_ns"], observed_duration_ns=ends[1]["duration_ns"])
    ends[0]["duration_ns"] = ends[0]["ts_ns"] - starts[0]["ts_ns"]
    ends[0]["metadata"].update(duration_ns=ends[0]["duration_ns"], observed_duration_ns=ends[0]["duration_ns"])
    changed.sort(key=lambda e: e["ts_ns"])
    errors = _errors(changed, expected, context)
    assert any("consumer compute intervals overlap" in error for error in errors)


@pytest.mark.parametrize("mutation", ["wrong_transfer_bytes", "wrong_transfer_layer", "wrong_old_owner",
                                       "wrong_live_snapshot", "wrong_credit_snapshot", "producer_only_access",
                                       "foreign_access", "released_access", "wrong_input_role"])
def test_rf4_ownership_and_consumer_access_fail_closed(config, records, mutation):
    events, expected, context = _hand_trace(config, records)
    changed = deepcopy(events)
    transfer = next(e for e in changed if e["event_type"] == "ownership_transfer")
    consumer = next(e for e in changed if e["event_type"] == "consumer_start")
    if mutation == "wrong_transfer_bytes": transfer["bytes"] += 1
    elif mutation == "wrong_transfer_layer": transfer["device"] = "gpu"
    elif mutation == "wrong_old_owner": transfer["metadata"]["old_owner"] = "foreign-owner"
    elif mutation == "wrong_live_snapshot": transfer["reported_live_bytes"]["host"] = 0
    elif mutation == "wrong_credit_snapshot": transfer["metadata"]["reported_credit_bytes"] = 0
    elif mutation == "producer_only_access":
        transfer["metadata"].update(old_owner="queue", new_owner="producer-only", owner="producer-only")
        obj_id = transfer["object_id"]
        next(e for e in changed if e["event_type"] == "release" and e["object_id"] == obj_id)["metadata"]["owner"] = "producer-only"
    elif mutation == "foreign_access": consumer["metadata"]["input_object_ids"] = ["manual-foreign-object"]
    elif mutation == "released_access":
        consumer["metadata"]["input_object_ids"] = ["already-released-object"]
    elif mutation == "wrong_input_role":
        alloc = next(e for e in changed if e["event_type"] == "allocation")
        alloc["metadata"]["object_role"] = "temporary"
    assert _errors(changed, expected, context)


@pytest.mark.parametrize("mutation", ["no_manifest", "terminal_only", "foreign_run", "foreign_context",
                                       "mixed_plan", "partial_occurrences", "duplicate_execution"])
def test_rf6_full_run_contract_is_mandatory(config, records, mutation):
    events, expected, context = _hand_trace(config, records)
    if mutation == "no_manifest":
        assert validate_trace(events, 100, expected_run_context=context)
        return
    changed = deepcopy(events)
    if mutation == "terminal_only":
        changed = [changed[-1]]
    elif mutation == "foreign_run": changed[0]["run_id"] = "other-run"
    elif mutation == "foreign_context": changed[0]["run_context_hash"] = "0" * 64
    elif mutation == "mixed_plan": changed[0]["training_plan_id"] = "other-plan"
    elif mutation == "partial_occurrences":
        omitted = records[-1]["occurrence_id"]
        changed = [event for event in changed if event.get("occurrence_id") != omitted]
    elif mutation == "duplicate_execution":
        start = deepcopy(next(e for e in changed if e["event_type"] == "consumer_start"))
        start["event_id"] = "extra-consumer-start"; start["interval_id"] = "extra-consumer-start"
        changed.append(start)
    assert _errors(changed, expected, context)


@pytest.mark.parametrize("mutation", ["delete", "duplicate", "swap_order", "occurrence", "object",
                                       "interval", "run", "dependency", "owner", "bytes", "layer",
                                       "ready_time", "plan"])
def test_deterministic_single_mutation_campaign(config, records, mutation):
    events, expected, context = _hand_trace(config, records)
    changed = deepcopy(events)
    if mutation == "delete":
        changed.remove(next(e for e in changed if e["event_type"] == "consumer_end"))
    elif mutation == "duplicate":
        duplicate = deepcopy(next(e for e in changed if e["event_type"] == "queue_admission"))
        duplicate["event_id"] = "duplicate-admission"; changed.append(duplicate)
    elif mutation == "swap_order":
        a = next(i for i, e in enumerate(changed) if e["event_type"] == "dependency_complete")
        b = next(i for i, e in enumerate(changed) if e["event_type"] == "ready")
        changed[a], changed[b] = changed[b], changed[a]
    elif mutation == "occurrence": next(e for e in changed if e["event_type"] == "consumer_start")["occurrence_id"] = "alien"
    elif mutation == "object": next(e for e in changed if e["event_type"] == "release")["object_id"] = "alien-object"
    elif mutation == "interval": next(e for e in changed if e["event_type"] == "consumer_end")["interval_id"] = "alien-interval"
    elif mutation == "run": next(e for e in changed if e["event_type"] != "pipeline_end")["run_id"] = "alien-run"
    elif mutation == "dependency": next(e for e in changed if e["event_type"] == "dependency_complete")["metadata"]["completion_event_id"] = "alien-root"
    elif mutation == "owner": next(e for e in changed if e["event_type"] == "ownership_transfer")["metadata"]["old_owner"] = "alien-owner"
    elif mutation == "bytes": next(e for e in changed if e["event_type"] == "release")["bytes"] += 1
    elif mutation == "layer": next(e for e in changed if e["event_type"] == "allocation")["device"] = "gpu"
    elif mutation == "ready_time": next(e for e in changed if e["event_type"] == "consumer_start")["ready_time_ns"] += 1
    elif mutation == "plan": next(e for e in changed if e["event_type"] != "pipeline_end")["input_plan_id"] = "alien-plan"
    if mutation != "swap_order":
        changed.sort(key=lambda e: e["ts_ns"])
    assert _errors(changed, expected, context)
