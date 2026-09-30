"""Fail-closed config loading for the M0–M2 instrumentation fixture."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised for an invalid or unsupported config option."""


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load config {config_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError("config root must be a mapping")
    validate_config(value)
    return value


def validate_config(config: dict[str, Any]) -> None:
    expected_sections = {"experiment", "resources", "manifest", "fixture", "trace", "execution"}
    if set(config) != expected_sections:
        raise ConfigError(f"config sections must be exactly {sorted(expected_sections)}")
    for section in expected_sections:
        if not isinstance(config.get(section), dict):
            raise ConfigError(f"missing config section: {section}")
    experiment = config["experiment"]
    if set(experiment) != {"name", "mode", "seed", "workload_version"}:
        raise ConfigError("experiment contains missing or unsupported fields")
    for key in ("name", "workload_version"):
        if not isinstance(experiment.get(key), str) or not experiment[key].strip():
            raise ConfigError(f"experiment.{key} must be a non-empty string")
    if not isinstance(experiment.get("seed"), int) or isinstance(experiment["seed"], bool):
        raise ConfigError("experiment.seed must be an integer")
    if experiment.get("mode") != "instrumentation_fixture":
        raise ConfigError("only experiment.mode=instrumentation_fixture is supported")
    resources = config["resources"]
    expected = {"cpu_threads", "host_memory_budget_bytes", "pinned_memory_budget_bytes", "gpu_memory_budget_bytes", "queue_byte_budget"}
    if set(resources) != expected:
        raise ConfigError(f"resources fields must be exactly {sorted(expected)}")
    for key in expected:
        value = resources[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"resources.{key} must be a non-negative integer")
    if resources["cpu_threads"] < 1 or resources["queue_byte_budget"] < 1:
        raise ConfigError("cpu_threads and queue_byte_budget must be positive")
    if resources["queue_byte_budget"] > resources["host_memory_budget_bytes"]:
        raise ConfigError("queue_byte_budget cannot exceed host memory budget")
    if set(config["manifest"]) != {"path"} or not isinstance(config["manifest"].get("path"), str) or not config["manifest"]["path"]:
        raise ConfigError("manifest.path is required and is the only supported manifest option")
    fixture = config["fixture"]
    if set(fixture) != {"object_sizes", "producer_delays_ms", "consumer_delays_ms", "ready_stage", "deterministic_order"}:
        raise ConfigError("fixture contains missing or unsupported fields")
    sizes = fixture.get("object_sizes")
    producer_delays = fixture.get("producer_delays_ms")
    consumer_delays = fixture.get("consumer_delays_ms")
    if not isinstance(sizes, list) or not sizes or any(not isinstance(n, int) or isinstance(n, bool) or n <= 0 for n in sizes):
        raise ConfigError("fixture.object_sizes must be a non-empty list of positive integers")
    for key, values in (("producer_delays_ms", producer_delays), ("consumer_delays_ms", consumer_delays)):
        if not isinstance(values, list) or len(values) != len(sizes):
            raise ConfigError(f"fixture.{key} must have one value per object")
        if any(not isinstance(n, (int, float)) or isinstance(n, bool) or not math.isfinite(n) or n < 0 for n in values):
            raise ConfigError(f"fixture.{key} values must be finite and non-negative")
    if any(n > resources["queue_byte_budget"] for n in sizes):
        raise ConfigError("fixture object cannot exceed queue byte budget")
    if fixture["ready_stage"] != "consumer_input_complete":
        raise ConfigError("unsupported fixture.ready_stage")
    if fixture["deterministic_order"] is not True:
        raise ConfigError("only deterministic_order=true is supported by the M0–M2 fixture")
    if set(config["trace"]) != {"enabled", "format", "level"}:
        raise ConfigError("trace contains missing or unsupported fields")
    if config["trace"].get("enabled") is not True:
        raise ConfigError("trace.enabled=false is unsupported: full trace is required for this command")
    if config["trace"].get("format") != "jsonl" or config["trace"].get("level") != "full":
        raise ConfigError("only trace.format=jsonl and trace.level=full are supported")
    execution = config["execution"]
    if set(execution) != {"input_plan_id", "training_plan_id"}:
        raise ConfigError("execution must contain input_plan_id and training_plan_id only")
    for key in execution:
        if not isinstance(execution[key], str) or not execution[key].strip():
            raise ConfigError(f"execution.{key} must be a non-empty string")


def validate_config_manifest(config: dict[str, Any], records: list[dict[str, Any]]) -> None:
    versions = {r.get("workload_version") for r in records}
    if versions != {config["experiment"]["workload_version"]}:
        raise ConfigError("config workload_version does not match frozen manifest")
    if len(records) != len(config["fixture"]["object_sizes"]):
        raise ConfigError("fixture object count does not match manifest occurrences")
