import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _workspace(tmp_path, source_root):
    for directory in ("configs", "data/manifests", "artifacts"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_root / "configs/m0_m2.local.yaml", tmp_path / "configs/m0_m2.local.yaml")
    shutil.copy2(source_root / "data/manifests/e1.jsonl", tmp_path / "data/manifests/e1.jsonl")
    return tmp_path


def _run(root, source_root, *args):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "cspp.cli", *args], cwd=root, env=env, capture_output=True, text=True)


def test_documented_cli_acceptance_commands(root, tmp_path):
    workspace = _workspace(tmp_path, root)
    doctor = _run(workspace, root, "doctor", "--config", "configs/m0_m2.local.yaml")
    assert doctor.returncode == 0, doctor.stderr
    env = json.loads((workspace / "artifacts/environment.json").read_text(encoding="utf-8"))
    required = {"os", "kernel", "python", "cpu", "ram_total_bytes", "gpu", "cuda_available",
                "pytorch_version", "cuda_runtime_version", "ffmpeg", "storage", "git"}
    assert required <= env.keys()

    config = _run(workspace, root, "validate-config", "--config", "configs/m0_m2.local.yaml")
    assert config.returncode == 0, config.stderr
    manifest = _run(workspace, root, "validate-manifest", "--manifest", "data/manifests/e1.jsonl")
    assert manifest.returncode == 0, manifest.stderr
    legality = _run(workspace, root, "legality-check", "--config", "configs/m0_m2.local.yaml")
    assert legality.returncode == 0, legality.stderr
    smoke = _run(workspace, root, "smoke", "--config", "configs/m0_m2.local.yaml", "--run-id", "integration-smoke")
    assert smoke.returncode == 0, smoke.stderr
    run_path = workspace / "artifacts/runs/integration-smoke"
    validation = json.loads((run_path / "trace_validation.json").read_text())
    assert validation["valid"] is True
    assert json.loads((run_path / "run_manifest.json").read_text())["status"] == "VALID"
    validate = _run(workspace, root, "validate-trace", "--run", "integration-smoke", "--config", "configs/m0_m2.local.yaml")
    assert validate.returncode == 0, validate.stderr
    export = _run(workspace, root, "audit-export", "--run", "integration-smoke", "--out", "artifacts/audits/export")
    assert export.returncode == 0, export.stderr
    assert (workspace / "artifacts/audits/export/trace.jsonl").is_file()


def test_trace_disabled_fails_closed(root, tmp_path):
    workspace = _workspace(tmp_path, root)
    config = workspace / "configs/m0_m2.local.yaml"
    config.write_text(config.read_text().replace("enabled: true", "enabled: false"), encoding="utf-8")
    result = _run(workspace, root, "validate-config", "--config", "configs/m0_m2.local.yaml")
    assert result.returncode != 0
    assert "trace.enabled=false is unsupported" in result.stderr


def test_failed_smoke_retains_invalid_run_provenance(root, tmp_path):
    workspace = _workspace(tmp_path, root)
    config = workspace / "configs/m0_m2.local.yaml"
    config.write_text(config.read_text().replace("enabled: true", "enabled: false"), encoding="utf-8")
    result = _run(workspace, root, "smoke", "--config", "configs/m0_m2.local.yaml", "--run-id", "failed-preflight")
    assert result.returncode != 0
    manifest = json.loads((workspace / "artifacts/runs/failed-preflight/run_manifest.json").read_text())
    assert manifest["status"] == "INVALID"
    assert manifest["failure_reason"]
    assert manifest["config_hash"] != "UNKNOWN"


def test_smoke_without_run_id_creates_unique_run(root, tmp_path):
    workspace = _workspace(tmp_path, root)
    result = _run(workspace, root, "smoke", "--config", "configs/m0_m2.local.yaml")
    assert result.returncode == 0, result.stderr
    run_id = json.loads(result.stdout)["run_id"]
    assert run_id.startswith("m2-smoke-")
    manifest = json.loads((workspace / "artifacts/runs" / run_id / "run_manifest.json").read_text())
    assert manifest["status"] == "VALID"


def test_m3_commands_fail_closed_outside_scope(root, tmp_path):
    workspace = _workspace(tmp_path, root)
    result = _run(workspace, root, "calibrate-input", "--config", "configs/m0_m2.local.yaml")
    assert result.returncode != 0
    assert "outside the authorized M0–M2 scope" in result.stderr
