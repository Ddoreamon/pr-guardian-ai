"""Run pytest in a TRUSTED, locally-authored repository and summarise the result.

Safety: this must only be pointed at fixtures we wrote ourselves. It is NOT a
sandbox. Never run it on untrusted/external PR code. Timeouts limit runtime only.

Honesty rule: if pytest could not actually run (missing plugin, collection error,
bad usage), the result is reported as ``ran=False`` with a reason. It is never
reported as "0 passed, 0 failed", which would look like a clean run.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, Field

PYTEST_TIMEOUT_SECONDS = 60

# pytest exit codes: 0 = ok, 1 = tests failed, 2 = interrupted, 3 = internal error,
# 4 = usage error, 5 = no tests collected.
_CODES_THAT_MEAN_TESTS_RAN = {0, 1}
_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|error|errors)")


class TestResult(BaseModel):
    ran: bool = False
    passed: int = 0
    failed: int = 0
    errors: int = 0
    failed_tests: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    timed_out: bool = False
    exit_code: int | None = None
    reason: str = ""


def _failure_reason(returncode: int | None, stdout: str, stderr: str) -> str:
    """Explain why pytest produced no usable result, or '' when it did run."""
    combined = f"{stdout}\n{stderr}"
    if "No module named pytest" in combined:
        return "pytest is not installed in this environment"
    if "unrecognized arguments: --json-report" in combined:
        return "pytest-json-report plugin is not installed"
    if "no tests ran" in combined.lower():
        return "pytest collected no tests"
    if returncode == 4:
        return "pytest rejected its arguments (usage error)"
    if returncode == 5:
        return "pytest collected no tests"
    if returncode is None:
        return "pytest did not start"
    return f"pytest produced no usable summary (exit code {returncode})"


def run_pytest_on_worktree(worktree: Path) -> TestResult:
    """Run pytest against a checked-out head worktree of a trusted repo."""
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        cmd = [
            sys.executable, "-m", "pytest", "-q", "--disable-warnings",
            "-p", "no:cacheprovider", "--json-report", f"--json-report-file={report}",
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=str(worktree), capture_output=True,
                timeout=PYTEST_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired:
            return TestResult(ran=False, timed_out=True, reason="pytest timed out",
                              duration_seconds=round(time.monotonic() - start, 3))

        stdout = proc.stdout.decode("utf-8", "replace")
        stderr = proc.stderr.decode("utf-8", "replace")
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
            # A report with no counts at all means nothing was collected.
            if (result.passed + result.failed + result.errors) == 0:
                result.ran = False
                result.reason = "pytest collected no tests"
            else:
                result.ran = True
            return result

        # No JSON report: fall back to the plain summary line, but only trust it
        # when pytest clearly executed tests.
        result = _parse_plain_output(stdout, result)
        counted = result.passed + result.failed + result.errors
        if counted > 0 or proc.returncode in _CODES_THAT_MEAN_TESTS_RAN:
            result.ran = True
            return result

        result.ran = False
        result.reason = _failure_reason(proc.returncode, stdout, stderr)
        return result


def _parse_plain_output(text: str, result: TestResult) -> TestResult:
    """Parse pytest's summary line, e.g. '1 failed, 2 passed in 0.10s'."""
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    for count, label in _SUMMARY_RE.findall(last):
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
