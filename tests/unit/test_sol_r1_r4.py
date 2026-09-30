"""Independent adversarial oracle cases for the targeted Sol remediation."""

from __future__ import annotations

from copy import deepcopy

import pytest

from cspp.config import ConfigError, validate_config_manifest
from cspp.legality import build_execution_context, compare_execution_contexts
from cspp.manifest import RNG_SCHEME_VERSION, canonical_json, keyed_u64, rng_message, validate_manifest
from cspp.tracing.trace import run_synthetic_pipeline, validate_trace


def _trace(config, records, run_id="r1-r4-test", **kwargs):
    result = run_synthetic_pipeline(config, records, run_id, **kwargs)
    expected = {r["occurrence_id"]: r for r in records}
    return result["events"], expected, result["run_context"]


def _errors(events, expected, context):
    return validate_trace(events, context["resources"]["queue_byte_budget"], expected_occurrences=expected,
                          expected_run_id=context["run_id"], expected_run_context=context)


def _mutate(events):
    return deepcopy(events)


def test_r1_a_early_credit_return_after_dequeue_fails(config, records):
    events, expected, context = _trace(config, records, "r1a")
    changed = _mutate(events)
    aid, bid = "obj-fixture-A-epoch0", "obj-fixture-A-epoch1"
    credit = next(e for e in changed if e["event_type"] == "queue_credit_release" and e["object_id"] == aid)
    release = next(e for e in changed if e["event_type"] == "release" and e["object_id"] == aid)
    b_block_end = next(e for e in changed if e["event_type"] == "queue_block_end" and e["object_id"] == bid)
    b_alloc = next(e for e in changed if e["event_type"] == "allocation" and e["object_id"] == bid)
    b_admission = next(e for e in changed if e["event_type"] == "queue_admission" and e["object_id"] == bid)
    consumer_end = next(e for e in changed if e["event_type"] == "consumer_end" and e["object_id"] == aid)
    release["ts_ns"] = consumer_end["ts_ns"] + 10
    credit["ts_ns"] = consumer_end["ts_ns"] + 2
    b_block_end["ts_ns"] = consumer_end["ts_ns"] + 4
    b_block_start = next(e for e in changed if e["event_type"] == "queue_block_start" and e["object_id"] == bid)
    b_block_end["duration_ns"] = b_block_end["ts_ns"] - b_block_start["ts_ns"]
    b_block_end["metadata"]["observed_duration_ns"] = b_block_end["duration_ns"]
    b_block_end["metadata"]["duration_ns"] = b_block_end["duration_ns"]
    b_alloc["ts_ns"] = consumer_end["ts_ns"] + 6
    b_admission["ts_ns"] = consumer_end["ts_ns"] + 8
    b_alloc["reported_live_bytes"] = {"host": 120, "pinned": 0, "gpu": 0}
    b_alloc["host_live_bytes"] = 120
    release["reported_live_bytes"] = {"host": 50, "pinned": 0, "gpu": 0}
    release["host_live_bytes"] = 50
    changed.sort(key=lambda e: e["ts_ns"])
    assert changed.index(b_admission) < changed.index(release), "malicious trace must admit B before A terminal release"
    assert any("credit returned before terminal release" in e for e in _errors(changed, expected, context))


@pytest.mark.parametrize("field,value,expected_text", [
    ("bytes", 69, "release bytes mismatch"),
    ("device", "gpu", "release layer mismatch"),
])
def test_r1_release_fields_match_allocation(config, records, field, value, expected_text):
    events, expected, context = _trace(config, records, "r1-release-" + field)
    changed = _mutate(events)
    event = next(e for e in changed if e["event_type"] == "release")
    event[field] = value
    assert any(expected_text in e for e in _errors(changed, expected, context))


def test_r1_duplicate_credit_return_fails(config, records):
    events, expected, context = _trace(config, records, "r1d")
    changed = _mutate(events)
    credit = deepcopy(next(e for e in changed if e["event_type"] == "queue_credit_release"))
    credit["event_id"] = "manual-duplicate-credit-return"
    changed.insert(-1, credit)
    assert _errors(changed, expected, context)


def test_r1_credit_return_bytes_must_equal_admitted_bytes(config, records):
    events, expected, context = _trace(config, records, "r1-credit-bytes")
    changed = _mutate(events)
    next(e for e in changed if e["event_type"] == "queue_credit_release")["bytes"] += 1
    assert any("credit return bytes mismatch" in e for e in _errors(changed, expected, context))


def test_r1_unknown_credit_return_fails_closed():
    from cspp.tracing.trace import ByteCreditQueue, LiveByteLedger, TraceRecorder
    trace = TraceRecorder("unknown-credit")
    queue = ByteCreditQueue(100, trace, LiveByteLedger(trace))
    with pytest.raises(ValueError, match="unknown or duplicate"):
        queue.release_credit({"object_id": "unknown", "bytes": 1})


