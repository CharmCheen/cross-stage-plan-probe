"""Command line entry points supported by milestones M0–M2."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import statistics
import time
from pathlib import Path
from typing import Any

import yaml

from cspp.config import ConfigError, load_config
from cspp.environment import write_environment_artifacts
from cspp.legality import legality_report, write_legality_report
from cspp.manifest import load_manifest, manifest_hash, validate_manifest
from cspp.tracing.trace import run_synthetic_pipeline, validate_trace

ROOT = Path.cwd()


def _json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolved(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def command_doctor(args: argparse.Namespace) -> int:
    load_config(_resolved(args.config))
    data = write_environment_artifacts(ROOT)
    print(json.dumps({"environment": "artifacts/environment.json", "git": data["git"]}, indent=2))
    return 0


def command_validate_config(args: argparse.Namespace) -> int:
    config = load_config(_resolved(args.config))
    resolved = _resolved(args.config)
    snapshot = dict(config)
    snapshot["_source"] = str(resolved.resolve())
    snapshot["_sha256"] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    out = ROOT / "artifacts" / "resolved_configs" / f"{resolved.stem}.json"
    _json_dump(out, snapshot)
    print(f"VALID {resolved} -> {out.relative_to(ROOT)}")
    return 0


def command_validate_manifest(args: argparse.Namespace) -> int:
    records = load_manifest(_resolved(args.manifest))
    errors = validate_manifest(records)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    print(json.dumps({"valid": True, "occurrences": len(records), "manifest_hash": manifest_hash(records)}, indent=2))
    return 0


def command_legality(args: argparse.Namespace) -> int:
    config = load_config(_resolved(args.config))
    records = load_manifest(_resolved(config["manifest"]["path"]))
    report = legality_report(records, config["experiment"]["seed"])
    if args.out:
        write_legality_report(_resolved(args.out), report)
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 2


def command_smoke(args: argparse.Namespace) -> int:
    config_path = _resolved(args.config)
    config = load_config(config_path)
    records = load_manifest(_resolved(config["manifest"]["path"]))
    manifest_errors = validate_manifest(records)
    if manifest_errors:
        raise ValueError("manifest invalid: " + "; ".join(manifest_errors))
    report = legality_report(records, config["experiment"]["seed"])
    if not report["valid"]:
        raise ValueError("legality validation failed")
    result = run_synthetic_pipeline(config, records, args.run_id)
    expected = {r["occurrence_id"]: r for r in records}
    errors = validate_trace(result["events"], config["resources"]["queue_byte_budget"],
                            expected_occurrences=expected, expected_run_id=args.run_id)
    run_dir = ROOT / "artifacts" / "runs" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "trace.jsonl"
    from cspp.tracing.trace import TraceRecorder

    recorder = TraceRecorder(args.run_id)
    recorder.events = result["events"]
    recorder.write_jsonl(trace_path)
    _json_dump(run_dir / "legality_report.json", report)
    resolved_config = dict(config)
    resolved_config["_sha256"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    _json_dump(run_dir / "resolved_config.json", resolved_config)
    git_commit = _git_commit()
    run_manifest = {
        "run_id": args.run_id,
        "git_commit": git_commit,
        "config_hash": resolved_config["_sha256"],
        "manifest_hash": manifest_hash(records),
        "plan_d": "synthetic",
        "plan_t": "consumer",
        "seed": config["experiment"]["seed"],
        "resources": config["resources"],
        "environment": write_environment_artifacts(ROOT),
        "cache_state": "synthetic_fixture_no_data_cache",
        "valid": not errors,
        "invalid_reason": "; ".join(errors) if errors else None,
    }
    _json_dump(run_dir / "run_manifest.json", run_manifest)
    _json_dump(run_dir / "trace_validation.json", {"valid": not errors, "errors": errors,
                                                      "events": len(result["events"]),
                                                      "final_live_bytes": result["ledger"].totals,
                                                      "final_queue_bytes": result["queue"].queued_bytes})
    if errors:
        print("TRACE INVALID: " + "; ".join(errors), file=sys.stderr)
        return 2
    milestone = ROOT / "artifacts" / "milestones" / "M2"
    milestone.mkdir(parents=True, exist_ok=True)
    shutil.copy2(trace_path, milestone / "generated_trace.jsonl")
    shutil.copy2(run_dir / "run_manifest.json", milestone / "run_manifest.json")
    shutil.copy2(run_dir / "legality_report.json", milestone / "legality_report.json")
    overhead = _measure_trace_overhead(config, records)
    _json_dump(milestone / "tracing_overhead.json", overhead)
    print(json.dumps({"valid": True, "run_id": args.run_id, "trace": str(trace_path),
                      "events": len(result["events"]), "final_live_bytes": result["ledger"].totals}, indent=2))
    return 0


def _measure_trace_overhead(config: dict[str, Any], records: list[dict[str, Any]], repeats: int = 3) -> dict[str, Any]:
    traced: list[int] = []
    untraced: list[int] = []
    for index in range(repeats):
        for enabled, bucket in ((False, untraced), (True, traced)):
            started = time.perf_counter_ns()
            result = run_synthetic_pipeline(config, records, f"overhead-{index}-{enabled}", tracing=enabled)
            bucket.append(time.perf_counter_ns() - started)
            if enabled and validate_trace(result["events"], config["resources"]["queue_byte_budget"],
                                          expected_occurrences={r["occurrence_id"]: r for r in records}):
                raise ValueError("tracing-on overhead run produced invalid trace")
    on_median, off_median = statistics.median(traced), statistics.median(untraced)
    return {
        "fixture": "same synthetic producer-consumer config and manifest",
        "repeats_per_mode": repeats,
        "tracing_on_wall_ns": traced,
        "tracing_off_wall_ns": untraced,
        "tracing_on_median_ns": on_median,
        "tracing_off_median_ns": off_median,
        "median_relative_overhead": (on_median - off_median) / off_median if off_median else None,
        "measurement": "diagnostic only; timing includes Python thread startup and fixture delays",
    }


def _git_commit() -> str:
    import subprocess

    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"


def command_audit_export(args: argparse.Namespace) -> int:
    run_dir = ROOT / "artifacts" / "runs" / args.run
    if not run_dir.is_dir():
        print(f"unknown run: {args.run}", file=sys.stderr)
        return 2
    required = ["trace.jsonl", "run_manifest.json", "legality_report.json", "trace_validation.json"]
    missing = [name for name in required if not (run_dir / name).is_file()]
    if missing:
        print(f"incomplete run artifacts: {missing}", file=sys.stderr)
        return 2
    destination = _resolved(args.out)
    destination.mkdir(parents=True, exist_ok=True)
    for name in required:
        shutil.copy2(run_dir / name, destination / name)
    print(f"exported {args.run} -> {destination}")
    return 0


def command_not_in_scope(args: argparse.Namespace) -> int:
    print(f"{args.command} belongs to a later milestone and is outside the authorized M0–M2 scope", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cspp")
    subs = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("doctor", command_doctor), ("validate-config", command_validate_config),
                          ("legality-check", command_legality)):
        cmd = subs.add_parser(name)
        cmd.add_argument("--config", required=True)
        cmd.set_defaults(handler=handler)
    manifest_cmd = subs.add_parser("validate-manifest")
    manifest_cmd.add_argument("--manifest", required=True)
    manifest_cmd.set_defaults(handler=command_validate_manifest)
    legality_cmd = subs.choices["legality-check"]
    legality_cmd.add_argument("--out")
    smoke = subs.add_parser("smoke")
    smoke.add_argument("--config", required=True)
    smoke.add_argument("--run-id", default="m2-synthetic-smoke")
    smoke.set_defaults(handler=command_smoke)
    export = subs.add_parser("audit-export")
    export.add_argument("--run", required=True)
    export.add_argument("--out", required=True)
    export.set_defaults(handler=command_audit_export)
    for name in ("calibrate-input", "calibrate-training"):
        cmd = subs.add_parser(name)
        cmd.add_argument("--config", required=True)
        cmd.set_defaults(handler=command_not_in_scope)
    run = subs.add_parser("run")
    run.add_argument("--config", required=True)
    run.add_argument("--plan-d", required=True)
    run.add_argument("--plan-t", required=True)
    run.add_argument("--seed", required=True, type=int)
    run.set_defaults(handler=command_not_in_scope)
    matrix = subs.add_parser("run-matrix")
    matrix.add_argument("--config", required=True)
    matrix.add_argument("--matrix", required=True)
    matrix.set_defaults(handler=command_not_in_scope)
    analyze = subs.add_parser("analyze")
    analyze.add_argument("--runs", required=True)
    analyze.add_argument("--out", required=True)
    analyze.set_defaults(handler=command_not_in_scope)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (ConfigError, ValueError, OSError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
