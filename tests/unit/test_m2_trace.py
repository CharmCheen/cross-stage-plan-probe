import threading
import time

from cspp.tracing.trace import ByteCreditQueue, LiveByteLedger, TraceRecorder, run_synthetic_pipeline, validate_trace


def test_deterministic_trace_semantic_order(config, records):
    first_result = run_synthetic_pipeline(config, records, "trace-one")
    second_result = run_synthetic_pipeline(config, records, "trace-two")
    first, second = first_result["events"], second_result["events"]

    def semantic(events):
        return [(e["event_type"], e.get("occurrence_id"), e.get("object_id")) for e in events
                if e["event_type"] not in {"consumer_wait_start", "consumer_wait_end", "pipeline_end"}]

    assert semantic(first) == semantic(second)
    assert all("worker" in event and "bytes_in" in event and "bytes_out" in event for event in first)
    for event in first:
        if event["event_type"] == "ready":
            assert event["ready_time_ns"] == event["ts_ns"]
        if event["event_type"] == "consumer_start":
            assert event["metadata"]["ready_time_ns"] <= event["ts_ns"]
    assert validate_trace(first, config["resources"]["queue_byte_budget"],
                          expected_occurrences={r["occurrence_id"]: r for r in records},
                          expected_run_id="trace-one", expected_run_context=first_result["run_context"]) == []


def test_byte_credit_blocks_until_live_object_release():
    trace = TraceRecorder("byte-test")
    ledger = LiveByteLedger(trace)
    queue = ByteCreditQueue(100, trace, ledger)
    a = {"object_id": "A", "bytes": 70, "sample_id": "a", "occurrence_id": "oa", "step_id": 0, "microbatch_id": "m0"}
    b = {"object_id": "B", "bytes": 50, "sample_id": "b", "occurrence_id": "ob", "step_id": 1, "microbatch_id": "m1"}
    queue.put(a)
    attempted = threading.Event()
    admitted = threading.Event()

    def put_b():
        attempted.set()
        queue.put(b)
        admitted.set()

    worker = threading.Thread(target=put_b)
    worker.start()
    assert attempted.wait(1)
    deadline = time.monotonic() + 1
    while not any(e["event_type"] == "queue_block_start" for e in trace.events) and time.monotonic() < deadline:
        time.sleep(0.001)
    assert any(e["event_type"] == "queue_block_start" for e in trace.events)
    assert not admitted.is_set()
    item = queue.get()
    assert {key: item[key] for key in a} == a
    assert not admitted.is_set(), "dequeue must not return credit while object remains live"
    ledger.release("A", "consumer")
    queue.release_credit(a)
    worker.join(1)
    assert admitted.is_set()
    assert any(e["event_type"] == "queue_admission" and e.get("object_id") == "B" for e in trace.events)
    assert any(e["event_type"] == "queue_block_end" and e.get("object_id") == "B" for e in trace.events)


def test_lifetime_reconciliation_and_all_layers(config, records):
    result = run_synthetic_pipeline(config, records, "lifetime-test")
    assert result["ledger"].totals == {"host": 0, "pinned": 0, "gpu": 0}
    assert validate_trace(result["events"], 100, expected_occurrences={r["occurrence_id"]: r for r in records},
                          expected_run_id="lifetime-test", expected_run_context=result["run_context"]) == []
    assert any(e["event_type"] == "ownership_transfer" for e in result["events"])


def test_intentional_leak_fails_validation():
    trace = TraceRecorder("leak")
    ledger = LiveByteLedger(trace)
    ledger.allocate("leak-object", 12, "host", "producer")
    errors = validate_trace(trace.events, 100)
    assert any("leaked live objects" in error for error in errors)


def test_double_free_and_wrong_owner_raise():
    trace = TraceRecorder("free")
    ledger = LiveByteLedger(trace)
    ledger.allocate("x", 1, "host", "producer")
    try:
        ledger.release("x", "consumer")
    except ValueError as exc:
        assert "incorrect ownership" in str(exc)
    else:
        raise AssertionError("wrong owner release was accepted")
    ledger.release("x", "producer")
    try:
        ledger.release("x", "producer")
    except ValueError as exc:
        assert "already released" in str(exc)
    else:
        raise AssertionError("double free was accepted")