def test_r1_forged_live_snapshots_zero_fail(config, records):
    events, expected, context = _trace(config, records, "r1e")
    changed = _mutate(events)
    for event in changed:
        if event["event_type"] in {"allocation", "ownership_transfer", "release"}:
            event["reported_live_bytes"] = {"host": 0, "pinned": 0, "gpu": 0}
            for key in ("host_live_bytes", "pinned_live_bytes", "gpu_live_bytes"):
                event[key] = 0
    assert any("live bytes mismatch" in e for e in _errors(changed, expected, context))


def test_r1_dequeue_cannot_reduce_reported_credit(config, records):
    events, expected, context = _trace(config, records, "r1f")
    changed = _mutate(events)
    dequeue = next(e for e in changed if e["event_type"] == "queue_dequeue")
    dequeue["metadata"]["reported_credit_bytes"] = 0
    dequeue["metadata"]["current_queue_bytes"] = 0
    assert any("reported_credit_bytes mismatch" in e or "current_queue_bytes mismatch" in e for e in _errors(changed, expected, context))


def test_r1_all_four_reference_fixtures_reconcile(config, records):
    no_bp = deepcopy(config); no_bp["resources"]["queue_byte_budget"] = 200
    no_bp["fixture"]["consumer_delays_ms"] = [0, 0, 0]
    events, expected, ctx = _trace(no_bp, records, "r1-case-a")
    assert not validate_trace(events, 200, expected_occurrences=expected,
                              expected_run_id="r1-case-a", expected_run_context=ctx)
    events, expected, ctx = _trace(config, records, "r1-case-b")
    assert not _errors(events, expected, ctx)
    starve = deepcopy(config); starve["fixture"]["producer_delays_ms"] = [5, 5, 5]; starve["fixture"]["consumer_delays_ms"] = [0, 0, 0]
    events, expected, ctx = _trace(starve, records, "r1-case-c")
    assert not _errors(events, expected, ctx)
    overlap = deepcopy(config); overlap["fixture"]["object_sizes"] = [30, 30, 30]; overlap["fixture"]["consumer_delays_ms"] = [10, 10, 10]
    events, expected, ctx = _trace(overlap, records, "r1-case-d")
    assert not _errors(events, expected, ctx)


def test_r2_a_b_missing_consumer_execution_and_dequeue_only_fail(config, records):
    events, expected, context = _trace(config, records, "r2ab")
    removed = _mutate(events)
    removed = [e for e in removed if e["event_type"] not in {"consumer_start", "consumer_end"}]
    assert any("lacks exactly one consumer execution" in e for e in _errors(removed, expected, context))
    only_dequeue = _mutate(events)
    only_dequeue = [e for e in only_dequeue if e["event_type"] != "consumer_start" and e["event_type"] != "consumer_end"]
    assert _errors(only_dequeue, expected, context)


def test_r2_c_release_before_consumer_end_fails(config, records):
    events, expected, context = _trace(config, records, "r2c")
    changed = _mutate(events)
    release = next(e for e in changed if e["event_type"] == "release")
    release["ts_ns"] = next(e["ts_ns"] for e in changed if e["event_type"] == "consumer_start" and e["occurrence_id"] == release["occurrence_id"])
    assert _errors(changed, expected, context)


@pytest.mark.parametrize("mutation", ["duplicate_start", "missing_end", "unmatched_end"])
def test_r2_interval_pairing_fails_closed(config, records, mutation):
    events, expected, context = _trace(config, records, "r2-interval-" + mutation)
    changed = _mutate(events)
    start = next(e for e in changed if e["event_type"] == "consumer_start")
    end = next(e for e in changed if e["event_type"] == "consumer_end" and e["occurrence_id"] == start["occurrence_id"])
    if mutation == "duplicate_start":
        duplicate = deepcopy(start); duplicate["event_id"] = "malicious-second-start"; duplicate["interval_id"] = duplicate["event_id"]
        changed.append(duplicate); changed.sort(key=lambda e: e["ts_ns"])
    elif mutation == "missing_end":
        changed.remove(end)
    else:
        end["interval_id"] = "unknown-interval"
    assert _errors(changed, expected, context)


