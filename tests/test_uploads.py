"""Upload & review: functionality, limits, and the security boundary.

These tests assert what the upload path does *not* do as much as what it does: the
uploaded code is never executed, tested, imported or installed, and public mode never
reaches a paid model.
"""

from __future__ import annotations

import io
import threading
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import ReviewStore
from pr_guardian.settings import Settings
from pr_guardian.upload_review import apply_ai_review, run_upload_review
from pr_guardian.uploads import (
    MAX_FILE_BYTES,
    UploadRejected,
    collect_from_files,
    collect_from_zip,
)

GOOD = b"def add(a, b):\n    return a + b\n"


def settings(**overrides) -> Settings:
    base = {"api_key": "", "base_url": "https://example.test", "model": "m",
                "public_mode": False, "allow_live_llm": False, "allow_test_execution": False,
                "max_live_llm_calls": 3, "upload_enabled": True}
    base.update(overrides)
    return Settings(**base)


def zip_bytes(entries: list[tuple[str, bytes]], *, symlink: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            archive.writestr(info, "/etc/passwd")
    return buffer.getvalue()


def client(monkeypatch, **env) -> TestClient:
    monkeypatch.setenv("PR_GUARDIAN_UPLOAD_ENABLED", "1")
    monkeypatch.setenv("PR_GUARDIAN_ALLOW_LIVE_LLM", "0")
    monkeypatch.setenv("PR_GUARDIAN_PUBLIC_MODE", "0")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return TestClient(app)


# ---- validation ------------------------------------------------------------

def test_single_file_is_accepted():
    files = collect_from_files([("mod.py", GOOD)])
    assert files[0].path == "mod.py"
    assert files[0].text.startswith("def add")


def test_multiple_files_keep_their_paths():
    files = collect_from_files([("pkg/a.py", GOOD), ("b.py", GOOD)])
    assert [f.path for f in files] == ["pkg/a.py", "b.py"]


def test_zip_is_accepted():
    files = collect_from_zip(zip_bytes([("pkg/a.py", GOOD), ("b.py", GOOD)]))
    assert [f.path for f in files] == ["pkg/a.py", "b.py"]


def test_non_python_file_is_rejected():
    with pytest.raises(UploadRejected) as exc:
        collect_from_files([("notes.txt", b"hello")])
    assert exc.value.code == "unsupported_type"


def test_zip_slip_is_rejected():
    with pytest.raises(UploadRejected) as exc:
        collect_from_zip(zip_bytes([("../evil.py", GOOD)]))
    assert exc.value.code == "path_traversal"


def test_absolute_path_is_rejected():
    with pytest.raises(UploadRejected) as exc:
        collect_from_files([("/etc/passwd.py", GOOD)])
    assert exc.value.code == "absolute_path"


def test_zip_symlink_is_rejected():
    with pytest.raises(UploadRejected) as exc:
        collect_from_zip(zip_bytes([("a.py", GOOD)], symlink="link.py"))
    assert exc.value.code == "symlink_entry"


def test_zip_bomb_ratio_is_rejected():
    payload = b"a = 1\n" * 60_000  # compresses far beyond the ratio limit
    with pytest.raises(UploadRejected) as exc:
        collect_from_zip(zip_bytes([("bomb.py", payload)]))
    assert exc.value.code in {"compression_ratio", "file_too_large", "upload_too_large"}


def test_oversized_file_is_rejected():
    with pytest.raises(UploadRejected) as exc:
        collect_from_files([("big.py", b"x = 1\n" * (MAX_FILE_BYTES // 5))])
    assert exc.value.code == "file_too_large"


def test_case_insensitive_duplicate_is_rejected():
    with pytest.raises(UploadRejected) as exc:
        collect_from_files([("A.py", GOOD), ("a.py", GOOD)])
    assert exc.value.code == "duplicate_path"


def test_unicode_filename_is_preserved():
    files = collect_from_files([("模块/工具.py", GOOD)])
    assert files[0].path == "模块/工具.py"


def test_bad_encoding_is_rejected():
    with pytest.raises(UploadRejected) as exc:
        collect_from_files([("bad.py", b"\xff\xfe\x00bad")])
    assert exc.value.code == "bad_encoding"


def test_empty_upload_is_rejected():
    with pytest.raises(UploadRejected) as exc:
        collect_from_files([])
    assert exc.value.code == "empty_upload"


def test_syntax_error_is_reported_not_raised():
    files = collect_from_files([("broken.py", b"def broken(:\n")])
    report = run_upload_review(files, settings())
    rules = {finding.rule for finding in report.final_findings}
    assert "AST001" in rules


# ---- analysis --------------------------------------------------------------

def test_findings_map_to_file_and_line():
    source = b"import os\n\ndef f(x=[]):\n    eval(x)\n    os.system('ls')\n"
    report = run_upload_review(collect_from_files([("pkg/m.py", source)]), settings())
    heuristics = [f for f in report.final_findings if f.source.value == "heuristic"]
    assert {f.rule for f in heuristics} >= {"UP001", "UP003", "UP006"}
    assert {f.file_path for f in heuristics} == {"pkg/m.py"}
    assert all(f.start_line >= 1 for f in heuristics)
    assert all(f.verification_status.value == "suspected" for f in heuristics)
    assert all(f.claim_status == "claim_unjudged" for f in heuristics)


def test_ruff_findings_are_lint_evidence():
    source = b"import os\n"  # unused import
    report = run_upload_review(collect_from_files([("lint.py", source)]), settings())
    ruff = [f for f in report.final_findings if f.source.value == "ruff"]
    assert ruff, "ruff should report the unused import"
    assert all(f.verification_status.value == "confirmed" for f in ruff)


def test_clean_file_has_no_findings_but_is_not_proclaimed_correct():
    report = run_upload_review(collect_from_files([("clean.py", GOOD)]), settings())
    assert [f for f in report.final_findings if f.source.value != "ruff"] == []
    assert any("does not prove" in note for note in report.notes)


def test_upload_never_runs_tests():
    report = run_upload_review(collect_from_files([("clean.py", GOOD)]), settings())
    assert report.test_summary.ran is False
    assert "never executed" in report.test_summary.skipped_reason


# ---- execution boundary ----------------------------------------------------

def test_uploaded_code_is_never_executed(tmp_path, monkeypatch):
    marker = tmp_path / "EXECUTED"
    source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "if __name__ == '__main__':\n"
        f"    Path({str(marker)!r}).write_text('executed')\n"
    ).encode()
    report = run_upload_review(collect_from_files([("evil.py", source)]), settings())
    assert report.final_findings is not None
    assert not marker.exists(), "uploaded code must never be executed"


def test_only_ruff_and_the_worker_are_spawned(monkeypatch):
    calls: list[list[str]] = []
    real_popen = __import__("subprocess").Popen

    def spy(cmd, *args, **kwargs):
        calls.append([str(part) for part in cmd])
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr("pr_guardian.upload_review.subprocess.Popen", spy)
    run_upload_review(collect_from_files([("a.py", GOOD)]), settings())
    flattened = " ".join(" ".join(call) for call in calls)
    assert "pr_guardian.upload_worker" in flattened
    assert "pytest" not in flattened
    assert "-m pip" not in flattened
    for call in calls:
        assert call[-1] != "a.py", "the uploaded file must never be a command"


def test_worker_runs_ruff_and_nothing_else():
    """Ruff is spawned inside the worker; the parent never sees the uploaded path."""
    import json
    import subprocess
    import sys
    import tempfile

    work = Path(tempfile.mkdtemp(prefix="prg-worker-test-"))
    (work / "a.py").write_text("import os\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-I", "-m", "pr_guardian.upload_worker",
                           str(work)], capture_output=True, text=True, timeout=60,
                          check=False)
    payload = json.loads(proc.stdout)
    assert payload["ruff_status"] == "ok"
    assert any(item["source"] == "ruff" for item in payload["findings"])


def test_work_directory_is_removed():
    import tempfile

    before = set(Path(tempfile.gettempdir()).glob("pr-guardian-upload-*"))
    run_upload_review(collect_from_files([("a.py", GOOD)]), settings())
    after = set(Path(tempfile.gettempdir()).glob("pr-guardian-upload-*"))
    assert after == before


def test_timeout_is_reported_not_hung(monkeypatch):
    import subprocess as real_subprocess

    class SlowProcess:
        pid = 999_999
        returncode = -1

        def poll(self):
            return None

        calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise real_subprocess.TimeoutExpired(cmd="worker", timeout=1)
            self.returncode = -9
            return b"", b""

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr("pr_guardian.upload_review.subprocess.Popen",
                        lambda *a, **k: SlowProcess())
    monkeypatch.setattr("pr_guardian.upload_review.kill_process_tree",
                        lambda proc: None)
    with pytest.raises(UploadRejected) as exc:
        run_upload_review(collect_from_files([("a.py", GOOD)]), settings())
    assert exc.value.code == "analysis_timeout"


# ---- public mode and AI ----------------------------------------------------

def test_public_mode_never_calls_a_paid_model(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("a paid model was called in public mode")

    monkeypatch.setattr("httpx.Client.post", explode)
    report = run_upload_review(collect_from_files([("a.py", GOOD)]),
                               settings(public_mode=True, api_key="sk-test",
                                        allow_live_llm=True, upload_ai_enabled=True))
    updated = apply_ai_review(report, collect_from_files([("a.py", GOOD)]),
                              settings(public_mode=True, api_key="sk-test",
                                       allow_live_llm=True, upload_ai_enabled=True),
                              ReviewStore())
    assert updated.llm_status == "disabled_by_public_policy"


def test_ai_is_off_by_default():
    report = run_upload_review(collect_from_files([("a.py", GOOD)]), settings())
    assert report.llm_status == "disabled_by_request"


def test_budget_exhaustion_is_reported():
    store = ReviewStore()
    store.try_consume_llm_budget(1)
    report = run_upload_review(collect_from_files([("a.py", GOOD)]),
                               settings(api_key="sk-test", allow_live_llm=True,
                                        upload_ai_enabled=True, max_live_llm_calls=1))
    updated = apply_ai_review(report, collect_from_files([("a.py", GOOD)]),
                              settings(api_key="sk-test", allow_live_llm=True,
                                       upload_ai_enabled=True, max_live_llm_calls=1),
                              store)
    assert updated.llm_status == "budget_exhausted"


# ---- HTTP surface ----------------------------------------------------------

def test_uploads_disabled_by_default(monkeypatch):
    monkeypatch.setenv("PR_GUARDIAN_UPLOAD_ENABLED", "0")
    response = TestClient(app).post(
        "/api/uploads", files=[("files", ("a.py", GOOD, "text/x-python"))])
    assert response.status_code == 403
    assert response.json()["error"] == "uploads_disabled"


def test_public_mode_always_refuses_uploads(monkeypatch):
    monkeypatch.setenv("PR_GUARDIAN_UPLOAD_ENABLED", "1")
    monkeypatch.setenv("PR_GUARDIAN_PUBLIC_MODE", "1")
    for ack in ("0", "1"):
        monkeypatch.setenv("PR_GUARDIAN_UPLOAD_PUBLIC", ack)
        with TestClient(app) as http:
            response = http.post(
                "/api/uploads", files=[("files", ("a.py", GOOD, "text/x-python"))])
            assert response.status_code == 403, f"ack={ack} must still be refused"
            assert http.get("/api/uploads/config").json()["enabled"] is False


def test_http_upload_round_trip(monkeypatch):
    with client(monkeypatch) as http:
        response = http.post("/api/uploads", files=[
            ("files", ("pkg/a.py", GOOD, "text/x-python")),
            ("files", ("b.py", b"import os\n", "text/x-python")),
        ])
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["files"] == 2
        review_id = payload["review_id"]

        fetched = http.get(f"/api/uploads/{review_id}")
        assert fetched.status_code == 200
        assert fetched.json()["report"]["review_id"] == review_id

        exported = http.get(f"/api/reviews/{review_id}/export?format=json")
        assert exported.status_code == 200
        assert "attachment" in exported.headers["content-disposition"]

        markdown = http.get(f"/api/reviews/{review_id}/export?format=markdown")
        assert markdown.status_code == 200
        assert "Review" in markdown.text


def test_http_zip_upload(monkeypatch):
    with client(monkeypatch) as http:
        response = http.post("/api/uploads", files=[
            ("archive", ("files.zip", zip_bytes([("a.py", GOOD)]), "application/zip")),
        ])
        assert response.status_code == 201, response.text
        assert response.json()["files"] == 1


def test_invalid_upload_returns_a_clear_error(monkeypatch):
    with client(monkeypatch) as http:
        response = http.post("/api/uploads", files=[
            ("files", ("notes.txt", b"hi", "text/plain")),
        ])
        assert response.status_code == 400
        assert response.json()["error"] == "unsupported_type"


def test_concurrent_uploads_stay_independent(monkeypatch):
    monkeypatch.setenv("PR_GUARDIAN_UPLOAD_CONCURRENCY", "4")
    results: list[int] = []
    lock = threading.Lock()

    def upload() -> None:
        with client(monkeypatch) as http:
            response = http.post("/api/uploads", files=[
                ("files", ("a.py", GOOD, "text/x-python"))])
            with lock:
                results.append(response.status_code)

    threads = [threading.Thread(target=upload) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [201, 201, 201, 201]


def test_upload_page_states_the_security_boundary(monkeypatch):
    with client(monkeypatch) as http:
        page = http.get("/upload")
        assert page.status_code == 200
        assert "never executed" in page.text.lower()
        assert "suspected" in page.text.lower()


def test_demo_page_and_api_still_work(monkeypatch):
    with client(monkeypatch) as http:
        assert http.get("/").status_code == 200
        payload = http.get("/api/cases").json()
        assert len(payload["cases"]) == 3


def test_utf8_bom_is_tolerated():
    """Windows-authored files often start with a BOM; it must not break parsing."""
    files = collect_from_files([("bom.py", "\ufeffx = 1\n".encode())])
    assert files[0].text == "x = 1\n"
    report = run_upload_review(files, settings())
    assert report.upload_analysis.ast_status == "ok"
    assert report.review_status == "completed"


def test_upload_page_is_disabled_by_default(monkeypatch):
    """Without the opt-in, the page renders but says analysis is off."""
    monkeypatch.setenv("PR_GUARDIAN_UPLOAD_ENABLED", "0")
    with TestClient(app) as http:
        page = http.get("/upload")
        assert page.status_code == 200
        assert "disabled on this instance" in page.text
        assert http.get("/api/uploads/config").json()["enabled"] is False


def test_lying_zip_headers_cannot_bypass_the_total_limit(monkeypatch):
    """Header sizes are attacker-controlled; the decompressed total is what matters."""
    from pr_guardian import uploads

    class LyingArchive:
        def __init__(self, *args, **kwargs):
            self.entries = [zipfile.ZipInfo(f"f{index}.py") for index in range(24)]
            for entry in self.entries:
                entry.file_size = 1024          # header claims 1 KiB
                entry.compress_size = 1024
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def infolist(self):
            return self.entries
        def read(self, info):
            # each entry stays under the per-file cap, but 24 of them exceed the total
            return b"x = 1\n" * 30_000

    monkeypatch.setattr(uploads.zipfile, "ZipFile", LyingArchive)
    with pytest.raises(UploadRejected) as exc:
        uploads.collect_from_zip(b"PK\x03\x04pretend")
    assert exc.value.code == "upload_too_large"


def test_temp_dir_is_cleaned_when_analysis_fails(monkeypatch):
    """A worker crash must not leave the uploaded source on disk."""
    import tempfile

    from pr_guardian.uploads import UploadRejected

    def boom(root):
        raise UploadRejected("analysis_failed", "worker crashed")

    monkeypatch.setattr("pr_guardian.upload_review._run_worker", boom)
    before = set(Path(tempfile.gettempdir()).glob("pr-guardian-upload-*"))
    with pytest.raises(UploadRejected):
        run_upload_review(collect_from_files([("a.py", GOOD)]), settings())
    after = set(Path(tempfile.gettempdir()).glob("pr-guardian-upload-*"))
    assert after == before
