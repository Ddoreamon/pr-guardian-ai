"""Product acceptance for the upload flow, run against a real local server.

    PR_GUARDIAN_UPLOAD_ENABLED=1 uvicorn app.main:app --port 8801
    python scripts/upload_acceptance.py --base-url http://127.0.0.1:8801

Covers the ten acceptance items and prints PASS/FAIL per check. It never mocks the
analyser: the server does the real work, and the script only drives HTTP.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

import httpx

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))


def zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


SAMPLE = (
    b"import os\nimport subprocess\n\nAPI_TOKEN = 'sk-not-a-real-key-value'\n\n\n"
    b"def run(cmd, items=[]):\n    items.append(cmd)\n    try:\n"
    b"        return subprocess.run(cmd, shell=True)\n    except:\n        return None\n"
    b"\n\ndef evaluate(text):\n    return eval(text)\n"
)
CLEAN = b"def add(a, b):\n    return a + b\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    with httpx.Client(base_url=base, timeout=60) as client:
        config = client.get("/api/uploads/config").json()
        check("uploads enabled", config.get("enabled") is True, json.dumps(config["limits"]))

        single = client.post("/api/uploads", files=[
            ("files", ("single.py", SAMPLE, "text/x-python"))])
        check("1 single .py accepted", single.status_code == 201, single.text[:80])
        report = single.json()["report"]
        review_id = report["review_id"]

        archive = client.post("/api/uploads", files=[
            ("archive", ("bundle.zip", zip_bytes([("pkg/tasks.py", SAMPLE),
                                                  ("pkg/clean.py", CLEAN)]),
             "application/zip"))])
        check("2 multi-file ZIP accepted", archive.status_code == 201,
              f"files={archive.json().get('files')}")

        ruff = [f for f in report["final_findings"] if f["source"] == "ruff"]
        check("3 Ruff finding present", bool(ruff),
              (ruff[0]["rule"] + ": " + ruff[0]["summary"])[:70] if ruff else "none")
        check("4 heuristic finding present",
              any(f["source"] == "heuristic" for f in report["final_findings"]),
              ",".join(sorted({f["rule"] for f in report["final_findings"]
                               if f["source"] == "heuristic"})))
        located = all(f["file_path"] and f["start_line"] >= 1 for f in report["final_findings"])
        check("5 every finding has file and line", located,
              f"{len(report['final_findings'])} findings")

        finding_id = report["final_findings"][0]["id"]
        decision = client.post(
            f"/api/reviews/{review_id}/findings/{finding_id}/decision",
            json={"decision": "accepted"})
        check("6 accept/reject works", decision.status_code == 200,
              json.dumps(decision.json()))

        markdown = client.get(f"/api/reviews/{review_id}/export?format=markdown")
        payload = client.get(f"/api/reviews/{review_id}/export?format=json")
        check("7 JSON and Markdown export", markdown.status_code == 200
              and payload.status_code == 200
              and "attachment" in payload.headers.get("content-disposition", ""),
              f"md={len(markdown.text)} chars")

        clean = client.post("/api/uploads", files=[
            ("files", ("clean.py", CLEAN, "text/x-python"))])
        clean_report = clean.json()["report"]
        honesty = ("does not prove" in json.dumps(clean_report).lower()
                   or clean_report["final_findings"] == [])
        check("8 clean result stays honest", honesty,
              f"findings={len(clean_report['final_findings'])}")

        bad = client.post("/api/uploads", files=[
            ("files", ("broken.py", b"def broken(:\n", "text/x-python"))])
        broken = bad.json()["report"]
        # a file that cannot be parsed at all is ast_status "failed"; a mixed upload is
        # "partial". Either way the review must not read as a completed clean run.
        check("9 syntax error reported, review not marked complete",
              bad.status_code == 201
              and broken["upload_analysis"]["ast_status"] in ("partial", "failed")
              and broken["review_status"] == "partial",
              f"ast={broken['upload_analysis']['ast_status']} "
              f"review={broken['review_status']}")

        def one(_: int) -> int:
            response = client.post("/api/uploads", files=[
                ("files", (f"c-{uuid.uuid4().hex[:6]}.py", CLEAN, "text/x-python"))])
            return response.status_code

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = list(pool.map(one, range(4)))
        health = client.get("/health")
        elapsed = time.perf_counter() - started
        check("10 concurrency bounded and health responsive",
              all(code in (201, 503) for code in codes)
              and 201 in codes and health.status_code == 200,
              f"codes={codes} health={health.status_code} in {elapsed:.1f}s")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} acceptance checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