def test_r2_d_reverse_consumer_steps_fail_but_producer_reorder_passes(config, records):
    config = deepcopy(config); config["resources"]["queue_byte_budget"] = 200
    events, expected, context = _trace(config, records, "r2d-good", producer_order=[r["occurrence_id"] for r in reversed(records)])
    assert not _errors(events, expected, context)
    changed = _mutate(events)
    starts = [e for e in changed if e["event_type"] == "consumer_start"]
    starts.reverse()
    timeline = sorted([e for e in changed if e["event_type"] == "consumer_start"], key=lambda e: e["ts_ns"])
    for target, source in zip(timeline, starts):
        for key in ("sample_id", "occurrence_id", "step_id", "microbatch_id", "object_id", "ready_time_ns"):
            target[key] = source[key]
        target["metadata"] = deepcopy(source["metadata"])
    assert _errors(changed, expected, context)


def test_r2_e_required_identity_null_fails(config, records):
    events, expected, context = _trace(config, records, "r2e")
    changed = _mutate(events)
    next(e for e in changed if e["event_type"] == "consumer_start")["microbatch_id"] = None
    assert any("required identity microbatch_id" in e for e in _errors(changed, expected, context))


def test_r2_f_g_h_fake_waits_fail(config, records):
    events, expected, context = _trace(config, records, "r2fgh")
    wait = next(e for e in events if e["event_type"] == "consumer_wait_start")
    changed = _mutate(events)
    wait_end = next(e for e in changed if e["event_type"] == "consumer_wait_end")
    wait_end["ts_ns"] = wait["ts_ns"] - 1
    assert _errors(changed, expected, context)
    changed = _mutate(events)
    wait_end = next(e for e in changed if e["event_type"] == "consumer_wait_end")
    wait_end["duration_ns"] = 10**15; wait_end["metadata"]["observed_duration_ns"] = 10**15
    wait_end["metadata"]["duration_ns"] = 10**15
    assert any("observed duration" in e for e in _errors(changed, expected, context))
    fake_config = deepcopy(config); fake_config["fixture"]["producer_delays_ms"] = [0, 5, 5]
    events, expected, context = _trace(fake_config, records, "r2fake", producer_order=[records[0]["occurrence_id"], records[2]["occurrence_id"], records[1]["occurrence_id"]])
    changed = _mutate(events)
    start = next(e for e in changed if e["event_type"] == "consumer_start")
    end = next(e for e in changed if e["event_type"] == "consumer_end" and e["occurrence_id"] == start["occurrence_id"])
    later = records[2]
    availability = next(e for e in changed if e["event_type"] == "queue_admission" and e["occurrence_id"] == later["occurrence_id"])
    wait_start = deepcopy(start); wait_start.update(event_id="manual-wait-start", event_type="consumer_wait_start", interval_id="manual-wait-start", reason="input_unavailable", ts_ns=start["ts_ns"] + 1)
    wait_start["metadata"] = {"critical_dependency": "input_readiness"}
    for key in ("sample_id", "occurrence_id", "step_id", "microbatch_id"):
        wait_start[key] = later[key]
    wait_end = deepcopy(wait_start); wait_end.update(event_id="manual-wait-end", event_type="consumer_wait_end", interval_id="manual-wait-start", ts_ns=availability["ts_ns"] + 1)
    wait_end["metadata"] = {"critical_dependency": "input_readiness", "availability_event_id": availability["event_id"]}
    duration = wait_end["ts_ns"] - wait_start["ts_ns"]
    wait_end["duration_ns"] = duration; wait_end["metadata"].update({"duration_ns": duration, "observed_duration_ns": duration})
    changed.extend([wait_start, wait_end]); changed.sort(key=lambda e: e["ts_ns"])
    assert any("overlaps consumer compute" in e for e in _errors(changed, expected, context))


def test_r3_a_generic_dependency_operator_name_and_multiple_objects(config, records):
    events, expected, context = _trace(config, records, "r3a")
    changed = _mutate(events)
    next(e for e in changed if e["event_type"] == "operator_begin")["operator"] = "some_other_completion_stage"
    next(e for e in changed if e["event_type"] == "operator_end")["operator"] = "some_other_completion_stage"
    assert not _errors(changed, expected, context)
    occurrence = records[0]["occurrence_id"]
    start = next(e for e in changed if e["event_type"] == "consumer_start" and e["occurrence_id"] == occurrence)
    end = next(e for e in changed if e["event_type"] == "consumer_end" and e["occurrence_id"] == occurrence)
    alloc = deepcopy(start); alloc.update(event_id="manual-temp-allocation", event_type="allocation", object_id="temp-buffer", ts_ns=start["ts_ns"] + 1, device="host", bytes=5, duration_ns=None, reported_live_bytes={"host": 75, "pinned": 0, "gpu": 0}, host_live_bytes=75)
    alloc["metadata"] = {"owner": "temporary-stage", "object_role": "temporary"}
    alloc["pinned_live_bytes"] = alloc["gpu_live_bytes"] = 0
    rel = deepcopy(alloc); rel.update(event_id="manual-temp-release", event_type="release", ts_ns=end["ts_ns"] - 1)
    rel["metadata"] = {"owner": "temporary-stage", "object_role": "temporary"}
    rel["reported_live_bytes"] = {"host": 70, "pinned": 0, "gpu": 0}; rel["host_live_bytes"] = 70
    changed.extend([alloc, rel]); changed.sort(key=lambda e: e["ts_ns"])
    # Recompute independent resource snapshots after injecting a second physical allocation.
    live = 0
    for event in changed:
        if event["event_type"] == "allocation": live += event["bytes"]
        elif event["event_type"] == "release": live -= event["bytes"]
        if event["event_type"] in {"allocation", "ownership_transfer", "release"}:
            event["reported_live_bytes"] = {"host": live, "pinned": 0, "gpu": 0}
            event["host_live_bytes"], event["pinned_live_bytes"], event["gpu_live_bytes"] = live, 0, 0
    assert not _errors(changed, expected, context)