def test_input_starvation_and_producer_backpressure_are_traced(config, records):
    result = run_synthetic_pipeline(config, records, "stall-test")
    events = result["events"]
    assert any(e["event_type"] == "consumer_wait_start" and e["reason"] == "input_unavailable" for e in events)
    assert any(e["event_type"] == "consumer_wait_end" for e in events)
    assert any(e["event_type"] == "queue_block_start" and e["reason"] == "queue_byte_capacity" for e in events)
    assert any(e["event_type"] == "queue_block_end" for e in events)


def test_fixture_case_a_has_no_backpressure(config, records):
    config["resources"]["queue_byte_budget"] = 200
    config["fixture"]["consumer_delays_ms"] = [0, 0, 0]
    events = run_synthetic_pipeline(config, records, "case-a-no-backpressure")["events"]
    assert not any(e["event_type"] == "queue_block_start" for e in events)


def test_fixture_case_c_exposes_input_starvation(config, records):
    config["fixture"]["producer_delays_ms"] = [10, 10, 10]
    config["fixture"]["consumer_delays_ms"] = [0, 0, 0]
    events = run_synthetic_pipeline(config, records, "case-c-input-starvation")["events"]
    waits = [e for e in events if e["event_type"] == "consumer_wait_end"]
    assert waits
    assert all(e["metadata"]["critical_dependency"] == "input_admission" for e in waits)
    assert all(e["metadata"]["duration_ns"] > 0 for e in waits)


def test_fixture_case_d_overlaps_object_lifetimes(config, records):
    config["fixture"]["object_sizes"] = [30, 30, 30]
    config["fixture"]["producer_delays_ms"] = [0, 0, 0]
    config["fixture"]["consumer_delays_ms"] = [50, 50, 50]
    events = run_synthetic_pipeline(config, records, "case-d-lifetime-overlap")["events"]
    allocations = [e["host_live_bytes"] for e in events if e["event_type"] == "allocation"]
    assert max(allocations) >= 60


def test_validator_detects_impossible_ready_order(config, records):
    events = run_synthetic_pipeline(config, records, "causal-test")["events"]
    copied = [dict(event) for event in events]
    producer_end = next(i for i, event in enumerate(copied) if event["event_type"] == "operator_end")
    ready = next(i for i, event in enumerate(copied) if event["event_type"] == "ready")
    copied[ready], copied[producer_end] = copied[producer_end], copied[ready]
    errors = validate_trace(copied, 100)
    assert any("timestamp ordering" in error or "ready precedes producer completion" in error for error in errors)


def test_validator_detects_double_release_event(config, records):
    events = run_synthetic_pipeline(config, records, "double-release-test")["events"]
    release = next(dict(e) for e in events if e["event_type"] == "release")
    events.append(release)
    events.sort(key=lambda e: e["ts_ns"])
    errors = validate_trace(events, 100)
    assert any("release for unknown/already released object" in error for error in errors)


def test_validator_detects_missing_manifest_occurrence(config, records):
    events = run_synthetic_pipeline(config, records, "missing-terminal-test")["events"]
    omitted = records[-1]["occurrence_id"]
    events = [event for event in events if event.get("occurrence_id") != omitted]
    expected = {r["occurrence_id"]: r for r in records}
    errors = validate_trace(events, 100, expected_occurrences=expected)
    assert any(f"required logical occurrence lacks exactly one consumer execution: {omitted}" in error for error in errors)


def test_validator_rejects_negative_timestamp(config, records):
    events = run_synthetic_pipeline(config, records, "negative-ts-test")["events"]
    copied = [dict(event) for event in events]
    copied[0]["ts_ns"] = -1
    errors = validate_trace(copied, 100)
    assert any("timestamp ordering violation" in error for error in errors)


def test_validator_rejects_negative_live_bytes_and_duration(config, records):
    events = run_synthetic_pipeline(config, records, "negative-live-test")["events"]
    copied = [dict(event) for event in events]
    copied[0]["host_live_bytes"] = -1
    errors = validate_trace(copied, 100)
    assert any("negative live bytes" in error for error in errors)
    copied[1]["duration_ns"] = -1
    duration_errors = validate_trace(copied, 100)
    assert any("negative or invalid duration_ns" in error for error in duration_errors)


def test_validator_rejects_incorrect_ownership_transfer(config, records):
    events = run_synthetic_pipeline(config, records, "ownership-test")["events"]
    copied = [dict(event, metadata=dict(event["metadata"])) for event in events]
    transfer = next(e for e in copied if e["event_type"] == "ownership_transfer")
    transfer["metadata"]["old_owner"] = "not-the-owner"
    errors = validate_trace(copied, 100)
    assert any("ownership transfer does not match live owner" in error for error in errors)
