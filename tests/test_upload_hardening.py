"""Hardening tests: bounded concurrency, honest stage status, process-tree cleanup.

These cover the V1.1 review findings: the analyser must not block the event loop, must
not spawn unbounded processes, must report partial analysis as partial, and must not
leave Ruff running when the worker is killed.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import time

import psutil
import pytest
from fastapi.testclient import TestClient

from app.main import app
from pr_guardian import upload_review
from pr_guardian.settings import Settings
from pr_guardian.upload_review import kill_process_tree, run_upload_review
from pr_guardian.uploads import collect_from_files

GOOD = b"def add(a, b):\n    return a + b\n"


def settings(**overrides) -> Settings:
    base = {"api_key": "", "base_url": "https://example.test", "model": "m",
                "public_mode": False, "allow_live_llm": False, "allow_test_execution": False,
                "max_live_llm_calls": 2, "upload_enabled": True}
    base.update(overrides)
    return Settings(**base)


def client(monkeypatch, **env) -> TestClient:
    monkeypatch.setenv("PR_GUARDIAN_UPLOAD_ENABLED", "1")
    monkeypatch.setenv("PR_GUARDIAN_PUBLIC_MODE", "0")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return TestClient(app)


# ---- bounded concurrency ---------------------------------------------------

def test_busy_response_when_all_slots_are_taken(monkeypatch):
    monkeypatch.setenv("PR_GUARDIAN_UPLOAD_CONCURRENCY", "1")
    release = __import__("threading").Event()

    def slow(files, cfg, store=None):
        release.wait(timeout=10)
        return run_upload_review(files, cfg, store=store)

    monkeypatch.setattr("app.uploads_api.run_upload_review", slow)
    with client(monkeypatch) as http:
        holder: dict = {}

        def first() -> None:
            holder["response"] = http.post(
                "/api/uploads", files=[("files", ("a.py", GOOD, "text/x-python"))])

        thread = __import__("threading").Thread(target=first)
        thread.start()
        deadline = time.time() + 5
        while time.time() < deadline:
            from app.uploads_api import active_slots

            if active_slots() == 1:
                break
            time.sleep(0.02)

        second = http.post("/api/uploads",
                           files=[("files", ("b.py", GOOD, "text/x-python"))])
        assert second.status_code == 503
        assert second.json()["error"] == "busy"
        assert second.headers.get("retry-after") == "2"

        release.set()
        thread.join(timeout=20)
        assert holder["response"].status_code == 201
        from app.uploads_api import active_slots

        assert active_slots() == 0


def test_health_and_demo_api_respond_while_analysis_runs(monkeypatch):
    monkeypatch.setenv("PR_GUARDIAN_UPLOAD_CONCURRENCY", "2")
    release = __import__("threading").Event()

    def slow(files, cfg, store=None):
        release.wait(timeout=10)
        return run_upload_review(files, cfg, store=store)

    monkeypatch.setattr("app.uploads_api.run_upload_review", slow)
    with client(monkeypatch) as http:
        import threading

        thread = threading.Thread(target=lambda: http.post(
            "/api/uploads", files=[("files", ("a.py", GOOD, "text/x-python"))]))
        thread.start()
        from app.uploads_api import active_slots

        deadline = time.time() + 5
        while time.time() < deadline and active_slots() == 0:
            time.sleep(0.02)

        started = time.perf_counter()
        assert http.get("/health").status_code == 200
        assert http.get("/api/cases").status_code == 200
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"health/demo API blocked for {elapsed:.2f}s"

        release.set()
        thread.join(timeout=20)


def test_slot_is_released_after_a_timeout(monkeypatch):
    from pr_guardian.uploads import UploadRejected

    calls = {"n": 0}
    real = upload_review.run_upload_review

    def flaky(files, cfg, store=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise UploadRejected("analysis_timeout", "too slow")
        return real(files, cfg, store=store)

    monkeypatch.setattr("app.uploads_api.run_upload_review", flaky)
    with client(monkeypatch) as http:
        first = http.post("/api/uploads",
                          files=[("files", ("a.py", GOOD, "text/x-python"))])
        assert first.status_code == 400
        assert first.json()["error"] == "analysis_timeout"
        second = http.post("/api/uploads",
                           files=[("files", ("a.py", GOOD, "text/x-python"))])
        assert second.status_code == 201, second.text


def test_oversized_body_is_rejected_before_parsing(monkeypatch):
    with client(monkeypatch) as http:
        payload = b"x" * (14 * 1024 * 1024)
        response = http.post("/api/uploads",
                             content=payload,
                             headers={"content-type": "multipart/form-data; boundary=x"})
        assert response.status_code == 413
        assert response.json()["error"] == "upload_too_large"


# ---- honest stage status ---------------------------------------------------

def test_ruff_timeout_makes_the_review_partial_and_keeps_findings(monkeypatch):
    payload = {
        "files": [{"path": "a.py", "lines": 3, "syntax_error": ""}],
        "findings": [{"file_path": "a.py", "start_line": 2, "end_line": 2,
                      "rule": "UP001", "severity": "high", "category": "heuristic",
                      "summary": "eval", "explanation": "", "suggested_fix": "",
                      "source": "heuristic"}],
        "ruff_status": "timeout",
        "ast_status": "ok",
        "files_parsed": 1,
        "files_with_syntax_errors": 0,
    }
    monkeypatch.setattr(upload_review, "_run_worker", lambda root: (payload, "ok"))
    report = run_upload_review(collect_from_files([("a.py", GOOD)]), settings())
    assert report.review_status == "partial"
    assert report.completeness.reason == "partial_analysis"
    assert report.upload_analysis.ast_status == "ok"
    assert report.upload_analysis.ruff_status == "timeout"
    assert report.upload_analysis.worker_status == "ok"
    assert [f.rule for f in report.final_findings] == ["UP001"]
    assert any("Ruff could not complete" in note for note in report.notes)


def test_worker_timeout_is_not_reported_as_completed(monkeypatch):
    import subprocess as real_subprocess

    class Slow:
        returncode = -1
        pid = 999_998

        def __init__(self):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise real_subprocess.TimeoutExpired(cmd="worker", timeout=1)
            self.returncode = -9
            return b"", b""

        def poll(self):
            return None

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(upload_review.subprocess, "Popen", lambda *a, **k: Slow())
    monkeypatch.setattr(upload_review, "kill_process_tree", lambda proc: None)
    from pr_guardian.uploads import UploadRejected

    with pytest.raises(UploadRejected) as exc:
        run_upload_review(collect_from_files([("a.py", GOOD)]), settings())
    assert exc.value.code == "analysis_timeout"


def test_syntax_error_makes_ast_partial_but_keeps_ruff_findings():
    files = collect_from_files([("ok.py", b"import os\n"), ("bad.py", b"def x(:\n")])
    report = run_upload_review(files, settings())
    assert report.review_status == "partial"
    assert report.upload_analysis.ast_status == "partial"
    assert report.upload_analysis.files_with_syntax_errors == 1
    assert any(f.source.value == "ruff" for f in report.final_findings)


def test_ai_not_run_is_reported_explicitly():
    report = run_upload_review(collect_from_files([("a.py", GOOD)]), settings())
    assert report.upload_analysis.ai_status == "not_run"
    assert report.llm_status == "disabled_by_request"


# ---- process tree ----------------------------------------------------------

SPAWNER = textwrap.dedent(
    """
    import subprocess, sys, time
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    print(child.pid, flush=True)
    time.sleep(120)
    """
)


@pytest.mark.skipif(os.name != "nt", reason="Windows tree-kill path")
def test_windows_tree_kill_reaches_the_grandchild(tmp_path):
    script = tmp_path / "spawner.py"
    script.write_text(SPAWNER, encoding="utf-8")
    proc = __import__("subprocess").Popen(
        [sys.executable, str(script)], stdout=__import__("subprocess").PIPE,
        text=True, creationflags=__import__("subprocess").CREATE_NEW_PROCESS_GROUP)
    try:
        grandchild = int(proc.stdout.readline().strip())
        assert psutil.pid_exists(grandchild)
        kill_process_tree(proc)
        proc.wait(timeout=10)
        for _ in range(50):
            if not psutil.pid_exists(grandchild):
                break
            time.sleep(0.1)
        assert not psutil.pid_exists(grandchild), "Ruff-like grandchild survived"
    finally:
        for pid in (proc.pid, locals().get("grandchild")):
            if pid and psutil.pid_exists(pid):
                psutil.Process(pid).kill()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group path")
def test_posix_tree_kill_reaches_the_grandchild(tmp_path):
    script = tmp_path / "spawner.py"
    script.write_text(SPAWNER, encoding="utf-8")
    proc = __import__("subprocess").Popen(
        [sys.executable, str(script)], stdout=__import__("subprocess").PIPE,
        text=True, start_new_session=True)
    try:
        grandchild = int(proc.stdout.readline().strip())
        kill_process_tree(proc)
        proc.wait(timeout=10)
        for _ in range(50):
            if not psutil.pid_exists(grandchild):
                break
            time.sleep(0.1)
        assert not psutil.pid_exists(grandchild)
    finally:
        for pid in (proc.pid, locals().get("grandchild")):
            if pid and psutil.pid_exists(pid):
                psutil.Process(pid).kill()


# ---- logs ------------------------------------------------------------------

def test_uploaded_source_never_reaches_the_logs(caplog):
    marker = "SOURCE_MARKER_SHOULD_NOT_BE_LOGGED"
    source = f"# {marker}\nx = 1\n".encode()
    with caplog.at_level("DEBUG"):
        run_upload_review(collect_from_files([("marked.py", source)]), settings())
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert marker not in text
    assert "x = 1" not in text


def test_report_does_not_contain_source_text():
    source = b"SECRET_VALUE = 'do-not-echo-this-string'\n"
    report = run_upload_review(collect_from_files([("s.py", source)]), settings())
    blob = json.dumps(report.model_dump(mode="json"))
    assert "do-not-echo-this-string" not in blob
