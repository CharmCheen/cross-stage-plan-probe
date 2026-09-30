"""Generate correctness traces for the four M2 instrumentation fixture cases."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from cspp.config import load_config
from cspp.manifest import load_manifest
from cspp.tracing.trace import TraceRecorder, run_synthetic_pipeline, validate_trace

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    base = load_config(ROOT / "configs" / "m0_m2.local.yaml")
    records = load_manifest(ROOT / base["manifest"]["path"])
    cases = {
        "A_no_backpressure": (200, [70, 50, 30], [0, 0, 0], [0, 0, 0]),
        "B_byte_credit_block": (100, [70, 50, 30], [0, 5, 0], [15, 2, 2]),
        "C_input_starvation": (100, [70, 50, 30], [15, 15, 15], [0, 0, 0]),
        "D_overlapping_lifetimes": (100, [30, 30, 30], [0, 0, 0], [50, 50, 50]),
    }
    out = ROOT / "artifacts" / "milestones" / "M2" / "fixtures"
    out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for case, (capacity, sizes, producer_delays, consumer_delays) in cases.items():
        config = copy.deepcopy(base)
        config["resources"]["queue_byte_budget"] = capacity
        config["fixture"].update(object_sizes=sizes, producer_delays_ms=producer_delays,
                                 consumer_delays_ms=consumer_delays)
        result = run_synthetic_pipeline(config, records, f"m2-{case}")
        expected = {r["occurrence_id"]: r for r in records}
        errors = validate_trace(result["events"], capacity,
                                expected_occurrences=expected,
                                expected_run_id=f"m2-{case}", expected_run_context=result["run_context"])
        recorder = TraceRecorder(f"m2-{case}", run_context=result["run_context"])
        recorder.events = result["events"]
        recorder.write_jsonl(out / f"{case}.jsonl")
        summary[case] = {
            "valid": not errors,
            "errors": errors,
            "event_count": len(result["events"]),
            "queue_blocked": any(e["event_type"] == "queue_block_start" for e in result["events"]),
            "input_waiting": any(e["event_type"] == "consumer_wait_start" for e in result["events"]),
            "final_live_bytes": result["ledger"].totals,
            "final_queue_bytes": result["queue"].queued_bytes,
        }
        if errors:
            raise SystemExit(f"{case} trace invalid: {errors}")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
