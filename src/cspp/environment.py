"""Best-effort host environment probe with explicit UNKNOWN reasons."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _command(args: list[str], timeout: float = 8.0) -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if result.returncode:
        return None, result.stderr.strip() or f"exit code {result.returncode}"
    return result.stdout.strip(), None


def _powershell_json(expression: str) -> tuple[Any, str | None]:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if not executable:
        return None, "PowerShell is unavailable"
    output, error = _command([executable, "-NoProfile", "-Command", expression])
    if error:
        return None, error
    try:
        return json.loads(output or "null"), None
    except json.JSONDecodeError as exc:
        return None, f"PowerShell returned invalid JSON: {exc}"


def _git_value(*args: str) -> tuple[str | None, str | None]:
    return _command(["git", *args])


def probe_environment(workspace: str | Path = ".") -> dict[str, Any]:
    root = Path(workspace).resolve()
    cpu_count = os.cpu_count()
    physical = None
    cpu_model = platform.processor() or None
    ram = None
    filesystem = None
    if os.name == "nt":
        comp, comp_error = _powershell_json(
            "Get-CimInstance Win32_ComputerSystem | Select-Object @{N='TotalPhysicalMemory';E={$_.TotalPhysicalMemory}} | ConvertTo-Json -Compress"
        )
        if comp and isinstance(comp, dict):
            ram = comp.get("TotalPhysicalMemory")
        proc, proc_error = _powershell_json(
            "Get-CimInstance Win32_Processor | Select-Object -First 1 Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json -Compress"
        )
        if isinstance(proc, dict):
            cpu_model = proc.get("Name") or cpu_model
            physical = proc.get("NumberOfCores")
            cpu_count = proc.get("NumberOfLogicalProcessors") or cpu_count
        volume, volume_error = _powershell_json(
            f"$d=(Get-Item -LiteralPath '{str(root).replace("'", "''")}').PSDrive.Name; Get-Volume -DriveLetter $d | Select-Object -First 1 FileSystem,SizeRemaining | ConvertTo-Json -Compress"
        )
        if isinstance(volume, dict):
            filesystem = volume.get("FileSystem")
        else:
            volume_error = volume_error or "volume filesystem query returned no value"
        ram_reason = comp_error
        physical_reason = proc_error or "physical core count unavailable"
        filesystem_reason = volume_error
    else:
        ram_reason = "platform-specific RAM query not configured"
        physical_reason = "physical core count is not available from the standard library"
        filesystem_reason = "filesystem type query is not available from the standard library"
    disk = shutil.disk_usage(root)
    nvidia, nvidia_error = _command(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    gpu_rows = []
    if nvidia:
        for row in nvidia.splitlines():
            parts = [part.strip() for part in row.split(",", 1)]
            if len(parts) == 2:
                gpu_rows.append({"model": parts[0], "vram_mib": int(parts[1])})
    try:
        import torch  # type: ignore[import-not-found]

        torch_version: str = torch.__version__
        cuda_available: bool | str = bool(torch.cuda.is_available())
        cuda_runtime: str | None = torch.version.cuda
        if cuda_available and not gpu_rows:
            gpu_rows = [
                {"model": torch.cuda.get_device_name(i), "vram_bytes": torch.cuda.get_device_properties(i).total_memory}
                for i in range(torch.cuda.device_count())
            ]
    except ImportError:
        torch_version, cuda_available, cuda_runtime = "UNKNOWN", "UNKNOWN", "UNKNOWN"
        torch_reason = "PyTorch is not installed (optional at M0–M2)"
    else:
        torch_reason = None
    ffmpeg, ffmpeg_error = _command(["ffmpeg", "-version"])
    ffmpeg_version = ffmpeg.splitlines()[0] if ffmpeg else "UNKNOWN"
    git_root, git_root_error = _git_value("rev-parse", "--show-toplevel")
    git_commit, git_error = _git_value("rev-parse", "HEAD")
    git_branch, branch_error = _git_value("branch", "--show-current")
    git_remote, remote_error = _git_value("config", "--get", "remote.origin.url")
    git_status, status_error = _git_value("status", "--porcelain")
    if git_root is not None and git_commit is None:
        git_commit = "PRE-COMMIT"
        git_error = "Git worktree exists but has no HEAD commit at probe time"
    return {
        "schema_version": "1.0",
        "captured_at_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "os": {"system": platform.system(), "release": platform.release(), "version": platform.version(), "machine": platform.machine()},
        "kernel": platform.release(),
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "cpu": {"model": cpu_model or "UNKNOWN", "logical_cores": cpu_count or "UNKNOWN", "physical_cores": physical or "UNKNOWN", "physical_cores_reason": physical_reason},
        "ram_total_bytes": ram if isinstance(ram, int) and ram > 0 else "UNKNOWN",
        "ram_reason": None if isinstance(ram, int) and ram > 0 else (ram_reason or "host query unavailable"),
        "gpu": {"count": len(gpu_rows) if nvidia is not None or cuda_available is True else "UNKNOWN", "devices": gpu_rows, "query_reason": nvidia_error if not gpu_rows else None},
        "cuda_available": cuda_available,
        "pytorch_version": torch_version,
        "pytorch_reason": torch_reason,
        "cuda_runtime_version": cuda_runtime or "UNKNOWN",
        "ffmpeg": {"available": ffmpeg is not None, "version": ffmpeg_version, "reason": ffmpeg_error},
        "storage": {"path": str(root), "filesystem": filesystem or "UNKNOWN", "filesystem_reason": filesystem_reason, "free_bytes": disk.free, "total_bytes": disk.total},
        "git": {"available": git_root is not None, "root": git_root or "UNKNOWN",
                "root_reason": git_root_error, "commit": git_commit or "UNKNOWN", "commit_reason": git_error,
                "branch": git_branch or "UNKNOWN", "branch_reason": branch_error,
                "origin": git_remote or "UNKNOWN", "origin_reason": remote_error,
                "dirty": (bool(git_status) if git_status is not None else "UNKNOWN"),
                "status_reason": status_error},
    }


def write_environment_artifacts(root: str | Path = ".") -> dict[str, Any]:
    root = Path(root)
    data = probe_environment(root)
    target = root / "artifacts" / "environment.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = ["# Environment Summary", "", f"- OS: {data['os']['system']} {data['os']['release']}",
               f"- Python: {data['python']['version']}", f"- CPU: {data['cpu']['model']} ({data['cpu']['logical_cores']} logical / {data['cpu']['physical_cores']} physical)",
               f"- RAM bytes: {data['ram_total_bytes']}", f"- GPUs: {data['gpu']['count']} {data['gpu']['devices']}",
               f"- CUDA available/runtime: {data['cuda_available']} / {data['cuda_runtime_version']}",
               f"- PyTorch: {data['pytorch_version']}", f"- FFmpeg: {data['ffmpeg']['version']}",
               f"- Storage: {data['storage']['filesystem']} with {data['storage']['free_bytes']} free bytes",
               f"- Git root/branch: {data['git']['root']} / {data['git']['branch']}",
               f"- Git commit/dirty: {data['git']['commit']} / {data['git']['dirty']}",
               f"- Git origin: {data['git']['origin']}"]
    (root / "artifacts" / "environment_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return data
