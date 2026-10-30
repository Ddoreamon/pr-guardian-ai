"""The one-command local launchers must stay local, opt-in and honest.

These are static checks: the launchers start a real server, so the suite asserts their
contract rather than launching uvicorn inside pytest. `bash -n` runs on Linux CI.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BAT = ROOT / "start-local.bat"
SH = ROOT / "start-local.sh"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_launchers_exist():
    assert BAT.is_file()
    assert SH.is_file()


@pytest.mark.parametrize("path", [BAT, SH])
def test_launchers_bind_loopback_only(path):
    text = _text(path)
    assert "--host 127.0.0.1" in text
    assert "--host 0.0.0.0" not in text
    assert "127.0.0.1:" in text or "127.0.0.1:${PORT}" in text


@pytest.mark.parametrize("path", [BAT, SH])
def test_launchers_enable_uploads_but_not_ai_or_public(path):
    text = _text(path)
    assert "PR_GUARDIAN_UPLOAD_ENABLED=1" in text
    assert "PR_GUARDIAN_UPLOAD_AI=0" in text
    assert "PR_GUARDIAN_PUBLIC_MODE=0" in text
    assert "PR_GUARDIAN_ALLOW_LIVE_LLM=0" in text


@pytest.mark.parametrize("path", [BAT, SH])
def test_launchers_ask_before_installing_anything(path):
    text = _text(path)
    assert "Install the project dependencies now?" in text
    assert "pip install -e" in text
    # the prompt must default to no
    assert "[y/N]" in text


@pytest.mark.parametrize("path", [BAT, SH])
def test_launchers_do_not_print_secrets(path):
    text = _text(path).lower()
    for forbidden in ("api_key=", "authorization", "bearer"):
        assert forbidden not in text


def test_env_checker_reports_ready_here():
    proc = subprocess.run(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "scripts/check_env.py"]
        if (ROOT / ".venv" / "Scripts" / "python.exe").exists() else ["python", "scripts/check_env.py"],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_shell_script_is_syntactically_valid():
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available on this platform")
    proc = subprocess.run([bash, "-n", str(SH)], capture_output=True, text=True,
                          check=False)
    assert proc.returncode == 0, proc.stderr


def test_shell_script_uses_lf_endings():
    assert b"\r\n" not in SH.read_bytes(), "a CRLF shell script fails on Linux"


def test_gitattributes_pins_launcher_line_endings():
    """A .bat must reach Windows with CRLF and a .sh must reach Linux with LF."""
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat text eol=crlf" in attrs
    assert "*.sh  text eol=lf" in attrs or "*.sh text eol=lf" in attrs
