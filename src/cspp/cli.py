"""Command line entry points supported by milestones M0–M2."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import statistics
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from cspp.config import ConfigError, load_config, validate_config_manifest
from cspp.environment import probe_environment, write_environment_artifacts
from cspp.legality import build_execution_context, legality_report, write_legality_report
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
    validate_config_manifest(config, load_manifest(_resolved(config["manifest"]["path"])))
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
    validate_config_manifest(config, records)
    report = legality_report(records, config["experiment"]["seed"])
    if args.out:
        write_legality_report(_resolved(args.out), report)
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 2


def command_smoke(args: argparse.Namespace) -> int:
    run_id = args.run_id or f"m2-smoke-{uuid.uuid4().hex[:12]}"
    if not isinstance(run_id, str) or not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in run_id):
        raise ValueError("run_id must contain only letters, digits, hyphen, underscore")
    run_dir = ROOT / "artifacts" / "runs" / run_id
    if run_dir.exists():
        raise ValueError(f"run artifact already exists; refusing to overwrite provenance: {run_dir}")
    run_dir.mkdir(parents=True)
    config_path = _resolved(args.config)
    run_manifest: dict[str, Any] = {"run_id": run_id, "status": "PREFLIGHT", "valid": False,
                                    "git_commit": _git_commit(), "config_hash": hashlib.sha256(config_path.read_bytes()).hexdigest() if config_path.is_file() else "UNKNOWN",
                                    "manifest_hash": "UNKNOWN", "plan_d": "UNKNOWN", "plan_t": "UNKNOWN",
                                    "seed": None, "resources": {}, "failure_reason": None}
    _json_dump(run_dir / "run_manifest.json", run_manifest)
    try:
        config = load_config(config_path)
        run_manifest.update({"seed": config["experiment"]["seed"],
                             "workload_version": config["experiment"]["workload_version"],
                             "plan_d": config["execution"]["input_plan_id"],
                             "plan_t": config["execution"]["training_plan_id"],
                             "resources": config["resources"]})
        _json_dump(run_dir / "run_manifest.json", run_manifest)
        records = load_manifest(_resolved(config["manifest"]["path"]))
        run_manifest.update({"manifest_hash": manifest_hash(records),
                             "rng_scheme_version": records[0]["rng_scheme_version"]})
        _json_dump(run_dir / "run_manifest.json", run_manifest)
        validate_config_manifest(config, records)
        report = legality_report(records, config["experiment"]["seed"])
        config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
        context = build_execution_context(records, config, run_id, config_hash, _git_commit(), _git_dirty())
        resolved_config = dict(config); resolved_config["_sha256"] = config_hash
        _json_dump(run_dir / "resolved_config.json", resolved_config)
        run_manifest.update({"status": "RUNNING", "config_hash": config_hash,
                             "manifest_hash": manifest_hash(records), "seed": config["experiment"]["seed"],
                             "workload_version": config["experiment"]["workload_version"],
                             "rng_scheme_version": context["rng_scheme_version"],
                             "frame_ids_hash": context["frame_ids_hash"], "draws_hash": context["draws_hash"],
                             "plan_d": context["input_plan_id"], "plan_t": context["training_plan_id"],
                             "resources": config["resources"], "run_context": context,
                             "environment": probe_environment(ROOT), "cache_state": "synthetic_fixture_no_data_cache"})
        _json_dump(run_dir / "run_manifest.json", run_manifest)
        _json_dump(run_dir / "legality_report.json", report)
        result = run_synthetic_pipeline(config, records, run_id, run_context=context)
        expected = {r["occurrence_id"]: r for r in records}
        errors = validate_trace(result["events"], config["resources"]["queue_byte_budget"],
                                expected_occurrences=expected, expected_run_id=run_id,
                                expected_run_context=context)
        from cspp.tracing.trace import TraceRecorder
        recorder = TraceRecorder(run_id, run_context=context); recorder.events = result["events"]
        recorder.write_jsonl(run_dir / "trace.jsonl")
        _json_dump(run_dir / "trace_validation.json", {"valid": not errors, "errors": errors,
                    "events": len(result["events"]), "final_live_bytes": result["ledger"].totals,
                    "final_credit_bytes": result["queue"].queued_bytes})
        run_manifest.update({"status": "VALID" if not errors else "INVALID", "valid": not errors,
                             "failure_reason": "; ".join(errors) if errors else None})
        _json_dump(run_dir / "run_manifest.json", run_manifest)
        if errors:
            print("TRACE INVALID: " + "; ".join(errors), file=sys.stderr)
            return 2
        print(json.dumps({"valid": True, "run_id": run_id, "trace": str(run_dir / "trace.jsonl"),
                          "events": len(result["events"]), "final_live_bytes": result["ledger"].totals}, indent=2))
        return 0
    except Exception as exc:
        run_manifest.update({"status": "INVALID", "valid": False, "failure_reason": f"{type(exc).__name__}: {exc}"})
        _json_dump(run_dir / "run_manifest.json", run_manifest)
        _json_dump(run_dir / "trace_validation.json", {"valid": False, "errors": [run_manifest["failure_reason"]]})
        print(f"error: {run_manifest['failure_reason']}", file=sys.stderr)
        return 2


def _git_dirty() -> bool | str:
    import subprocess
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False)
    return bool(result.stdout.strip()) if result.returncode == 0 else "UNKNOWN"


def _measure_trace_overhead(config: dict[str, Any], records: list[dict[str, Any]], repeats: int = 3) -> dict[str, Any]:
    traced: list[int] = []
    untraced: list[int] = []
    for index in range(repeats):
        for enabled, bucket in ((False, untraced), (True, traced)):
            started = time.perf_counter_ns()
            result = run_synthetic_pipeline(config, records, f"overhead-{index}-{enabled}", tracing=enabled)
            bucket.append(time.perf_counter_ns() - started)
            if enabled and validate_trace(result["events"], config["resources"]["queue_byte_budget"],
                                          expected_occurrences={r["occurrence_id"]: r for r in records},
                                          expected_run_id=result["run_id"], expected_run_context=result["run_context"]):
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
    if destination.exists() and any(destination.iterdir()):
        print(f"refusing to overwrite non-empty audit export: {destination}", file=sys.stderr)
        return 2
    destination.mkdir(parents=True, exist_ok=True)
    for name in [*required, *( ["trace_validation_recheck.json"] if (run_dir / "trace_validation_recheck.json").is_file() else [])]:
        shutil.copy2(run_dir / name, destination / name)
    print(f"exported {args.run} -> {destination}")
    return 0


def command_validate_trace(args: argparse.Namespace) -> int:
    config_path = _resolved(args.config)
    config = load_config(_resolved(args.config))
    records = load_manifest(_resolved(config["manifest"]["path"]))
    validate_config_manifest(config, records)
    run_dir = ROOT / "artifacts" / "runs" / args.run
    try:
        run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines]
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot load trace provenance: {exc}", file=sys.stderr)
        return 2
    stored_context = run_manifest.get("run_context")
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    errors: list[str] = []
    if run_manifest.get("config_hash") != config_hash:
        errors.append("current resolved config hash differs from run provenance")
    try:
        expected_context = build_execution_context(records, config, args.run, config_hash,
                                                   (stored_context or {}).get("git_commit", "UNKNOWN"),
                                                   (stored_context or {}).get("git_dirty", "UNKNOWN"))
        if not stored_context or expected_context != stored_context:
            errors.append("stored run context does not match resolved config/manifest/seed")
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"cannot reconstruct expected run context: {exc}")
        expected_context = stored_context
    errors.extend(validate_trace(events, config["resources"]["queue_byte_budget"],
                            expected_occurrences={r["occurrence_id"]: r for r in records},
                            expected_run_id=args.run, expected_run_context=expected_context))
    result = {"valid": not errors, "errors": errors, "events": len(events)}
    _json_dump(run_dir / "trace_validation_recheck.json", result)
    print(json.dumps(result, indent=2))
    return 0 if not errors else 2


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
    smoke.add_argument("--run-id", default=None)
    smoke.set_defaults(handler=command_smoke)
    export = subs.add_parser("audit-export")
    export.add_argument("--run", required=True)
    export.add_argument("--out", required=True)
    export.set_defaults(handler=command_audit_export)
    trace_validation = subs.add_parser("validate-trace")
    trace_validation.add_argument("--run", required=True)
    trace_validation.add_argument("--config", required=True)
    trace_validation.set_defaults(handler=command_validate_trace)
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
