"""Workload legality reports and run identity binding."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from cspp.manifest import RNG_SCHEME_VERSION, compare_manifests, keyed_u64, manifest_hash, validate_manifest


def _hash_json(value: Any) -> str:
    from cspp.manifest import canonical_json
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def legality_report(records: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    errors = validate_manifest(records)
    draws = {}
    if not errors:
        draws = {r["occurrence_id"]: keyed_u64(seed, r) for r in records}
    return {
        "valid": not errors, "errors": errors,
        "sample_manifest_hash": manifest_hash(records) if not errors else None,
        "transform_draws_hash": _hash_json(draws),
        "frame_ids_hash": _hash_json({r.get("occurrence_id"): r.get("frame_ids") for r in records}),
        "rng_scheme_version": RNG_SCHEME_VERSION,
        "occurrence_count": len(records), "seed": seed,
    }


def compare_plan_legality(reference: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> list[str]:
    return compare_manifests(reference, candidate)


def build_execution_context(records: list[dict[str, Any]], config: dict[str, Any], run_id: str,
                            resolved_config_hash: str | None = None, git_commit: str = "UNKNOWN",
                            git_dirty: str = "UNKNOWN") -> dict[str, Any]:
    report = legality_report(records, config["experiment"]["seed"])
    if not report["valid"]:
        raise ValueError("cannot bind invalid manifest: " + "; ".join(report["errors"]))
    if config["experiment"]["workload_version"] != records[0]["workload_version"]:
        raise ValueError("config workload_version does not match manifest")
    resources = config["resources"]
    work_identity = {
        "manifest_hash": report["sample_manifest_hash"], "seed": report["seed"],
        "workload_version": config["experiment"]["workload_version"],
        "rng_scheme_version": report["rng_scheme_version"],
        "draws_hash": report["transform_draws_hash"], "frame_ids_hash": report["frame_ids_hash"],
        "resources": resources,
    }
    context = {
        "run_id": run_id, "manifest_hash": report["sample_manifest_hash"],
        "seed": report["seed"], "workload_version": config["experiment"]["workload_version"],
        "rng_scheme_version": report["rng_scheme_version"],
        "draws_hash": report["transform_draws_hash"], "frame_ids_hash": report["frame_ids_hash"],
        "input_plan_id": config["execution"]["input_plan_id"],
        "training_plan_id": config["execution"]["training_plan_id"],
        "resolved_config_hash": resolved_config_hash or _hash_json(config),
        "resources": resources, "git_commit": git_commit, "git_dirty": git_dirty,
        "work_identity_hash": _hash_json(work_identity),
    }
    context["run_context_hash"] = _hash_json(context)
    return context


def validate_execution_context(context: dict[str, Any]) -> list[str]:
    errors = []
    required = {"run_id", "manifest_hash", "seed", "workload_version", "rng_scheme_version", "draws_hash",
                "frame_ids_hash", "input_plan_id", "training_plan_id", "resolved_config_hash", "resources",
                "git_commit", "git_dirty", "work_identity_hash", "run_context_hash"}
    missing = required - context.keys()
    if missing:
        return [f"execution context missing {sorted(missing)}"]
    copied = dict(context)
    supplied = copied.pop("run_context_hash")
    try:
        calculated_hash = _hash_json(copied)
    except (TypeError, ValueError) as exc:
        calculated_hash = None
        errors.append(f"run context is not canonical JSON: {exc}")
    if supplied != calculated_hash:
        errors.append("run_context_hash mismatch")
    if context.get("rng_scheme_version") != RNG_SCHEME_VERSION:
        errors.append("unsupported RNG scheme in run context")
    if not isinstance(context.get("seed"), int) or isinstance(context.get("seed"), bool):
        errors.append("invalid run seed")
    if not all(isinstance(context.get(k), str) and context[k] for k in ("run_id", "manifest_hash", "workload_version", "rng_scheme_version",
                                                                            "draws_hash", "frame_ids_hash", "input_plan_id",
                                                                            "training_plan_id", "resolved_config_hash")):
        errors.append("run context has empty identity field")
    for key in ("manifest_hash", "draws_hash", "frame_ids_hash", "resolved_config_hash", "work_identity_hash", "run_context_hash"):
        value = context.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            errors.append(f"run context {key} must be a lowercase SHA-256 digest")
    if not isinstance(context.get("resources"), dict):
        errors.append("run context resources must be an object")
    work_identity = {
        "manifest_hash": context.get("manifest_hash"), "seed": context.get("seed"),
        "workload_version": context.get("workload_version"),
        "rng_scheme_version": context.get("rng_scheme_version"), "draws_hash": context.get("draws_hash"),
        "frame_ids_hash": context.get("frame_ids_hash"), "resources": context.get("resources"),
    }
    try:
        expected_work_hash = _hash_json(work_identity)
    except (TypeError, ValueError):
        expected_work_hash = None
    if context.get("work_identity_hash") != expected_work_hash:
        errors.append("work_identity_hash mismatch")
    return errors


def compare_execution_contexts(reference: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    errors = validate_execution_context(reference) + validate_execution_context(candidate)
    for field in ("manifest_hash", "seed", "workload_version", "rng_scheme_version", "draws_hash", "frame_ids_hash", "work_identity_hash"):
        if reference.get(field) != candidate.get(field):
            errors.append(f"execution contract mismatch: {field}")
    return errors


def write_legality_report(path: str | Path, report: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
