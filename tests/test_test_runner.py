"""Tests for pytest summary interpretation.

The important property: a run that did not execute tests must never be reported
as "0 passed, 0 failed", which would look like a clean run.
"""

from pr_guardian.test_runner import TestResult as RunResult
from pr_guardian.test_runner import _failure_reason, _parse_plain_output


def test_parses_summary_line():
    result = _parse_plain_output("1 failed, 2 passed in 0.10s", RunResult())
    assert (result.passed, result.failed, result.errors) == (2, 1, 0)


def test_parses_failed_test_names():
    text = "FAILED tests/test_x.py::test_a - AssertionError\n1 failed in 0.01s"
    result = _parse_plain_output(text, RunResult())
    assert result.failed == 1
    assert result.failed_tests == ["tests/test_x.py::test_a"]


def test_missing_pytest_is_explained():
    reason = _failure_reason(1, "", "No module named pytest")
    assert "not installed" in reason


def test_missing_json_report_plugin_is_explained():
    reason = _failure_reason(4, "", "error: unrecognized arguments: --json-report")
    assert "pytest-json-report" in reason


def test_no_tests_collected_is_explained():
    assert "collected no tests" in _failure_reason(5, "no tests ran in 0.01s", "")


def test_usage_error_is_explained():
    assert "usage error" in _failure_reason(4, "", "")


def test_unknown_failure_falls_back_to_exit_code():
    assert "exit code 3" in _failure_reason(3, "", "")


def test_default_result_is_not_run():
    # A fresh TestResult must not claim a run happened.
    assert RunResult().ran is False

