"""Strict identity checks and machine-readable legality report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cspp.manifest import compare_manifests, keyed_u64, manifest_hash, validate_manifest


def legality_report(records: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    errors = validate_manifest(records)
    draws = {
        record["occurrence_id"]: keyed_u64(seed, record)
        for record in records if {"occurrence_id", "workload_version", "sample_id", "augmentation_key"} <= record.keys()
    }
    return {
        "valid": not errors,
        "errors": errors,
        "sample_manifest_hash": manifest_hash(records) if not errors else None,
        "transform_draws_hash": _hash_json(draws),
        "frame_ids_hash": _hash_json({r.get("occurrence_id"): r.get("frame_ids") for r in records}),
        "occurrence_count": len(records),
        "seed": seed,
    }


def compare_plan_legality(reference: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> list[str]:
    return compare_manifests(reference, candidate)


def write_legality_report(path: str | Path, report: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _hash_json(value: Any) -> str:
    from hashlib import sha256

    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
