"""Run pytest in a TRUSTED, locally-authored repository and summarise the result.

Safety: this must only be pointed at fixtures we wrote ourselves. It is NOT a
sandbox. Never run it on untrusted/external PR code. Timeouts limit runtime only.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, Field

PYTEST_TIMEOUT_SECONDS = 60


class TestResult(BaseModel):
    passed: int = 0
    failed: int = 0
    errors: int = 0
    failed_tests: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    timed_out: bool = False
    exit_code: int | None = None


def run_pytest_on_worktree(worktree: Path) -> TestResult:
    """Run pytest against a checked-out head worktree of a trusted repo."""
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        cmd = [
            sys.executable, "-m", "pytest", "-q", "--disable-warnings",
            "-p", "no:cacheprovider", "--json-report", f"--json-report-file={report}",
        ]
        # fall back to plain junit-less parsing if pytest-json-report is missing
        try:
            proc = subprocess.run(
                cmd, cwd=str(worktree), capture_output=True,
                timeout=PYTEST_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired:
            return TestResult(timed_out=True, duration_seconds=time.monotonic() - start)

        result = TestResult(exit_code=proc.returncode,
                            duration_seconds=round(time.monotonic() - start, 3))
        if report.exists():
            data = json.loads(report.read_text(encoding="utf-8"))
            summary = data.get("summary", {})
            result.passed = summary.get("passed", 0)
            result.failed = summary.get("failed", 0)
            result.errors = summary.get("error", 0)
            result.failed_tests = [
                t["nodeid"] for t in data.get("tests", []) if t.get("outcome") in ("failed", "error")
            ]
        else:
            result = _parse_plain_output(proc.stdout.decode("utf-8", "replace"), result)
        return result


def _parse_plain_output(text: str, result: TestResult) -> TestResult:
    """Parse the final pytest summary line, e.g. '1 failed, 2 passed in 0.10s'."""
    import re

    last = text.strip().splitlines()[-1] if text.strip() else ""
    for count, label in re.findall(r"(\d+) (passed|failed|error)", last):
        if label == "passed":
            result.passed = int(count)
        elif label == "failed":
            result.failed = int(count)
        else:
            result.errors = int(count)
    result.failed_tests = [
        line.split("FAILED ", 1)[1].split(" - ")[0]
        for line in text.splitlines() if line.startswith("FAILED ")
    ]
    return result
