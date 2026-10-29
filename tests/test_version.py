"""Release version consistency: one source of truth, checked everywhere.

The version must agree across the package, the packaging metadata, the FastAPI app,
`/health`, the rendered page, the README and the CHANGELOG. A mismatch anywhere is a
release defect, not a cosmetic one: a public deployment that claims a version it does
not run is a false statement.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from pr_guardian.version import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_version_is_a_release_string():
    # PEP 440: a pre-release keeps the numeric core and appends rcN
    assert re.fullmatch(r"\d+\.\d+\.\d+(rc\d+)?", __version__), __version__
    assert __version__ == "1.1.0rc1"


def test_pyproject_takes_its_version_from_the_same_file():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in data["project"], "a static version would drift"
    assert "version" in data["project"]["dynamic"]
    hatch = data["tool"]["hatch"]["version"]
    assert hatch["path"] == "src/pr_guardian/version.py"


def test_fastapi_app_version_matches():
    assert app.version == __version__


def test_health_reports_the_running_version():
    client = TestClient(app)
    payload = client.get("/health").json()
    assert payload["version"] == __version__


def test_page_footer_reports_the_running_version():
    client = TestClient(app)
    html = client.get("/").text
    assert f"PR Guardian v{__version__}" in html


def test_readme_quotes_the_release():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"v{__version__}" in readme or f"**{__version__}**" in readme


def test_changelog_has_an_entry_for_the_release():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"[{__version__}]" in changelog


def test_no_hardcoded_stale_versions_in_shipped_code():
    """The old 0.1.0 / 0.2.0 strings must not survive in code paths."""
    offenders = []
    for path in list((ROOT / "app").rglob("*.py")) + list((ROOT / "app").rglob("*.html")) \
            + list((ROOT / "src").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for stale in ("0.1.0", "0.2.0"):
            if stale in text:
                offenders.append(f"{path.relative_to(ROOT)}: {stale}")
    assert offenders == [], offenders


def test_installed_package_metadata_matches():
    """`pip install -e .` must expose the same version as the source file."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        installed = version("pr-guardian")
    except PackageNotFoundError:
        pytest.skip("package is not installed in this environment")
    assert installed == __version__
