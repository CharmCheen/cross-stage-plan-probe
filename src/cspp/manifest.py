"""Canonical occurrence manifests and order-independent keyed augmentation decisions."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Iterable

REQUIRED_FIELDS = {
    "sample_id", "occurrence_id", "step_id", "microbatch_id", "sample_order_baseline",
    "frame_selection_spec", "frame_ids", "augmentation_key", "workload_version", "weight",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def manifest_hash(records: Iterable[dict[str, Any]]) -> str:
    normalized = sorted((dict(record) for record in records), key=lambda r: r["occurrence_id"])
    return hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()


def load_manifest(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read manifest {path}: {exc}") from exc
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"manifest line {line_no} is invalid JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"manifest line {line_no} must be an object")
        records.append(record)
    if not records:
        raise ValueError("manifest has no occurrences")
    return records


def write_manifest(path: str | Path, records: Iterable[dict[str, Any]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(canonical_json(record) + "\n")


def keyed_u64(seed: int, record: dict[str, Any], purpose: str = "augmentation") -> int:
    """Return a stable 64-bit draw keyed by occurrence, independent of execution order."""
    material = "\0".join((str(seed), record["workload_version"], record["sample_id"],
                           record["occurrence_id"], record["augmentation_key"], purpose))
    digest = hmac.new(str(seed).encode("ascii"), material.encode("utf-8"), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big")


def validate_manifest(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        prefix = f"record[{index}]"
        missing = REQUIRED_FIELDS - record.keys()
        if missing:
            errors.append(f"{prefix}: missing fields {sorted(missing)}")
            continue
        oid = record["occurrence_id"]
        if not isinstance(oid, str) or not oid:
            errors.append(f"{prefix}: occurrence_id must be a non-empty string")
        elif oid in seen:
            errors.append(f"duplicate occurrence_id: {oid}")
        seen.add(oid)
        if not isinstance(record["sample_id"], str) or not record["sample_id"]:
            errors.append(f"{prefix}: sample_id must be a non-empty string")
        if not isinstance(record["step_id"], int) or record["step_id"] < 0:
            errors.append(f"{prefix}: step_id must be a non-negative integer")
        if not isinstance(record["microbatch_id"], str) or not record["microbatch_id"]:
            errors.append(f"{prefix}: microbatch_id must be a non-empty string")
        if not isinstance(record["frame_ids"], list):
            errors.append(f"{prefix}: frame_ids must be a list")
        if not isinstance(record["weight"], (int, float)) or record["weight"] <= 0:
            errors.append(f"{prefix}: weight must be positive")
    return errors


def compare_manifests(reference: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> list[str]:
    """Check strict work identity across plans with actionable mismatch categories."""
    errors = validate_manifest(reference) + validate_manifest(candidate)
    if errors:
        return errors
    ref = {r["occurrence_id"]: r for r in reference}
    got = {r["occurrence_id"]: r for r in candidate}
    for oid in sorted(ref.keys() - got.keys()):
        errors.append(f"missing occurrence: {oid}")
    for oid in sorted(got.keys() - ref.keys()):
        errors.append(f"unexpected occurrence: {oid}")
    fields = {
        "sample_id": "sample identity mismatch",
        "step_id": "sample moved across step",
        "microbatch_id": "microbatch membership mismatch",
        "sample_order_baseline": "baseline ordering mismatch",
        "frame_selection_spec": "frame selection specification mismatch",
        "frame_ids": "frame IDs mismatch",
        "augmentation_key": "RNG key mismatch",
        "workload_version": "workload version mismatch",
        "weight": "sample weight mismatch",
    }
    for oid in sorted(ref.keys() & got.keys()):
        for field, message in fields.items():
            if ref[oid].get(field) != got[oid].get(field):
                errors.append(f"{message}: {oid} ({field})")
    if not errors and manifest_hash(reference) != manifest_hash(candidate):
        errors.append("manifest mismatch")
    return errors
