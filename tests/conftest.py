"""Shared fixtures: builds the demo git repository once per test session."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_fixtures", ROOT / "demo" / "build_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def demo_repo(tmp_path_factory) -> Path:
    repo = tmp_path_factory.mktemp("demo") / "demo-repo"
    _load_builder().build(repo)
    return repo
