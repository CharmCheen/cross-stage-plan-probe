from copy import deepcopy

from cspp.legality import compare_plan_legality, legality_report
from cspp.manifest import keyed_u64, manifest_hash, validate_manifest


def test_keyed_rng_is_reorder_independent_and_occurrence_specific(records):
    seed = 17
    forward = {r["occurrence_id"]: keyed_u64(seed, r) for r in records}
    reverse = {r["occurrence_id"]: keyed_u64(seed, r) for r in reversed(records)}
    assert forward == reverse
    assert len(set(forward.values())) == len(records)


def test_manifest_hash_stable_under_record_execution_reordering(records):
    assert manifest_hash(records) == manifest_hash(list(reversed(records)))


def test_roundtrip_and_legality_report_stable(tmp_path, records):
    from cspp.manifest import load_manifest, write_manifest

    path = tmp_path / "manifest.jsonl"
    write_manifest(path, records)
    loaded = load_manifest(path)
    assert manifest_hash(records) == manifest_hash(loaded)
    assert legality_report(records, 9) == legality_report(loaded, 9)


def test_deliberate_duplicate_occurrence_fails(records):
    invalid = deepcopy(records)
    invalid.append(deepcopy(records[0]))
    assert any("duplicate occurrence_id" in error for error in validate_manifest(invalid))


def test_missing_occurrence_fails_cross_plan_check(records):
    assert any("missing occurrence" in error for error in compare_plan_legality(records, records[:-1]))


def test_sample_moved_across_step_fails(records):
    moved = deepcopy(records)
    moved[0]["step_id"] = 2
    assert any("moved across step" in error for error in compare_plan_legality(records, moved))


def test_rng_key_mismatch_fails(records):
    changed = deepcopy(records)
    changed[1]["augmentation_key"] = "different-draw"
    assert any("RNG key mismatch" in error for error in compare_plan_legality(records, changed))


def test_microbatch_and_version_mismatch_fail(records):
    changed = deepcopy(records)
    changed[0]["microbatch_id"] = "other"
    changed[1]["workload_version"] = "fixture-v2"
    errors = compare_plan_legality(records, changed)
    assert any("microbatch membership mismatch" in error for error in errors)
    assert any("workload version mismatch" in error for error in errors)


def test_reordered_execution_semantics_are_legal(records):
    assert compare_plan_legality(records, list(reversed(records))) == []
