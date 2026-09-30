"""Config loading and validation for the local M0–M2 fixture."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised for an invalid or incomplete config."""


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
    for section in ("experiment", "resources", "manifest", "fixture", "trace"):
        if not isinstance(config.get(section), dict):
            raise ConfigError(f"missing config section: {section}")
    experiment = config["experiment"]
    if not experiment.get("name") or not experiment.get("workload_version"):
        raise ConfigError("experiment.name and experiment.workload_version are required")
    if not isinstance(experiment.get("seed"), int):
        raise ConfigError("experiment.seed must be an integer")
    resources = config["resources"]
    for key in ("cpu_threads", "host_memory_budget_bytes", "queue_byte_budget"):
        if not isinstance(resources.get(key), int) or resources[key] < 0:
            raise ConfigError(f"resources.{key} must be a non-negative integer")
    if resources["cpu_threads"] < 1 or resources["queue_byte_budget"] < 1:
        raise ConfigError("cpu_threads and queue_byte_budget must be positive")
    fixture = config["fixture"]
    sizes = fixture.get("object_sizes")
    producer_delays = fixture.get("producer_delays_ms")
    consumer_delays = fixture.get("consumer_delays_ms")
    if not isinstance(sizes, list) or not sizes or any(not isinstance(n, int) or n <= 0 for n in sizes):
        raise ConfigError("fixture.object_sizes must be a non-empty list of positive integers")
    for key, values in (("producer_delays_ms", producer_delays), ("consumer_delays_ms", consumer_delays)):
        if not isinstance(values, list) or len(values) != len(sizes):
            raise ConfigError(f"fixture.{key} must have one value per object")
        if any(not isinstance(n, (int, float)) or n < 0 for n in values):
            raise ConfigError(f"fixture.{key} values must be non-negative")
    if not isinstance(config["trace"].get("enabled"), bool):
        raise ConfigError("trace.enabled must be boolean")
