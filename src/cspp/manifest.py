"""Frozen workload manifest validation and versioned, keyed randomness."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from pathlib import Path
from typing import Any

RNG_SCHEME_VERSION = "hmac-sha256-canonical-json-tuple-v2"
REQUIRED_FIELDS = ("sample_id", "occurrence_id", "step_id", "microbatch_id",
                   "sample_order_baseline", "frame_selection_spec", "frame_ids",
                   "augmentation_key", "workload_version", "weight", "rng_scheme_version")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def validate_manifest(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if not isinstance(records, list) or not records:
        return ["manifest must be a non-empty list"]
    seen: set[str] = set()
    for i, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"record[{i}] must be an object")
            continue
        missing = set(REQUIRED_FIELDS) - record.keys()
        if missing:
            errors.append(f"record[{i}] missing fields {sorted(missing)}")
        for key in ("sample_id", "occurrence_id", "microbatch_id", "augmentation_key", "workload_version"):
            value = record.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"record[{i}].{key} must be a non-empty string")
        occurrence = record.get("occurrence_id")
        if isinstance(occurrence, str) and occurrence:
            if occurrence in seen:
                errors.append(f"duplicate occurrence_id: {occurrence}")
            seen.add(occurrence)
        for key in ("step_id", "sample_order_baseline"):
            value = record.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"record[{i}].{key} must be a non-negative integer")
        weight = record.get("weight")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not math.isfinite(weight) or weight <= 0:
            errors.append(f"record[{i}].weight must be finite and positive")
        if not isinstance(record.get("frame_selection_spec"), dict) or not record.get("frame_selection_spec"):
            errors.append(f"record[{i}].frame_selection_spec must be a non-empty object")
        frames = record.get("frame_ids")
        if not isinstance(frames, list) or not frames:
            errors.append(f"record[{i}].frame_ids must be a non-empty list")
        elif any(not isinstance(v, (str, int)) or isinstance(v, bool) or (isinstance(v, str) and not v) for v in frames):
            errors.append(f"record[{i}].frame_ids contain invalid IDs")
        if record.get("rng_scheme_version") != RNG_SCHEME_VERSION:
            errors.append(f"record[{i}].rng_scheme_version must be {RNG_SCHEME_VERSION}")
        try:
            canonical_json(record)
        except (TypeError, ValueError) as exc:
            errors.append(f"record[{i}] is not canonical JSON: {exc}")
    return errors


def manifest_hash(records: list[dict[str, Any]]) -> str:
    ordered = sorted(records, key=lambda r: str(r.get("occurrence_id", "")))
    return hashlib.sha256(canonical_json(ordered).encode("utf-8")).hexdigest()


def load_manifest(path: str | Path) -> list[dict[str, Any]]:
    records = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"invalid {v}")))
            if not isinstance(value, dict):
                raise ValueError(f"manifest line {line_number} must be a JSON object")
            records.append(value)
    errors = validate_manifest(records)
    if errors:
        raise ValueError("manifest invalid: " + "; ".join(errors))
    return records


def write_manifest(path: str | Path, records: list[dict[str, Any]]) -> None:
    errors = validate_manifest(records)
    if errors:
        raise ValueError("manifest invalid: " + "; ".join(errors))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(canonical_json(r) + "\n" for r in records), encoding="utf-8", newline="\n")


def rng_message(seed: int, record: dict[str, Any], purpose: str = "augmentation") -> bytes:
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    if not isinstance(purpose, str) or not purpose:
        raise ValueError("purpose must be a non-empty string")
    tuple_value = [RNG_SCHEME_VERSION, seed, record["workload_version"], record["sample_id"],
                   record["occurrence_id"], record["augmentation_key"], purpose]
    return canonical_json(tuple_value).encode("utf-8")


def keyed_u64(seed: int, record: dict[str, Any], purpose: str = "augmentation") -> int:
    if record.get("rng_scheme_version") != RNG_SCHEME_VERSION:
        raise ValueError("unsupported or missing rng_scheme_version")
    digest = hmac.new(b"cspp-keyed-rng-v2", rng_message(seed, record, purpose), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big")


def compare_manifests(reference: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> list[str]:
    errors = validate_manifest(reference) + validate_manifest(candidate)
    if errors:
        return errors
    left = {r["occurrence_id"]: r for r in reference}
    right = {r["occurrence_id"]: r for r in candidate}
    for missing in sorted(left.keys() - right.keys()):
        errors.append(f"missing occurrence: {missing}")
    for extra in sorted(right.keys() - left.keys()):
        errors.append(f"extra occurrence: {extra}")
    identity_fields = ("sample_id", "step_id", "microbatch_id", "sample_order_baseline", "weight",
                       "frame_selection_spec", "frame_ids", "augmentation_key", "workload_version", "rng_scheme_version")
    for occurrence in sorted(left.keys() & right.keys()):
        for field in identity_fields:
            if left[occurrence].get(field) != right[occurrence].get(field):
                label = {"step_id": "moved across step", "augmentation_key": "RNG key mismatch",
                         "microbatch_id": "microbatch membership mismatch", "workload_version": "workload version mismatch"}.get(field, f"{field} mismatch")
                errors.append(f"{label} for occurrence {occurrence}")
    if not errors and manifest_hash(reference) != manifest_hash(candidate):
        errors.append("manifest identity mismatch")
    return errors
