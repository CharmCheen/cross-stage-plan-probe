from pathlib import Path

import pytest

from cspp.config import load_config
from cspp.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def root():
    return ROOT


@pytest.fixture
def config():
    return load_config(ROOT / "configs" / "m0_m2.local.yaml")


@pytest.fixture
def records():
    return load_manifest(ROOT / "data" / "manifests" / "e1.jsonl")
