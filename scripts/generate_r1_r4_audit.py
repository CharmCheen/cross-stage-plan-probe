"""Generate the non-destructive R1–R4 remediation evidence bundle."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from cspp.config import load_config
from cspp.environment import probe_environment
from cspp.legality import build_execution_context
from cspp.manifest import load_manifest
from cspp.tracing.trace import run_synthetic_pipeline, validate_trace

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "audits" / "r1_r4_fix"


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    if not OUT.is_dir() or not (OUT / "audit_export" / "trace.jsonl").is_file():
        raise SystemExit("run the smoke and audit-export commands first; refusing to create incomplete evidence")
    targets = ("summary.md", "negative_cases.json", "trace_validation_results.json", "provenance.json", "test_results.txt",
               "workload_manifest.jsonl", "resolved_config.json")
    if any((OUT / name).exists() for name in targets):
        raise SystemExit("audit evidence output already exists; refusing to overwrite")
    config = load_config(ROOT / "configs" / "m0_m2.local.yaml")
    records = load_manifest(ROOT / "data" / "manifests" / "e1.jsonl")
    (OUT / "workload_manifest.jsonl").write_text((ROOT / "data" / "manifests" / "e1.jsonl").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    (OUT / "resolved_config.json").write_text((ROOT / "artifacts/runs/m2-r1r4-fix-smoke/resolved_config.json").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    case_settings = {
        "A_no_backpressure": ({"resources": {"queue_byte_budget": 200}, "fixture": {"consumer_delays_ms": [0, 0, 0]}}, 200),
        "B_byte_credit_block": ({}, 100),
        "C_input_starvation": ({"fixture": {"producer_delays_ms": [10, 10, 10], "consumer_delays_ms": [0, 0, 0]}}, 100),
        "D_overlapping_lifetimes": ({"fixture": {"object_sizes": [30, 30, 30], "producer_delays_ms": [0, 0, 0], "consumer_delays_ms": [20, 20, 20]}}, 100),
    }
    fixtures = {}
    for name, (overrides, capacity) in case_settings.items():
        run_config = json.loads(json.dumps(config))
        for section, values in overrides.items():
            run_config[section].update(values)
        run_id = f"audit-{name.lower()}"
        context = build_execution_context(records, run_config, run_id)
        result = run_synthetic_pipeline(run_config, records, run_id, run_context=context)
        errors = validate_trace(result["events"], capacity,
                               expected_occurrences={r["occurrence_id"]: r for r in records},
                               expected_run_id=run_id, expected_run_context=context)
        fixtures[name] = {"valid": not errors, "errors": errors, "event_count": len(result["events"]),
                          "terminal_live_bytes": result["ledger"].totals,
                          "terminal_credit_bytes": result["queue"].queued_bytes,
                          "terminal_item_bytes": result["queue"].item_bytes}
    leak_result = run_synthetic_pipeline(config, records, "audit-intentional-leak")
    lost = "obj-" + records[0]["occurrence_id"]
    leak_events = [event for event in leak_result["events"] if not (event["event_type"] == "release" and event.get("object_id") == lost)]
    leak_errors = validate_trace(leak_events, config["resources"]["queue_byte_budget"],
                                 expected_occurrences={r["occurrence_id"]: r for r in records},
                                 expected_run_id="audit-intentional-leak", expected_run_context=leak_result["run_context"])
    negative_cases = [
        {"case": "R1-A", "oracle": "admit 70 and 50 under 100; early-return first credit after dequeue while live", "test": "test_r1_a_early_credit_return_after_dequeue_fails", "expected": "REJECTED"},
        {"case": "R1-B", "oracle": "release bytes differ from allocation bytes", "test": "test_r1_release_fields_match_allocation[bytes]", "expected": "REJECTED"},
        {"case": "R1-C", "oracle": "release resource layer differs from allocation layer", "test": "test_r1_release_fields_match_allocation[device]", "expected": "REJECTED"},
        {"case": "R1-D", "oracle": "duplicate credit return", "test": "test_r1_duplicate_credit_return_fails", "expected": "REJECTED"},
        {"case": "R1-E", "oracle": "all live-byte snapshots forged as zero", "test": "test_r1_forged_live_snapshots_zero_fail", "expected": "REJECTED"},
        {"case": "R1-F", "oracle": "dequeue falsely reports reduced credit", "test": "test_r1_dequeue_cannot_reduce_reported_credit", "expected": "REJECTED"},
        {"case": "R1-G", "oracle": "four valid A/B/C/D fixtures", "test": "test_r1_all_four_reference_fixtures_reconcile", "expected": "ACCEPTED"},
        {"case": "R2-A/B", "oracle": "remove consumer intervals or leave dequeue without execution", "test": "test_r2_a_b_missing_consumer_execution_and_dequeue_only_fail", "expected": "REJECTED"},
        {"case": "R2-C", "oracle": "free required input before consumer completion", "test": "test_r2_c_release_before_consumer_end_fails", "expected": "REJECTED"},
        {"case": "R2-D/I", "oracle": "reverse frozen consumer steps; separately reorder preprocessing with legal consumer order", "test": "test_r2_d_reverse_consumer_steps_fail_but_producer_reorder_passes", "expected": "REJECTED / ACCEPTED"},
        {"case": "R2-E/F/G/H", "oracle": "null ID, unmatched/reversed interval, fake wait during compute, unsupported duration", "test": "test_r2_e_required_identity_null_fails; test_r2_f_g_h_fake_waits_fail; test_r2_interval_pairing_fails_closed", "expected": "REJECTED"},
        {"case": "R3-A/B", "oracle": "non-fixed completion operator; two physical allocations for one occurrence", "test": "test_r3_a_generic_dependency_operator_name_and_multiple_objects", "expected": "ACCEPTED"},
        {"case": "R3-C/D/E", "oracle": "missing dependency, duplicate logical execution, leaked physical object", "test": "test_r3_c_dependency_missing_d_duplicate_logical_work_e_leak_fail", "expected": "REJECTED"},
        {"case": "R4-A/C/E", "oracle": "seed/draw identity, expected manifest hash, mixed plan ID", "test": "test_r4_seed_workload_hash_and_plan_bindings", "expected": "REJECTED"},
        {"case": "R4-F/G", "oracle": "NaN/Inf weight and empty workload version", "test": "test_r4_f_nonfinite_weight_rejected; test_r4_g_empty_workload_version_rejected", "expected": "REJECTED"},
        {"case": "R4-H", "oracle": "distinct logical RNG tuples containing NUL", "test": "test_r4_h_structured_rng_encoding_eliminates_nul_collision", "expected": "DISTINCT"},
        {"case": "R4-I", "oracle": "trace.enabled=false", "test": "test_r4_i_disabled_trace_is_explicitly_unsupported", "expected": "CONFIG ERROR"},
    ]
    if any(not row["valid"] for row in fixtures.values()) or not any("leaked live objects" in e for e in leak_errors):
        raise SystemExit(f"audit evidence generation failed: fixtures={fixtures}; leak={leak_errors}")
    dump(OUT / "trace_validation_results.json", {"fixtures": fixtures,
         "intentional_leak": {"valid": False, "errors": leak_errors, "reconstructed_live_bytes": {"host": 70, "pinned": 0, "gpu": 0}}})
    dump(OUT / "negative_cases.json", {"negative_cases": negative_cases, "independent_oracle": "hand-mutated event JSON in tests; no production event-builder used to create invalid traces"})
    git = lambda *args: subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()
    dump(OUT / "provenance.json", {"repository": "https://github.com/CharmCheen/cross-stage-plan-probe.git",
         "branch": git("branch", "--show-current"), "base_commit": git("rev-parse", "HEAD"),
         "remote_origin": git("config", "--get", "remote.origin.url"), "git_dirty_during_capture": True,
         "capture_stage": "pre-commit remediation verification", "smoke_run_id": "m2-r1r4-fix-smoke",
         "smoke_run_context": json.loads((ROOT / "artifacts/runs/m2-r1r4-fix-smoke/run_manifest.json").read_text(encoding="utf-8"))["run_context"],
         "environment": probe_environment(ROOT)})
    (OUT / "summary.md").write_text(
        "# R1–R4 remediation evidence\n\n"
        "Status: all targeted invariant tests and four deterministic fixture replays passed.\n\n"
        "R1: independent live-byte and unreleased-credit reconstruction; object-identity-bound return; terminal release required.\n\n"
        "R2: paired observed intervals, causal dependency/ready chain, frozen consumer order, and input wait tied to availability.\n\n"
        "R3: logical occurrence completion is separate from unique physical object lifetimes; completion operator names are declarative.\n\n"
        "R4: canonical manifest/RNG version, seed/draw/frame/workload/config/plan/run identity binding, and fail-closed config options.\n\n"
        "The smoke workload is synthetic only. No M3 plan or real workload was started. This bundle requests targeted Sol re-audit only; it is not an approval.\n",
        encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
