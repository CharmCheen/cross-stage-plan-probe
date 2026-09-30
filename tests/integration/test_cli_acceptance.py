import json
import subprocess
import sys


def run(*args):
    return subprocess.run([sys.executable, "-m", "cspp.cli", *args], capture_output=True, text=True)


def test_documented_cli_acceptance_commands(root):
    doctor = run("doctor", "--config", "configs/m0_m2.local.yaml")
    assert doctor.returncode == 0, doctor.stderr
    env = json.loads((root / "artifacts/environment.json").read_text(encoding="utf-8"))
    required = {"os", "kernel", "python", "cpu", "ram_total_bytes", "gpu", "cuda_available",
                "pytorch_version", "cuda_runtime_version", "ffmpeg", "storage", "git"}
    assert required <= env.keys()

    config = run("validate-config", "--config", "configs/m0_m2.local.yaml")
    assert config.returncode == 0, config.stderr
    manifest = run("validate-manifest", "--manifest", "data/manifests/e1.jsonl")
    assert manifest.returncode == 0, manifest.stderr
    legality = run("legality-check", "--config", "configs/m0_m2.local.yaml")
    assert legality.returncode == 0, legality.stderr
    smoke = run("smoke", "--config", "configs/m0_m2.local.yaml", "--run-id", "integration-smoke")
    assert smoke.returncode == 0, smoke.stderr
    validation = json.loads((root / "artifacts/runs/integration-smoke/trace_validation.json").read_text())
    assert validation["valid"] is True
    assert validation["final_live_bytes"] == {"gpu": 0, "host": 0, "pinned": 0}
    overhead = json.loads((root / "artifacts/milestones/M2/tracing_overhead.json").read_text())
    assert len(overhead["tracing_on_wall_ns"]) == len(overhead["tracing_off_wall_ns"]) == 3


def test_m3_commands_fail_closed_outside_scope():
    result = run("calibrate-input", "--config", "configs/m0_m2.local.yaml")
    assert result.returncode != 0
    assert "outside the authorized M0–M2 scope" in result.stderr