def test_r3_c_dependency_missing_d_duplicate_logical_work_e_leak_fail(config, records):
    events, expected, context = _trace(config, records, "r3cde")
    changed = _mutate(events)
    changed = [e for e in changed if not (e["event_type"] == "dependency_complete" and e["occurrence_id"] == records[0]["occurrence_id"])]
    assert any("incomplete declared dependencies" in e for e in _errors(changed, expected, context))
    changed = _mutate(events)
    start = deepcopy(next(e for e in changed if e["event_type"] == "consumer_start")); start["event_id"] = "duplicate-consumer-start"; start["interval_id"] = start["event_id"]
    changed.insert(-1, start)
    assert _errors(changed, expected, context)
    changed = _mutate(events)
    changed = [e for e in changed if not (e["event_type"] == "release" and e["object_id"] == "obj-" + records[0]["occurrence_id"])]
    assert any("leaked live objects" in e for e in _errors(changed, expected, context))


def test_r4_seed_workload_hash_and_plan_bindings(config, records):
    one = build_execution_context(records, config, "r4", "a" * 64)
    changed_config = deepcopy(config); changed_config["experiment"]["seed"] += 1
    two = build_execution_context(records, changed_config, "r4", "a" * 64)
    assert any("seed" in e or "draws_hash" in e for e in compare_execution_contexts(one, two))
    bad = deepcopy(config); bad["experiment"]["workload_version"] = "other"
    with pytest.raises(ConfigError):
        validate_config_manifest(bad, records)
    events, expected, context = _trace(config, records, "r4c")
    changed = _mutate(events); changed[0]["manifest_hash"] = "f" * 64
    assert _errors(changed, expected, context)
    changed = _mutate(events); changed[0]["input_plan_id"] = "mixed-plan"
    assert _errors(changed, expected, context)
    changed = _mutate(events)
    next(e for e in changed if e["event_type"] == "consumer_start")["metadata"]["augmentation_draw_u64"] ^= 1
    assert any("actual RNG draw" in e for e in _errors(changed, expected, context))
    changed = deepcopy(records); changed[0]["rng_scheme_version"] = "legacy-nul-v1"
    assert any("rng_scheme_version" in e for e in validate_manifest(changed))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_r4_f_nonfinite_weight_rejected(records, value):
    changed = deepcopy(records); changed[0]["weight"] = value
    assert any("weight must be finite" in e for e in validate_manifest(changed))


def test_r4_g_empty_workload_version_rejected(records):
    changed = deepcopy(records); changed[0]["workload_version"] = ""
    assert any("workload_version" in e for e in validate_manifest(changed))


def test_r4_rng_scheme_mismatch_in_context_fails(config, records):
    events, expected, context = _trace(config, records, "r4-scheme")
    changed = deepcopy(context); changed["rng_scheme_version"] = "legacy-nul-v1"
    assert _errors(events, expected, changed)


def test_r4_h_structured_rng_encoding_eliminates_nul_collision(records):
    first = deepcopy(records[0]); second = deepcopy(records[0])
    first["sample_id"], first["occurrence_id"] = "a\u0000b", "c"
    second["sample_id"], second["occurrence_id"] = "a", "b\u0000c"
    assert rng_message(7, first) != rng_message(7, second)
    assert RNG_SCHEME_VERSION in first["rng_scheme_version"]
    assert keyed_u64(7, first) != keyed_u64(7, second)


def test_r4_i_disabled_trace_is_explicitly_unsupported(config):
    changed = deepcopy(config); changed["trace"]["enabled"] = False
    from cspp.config import validate_config
    with pytest.raises(ConfigError, match="trace.enabled=false is unsupported"):
        validate_config(changed)
