"""API tests for the FastAPI layer.

These exercise the real pipeline against the trusted demo repository: the same
code path a browser request takes. No network access and no model calls.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(scope="module")
def boundary_review():
    res = client.post("/api/reviews", json={"case_id": "boundary-bug"})
    assert res.status_code == 200, res.text
    return res.json()


# ---- health & pages ---------------------------------------------------------

def test_health_reports_status():
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["cases"] == 3
    assert "llm_status" in body


def test_index_page_renders():
    res = client.get("/")
    assert res.status_code == 200
    assert "PR Guardian" in res.text
    # all three preset cases are present in the markup
    assert res.text.count("case-card") >= 3


def test_static_assets_served():
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


# ---- cases & diff -----------------------------------------------------------

def test_cases_listed():
    body = client.get("/api/cases").json()
    ids = [c["case_id"] for c in body["cases"]]
    assert ids == ["boundary-bug", "clean", "injection"]


def test_diff_has_line_numbers():
    body = client.get("/api/cases/boundary-bug/diff").json()
    assert body["files"], "expected at least one changed file"
    file = body["files"][0]
    assert file["file_path"] == "src/discount.py"
    add_lines = [ln for h in file["hunks"] for ln in h["lines"] if ln["kind"] == "add"]
    assert any("order_total > 100" in ln["text"] for ln in add_lines)
    assert all(ln["new_no"] is not None for ln in add_lines)


def test_diff_unknown_case_is_404():
    assert client.get("/api/cases/nope/diff").status_code == 404


# ---- running a review -------------------------------------------------------

def test_boundary_bug_yields_confirmed_test_finding(boundary_review):
    report = boundary_review["report"]
    assert report["test_summary"]["ran"] is True
    assert report["test_summary"]["failed"] == 1
    findings = report["final_findings"]
    assert len(findings) == 1
    assert findings[0]["source"] == "pytest"
    assert findings[0]["verification_status"] == "confirmed"


def test_llm_marked_not_configured_without_key(boundary_review, monkeypatch):
    # The server process has no API key in the test environment.
    report = boundary_review["report"]
    assert report["llm_status"] in {"not_configured", "disabled_by_public_policy"}
    assert report["llm_findings"] == []


def test_clean_case_has_no_findings():
    res = client.post("/api/reviews", json={"case_id": "clean"})
    body = res.json()
    assert body["report"]["final_findings"] == []
    assert body["report"]["test_summary"]["failed"] == 0
    assert body["report"]["test_summary"]["passed"] == 2


def test_injection_case_is_reviewed_normally():
    res = client.post("/api/reviews", json={"case_id": "injection"})
    body = res.json()
    # the injected instruction must not create or suppress findings
    assert body["report"]["final_findings"] == []
    assert body["report"]["test_summary"]["passed"] == 2


def test_unknown_case_is_404():
    assert client.post("/api/reviews", json={"case_id": "nope"}).status_code == 404


def test_empty_case_id_rejected():
    assert client.post("/api/reviews", json={"case_id": ""}).status_code == 422


def test_run_without_tests_skips_pytest():
    res = client.post("/api/reviews", json={"case_id": "boundary-bug", "run_tests": False})
    body = res.json()
    assert body["report"]["test_summary"]["ran"] is False
    assert body["report"]["final_findings"] == []


# ---- approvals --------------------------------------------------------------

def test_accept_and_reject_decision(boundary_review):
    rid = boundary_review["review_id"]
    fid = boundary_review["report"]["final_findings"][0]["id"]
    url = f"/api/reviews/{rid}/findings/{fid}/decision"

    assert client.post(url, json={"decision": "accepted"}).json()["decision"] == "accepted"
    got = client.get(f"/api/reviews/{rid}").json()
    assert got["approvals"][fid] == "accepted"

    assert client.post(url, json={"decision": "rejected"}).json()["decision"] == "rejected"
    got = client.get(f"/api/reviews/{rid}").json()
    assert got["approvals"][fid] == "rejected"


def test_invalid_decision_rejected(boundary_review):
    rid = boundary_review["review_id"]
    fid = boundary_review["report"]["final_findings"][0]["id"]
    res = client.post(f"/api/reviews/{rid}/findings/{fid}/decision", json={"decision": "maybe"})
    assert res.status_code == 422


def test_decision_unknown_review_is_404(boundary_review):
    fid = boundary_review["report"]["final_findings"][0]["id"]
    res = client.post(f"/api/reviews/nope/findings/{fid}/decision", json={"decision": "accepted"})
    assert res.status_code == 404


def test_decision_unknown_finding_is_404(boundary_review):
    rid = boundary_review["review_id"]
    res = client.post(f"/api/reviews/{rid}/findings/nope/decision", json={"decision": "accepted"})
    assert res.status_code == 404


def test_unknown_review_is_404():
    assert client.get("/api/reviews/nope").status_code == 404


# ---- export -----------------------------------------------------------------

def test_markdown_export(boundary_review):
    rid = boundary_review["review_id"]
    res = client.get(f"/api/reviews/{rid}/export?format=markdown")
    assert res.status_code == 200
    assert "text/markdown" in res.headers["content-type"]
    assert "attachment" in res.headers["content-disposition"]
    assert "# PR Guardian review" in res.text


def test_json_export_parses(boundary_review):
    rid = boundary_review["review_id"]
    res = client.get(f"/api/reviews/{rid}/export?format=json")
    assert res.status_code == 200
    payload = res.json()
    assert payload["review_id"] == rid
    assert "findings" in payload


def test_approved_only_export_respects_decisions(boundary_review):
    rid = boundary_review["review_id"]
    fid = boundary_review["report"]["final_findings"][0]["id"]
    url = f"/api/reviews/{rid}/findings/{fid}/decision"

    client.post(url, json={"decision": "pending"})
    pending = client.get(f"/api/reviews/{rid}/export?format=markdown&approved_only=true").text
    assert "### " not in pending

    client.post(url, json={"decision": "accepted"})
    accepted = client.get(f"/api/reviews/{rid}/export?format=markdown&approved_only=true").text
    assert "### " in accepted
    assert "ACCEPTED" in accepted


def test_export_unknown_review_is_404():
    assert client.get("/api/reviews/nope/export").status_code == 404


def test_export_bad_format_rejected(boundary_review):
    rid = boundary_review["review_id"]
    assert client.get(f"/api/reviews/{rid}/export?format=xml").status_code == 422
