import json
import subprocess
import sys

from cspp import __version__
from cspp.config import ConfigError, load_config
from cspp.environment import probe_environment
from cspp.manifest import load_manifest, validate_manifest


def test_package_import_and_config(root):
    assert __version__
    config = load_config(root / "configs" / "m0_m2.local.yaml")
    assert config["experiment"]["workload_version"] == "fixture-v1"


def test_config_rejects_invalid_queue_budget(tmp_path, root):
    source = (root / "configs" / "m0_m2.local.yaml").read_text(encoding="utf-8")
    bad = tmp_path / "bad.yaml"
    bad.write_text(source.replace("queue_byte_budget: 100", "queue_byte_budget: 0"), encoding="utf-8")
    try:
        load_config(bad)
    except ConfigError as exc:
        assert "queue_byte_budget" in str(exc)
    else:
        raise AssertionError("invalid config unexpectedly loaded")


def test_environment_probe_has_required_keys():
    data = probe_environment()
    assert {"os", "kernel", "python", "cpu", "ram_total_bytes", "gpu", "cuda_available",
            "pytorch_version", "cuda_runtime_version", "ffmpeg", "storage", "git"} <= data.keys()
    assert data["python"]["version"]
    assert data["storage"]["free_bytes"] >= 0


def test_manifest_fixture_schema(root):
    records = load_manifest(root / "data" / "manifests" / "e1.jsonl")
    assert validate_manifest(records) == []
    assert records[0]["sample_id"] == records[1]["sample_id"]
    assert records[0]["occurrence_id"] != records[1]["occurrence_id"]


def test_cli_smoke_help():
    result = subprocess.run([sys.executable, "-m", "cspp.cli", "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "doctor" in result.stdout and "smoke" in result.stdout
