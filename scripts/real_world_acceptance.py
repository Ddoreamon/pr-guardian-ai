"""Real-world acceptance: upload three realistic projects, fix, re-upload, export.

Drives the real HTTP API and (for one case) the real browser page. Nothing is mocked:
the server runs the actual analyser.

    PR_GUARDIAN_UPLOAD_ENABLED=1 uvicorn app.main:app --port 8821 > /tmp/prg.log 2>&1
    python scripts/real_world_acceptance.py --base-url http://127.0.0.1:8821 \
        --server-log /tmp/prg.log

Outputs docs/v1.1-acceptance/results.json and REPORT.md.
"""

from __future__ import annotations

import argparse
import io
import json
import time
import zipfile
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path("docs/v1.1-acceptance")
SAMPLES = ROOT / "samples"
FIXED = ROOT / "fixed"

# Human judgement, written before running the tool: what a reviewer considers a real
# problem in each sample, and what the deterministic checks should find.
EXPECTATIONS = {
    "A_csv_report": {
        "title": "CSV → JSON sales summary (single file)",
        "license": "self-authored, MIT",
        "files": ["csv_report.py"],
        "expected": [
            {"rule": "F401", "file": "csv_report.py", "verdict": "valid",
             "actionable": "yes", "note": "unused OrderedDict import"},
            {"rule": "F541", "file": "csv_report.py", "verdict": "valid",
             "actionable": "yes", "note": "f-string without placeholders"},
            {"rule": "UP006", "file": "csv_report.py", "verdict": "valid",
             "actionable": "yes", "note": "mutable default argument"},
            {"rule": "B006", "file": "csv_report.py", "verdict": "duplicate",
             "actionable": "yes", "note": "same defect as UP006, different rule"},
        ],
        "should_disappear": ["F401", "F541", "UP006", "B006"],
        "missed": [
            "next(reader) skips the first data row: DictReader already consumed the header",
            "float() on a malformed amount raises with no handler",
        ],
    },
    "B_fastapi_service": {
        "title": "FastAPI inventory service (single file)",
        "license": "self-authored, MIT",
        "files": ["api.py"],
        "expected": [
            {"rule": "F401", "file": "api.py", "verdict": "valid",
             "actionable": "yes", "note": "unused os import"},
            {"rule": "UP005", "file": "api.py", "verdict": "valid",
             "actionable": "yes", "note": "bare except hides real errors"},
        ],
        "should_disappear": ["F401", "UP005"],
        "extra_verdicts": {
            "I001": {"verdict": "valid", "actionable": "yes",
                     "note": "import order (style, low value)"},
            "E722": {"verdict": "duplicate", "actionable": "yes",
                     "note": "Ruff's own bare-except rule; same defect as UP005"},
            "BLE001": {"verdict": "valid", "actionable": "yes",
                       "note": "delete handler catches every exception and leaks its text"},
        },
        "missed": [
            "quantity accepts negative values; no validation",
            "delete handler returns the exception text to the client",
            "path parameter is untyped, so every name is a string id",
        ],
    },
    "C_inventory_pkg": {
        "title": "Inventory package (multi-file, ZIP)",
        "license": "self-authored, MIT",
        "files": ["inventory/__init__.py", "inventory/models.py",
                  "inventory/service.py", "inventory/cli.py"],
        "expected": [
            {"rule": "UP002", "file": "inventory/service.py", "verdict": "valid",
             "actionable": "yes", "note": "shell=True with a caller-controlled path"},
            {"rule": "UP006", "file": "inventory/models.py", "verdict": "valid",
             "actionable": "yes", "note": "mutable default accumulates across calls"},
        ],
        "should_disappear": ["UP002", "UP006"],
        "extra_verdicts": {
            "B006": {"verdict": "duplicate", "actionable": "yes",
                     "note": "Ruff's version of UP006, same defect"},
            "PLW1510": {"verdict": "valid", "actionable": "yes",
                        "note": "subprocess.run without check=True silences failures"},
            "RUF100": {"verdict": "false_positive", "actionable": "no",
                       "note": "flags the deliberate noqa; an artefact of isolated mode"},
        },
        "missed": [
            "cli ignores the report subprocess return code and exit status",
            "discount_all reuses the shared out list between calls (same root cause)",
        ],
    },
}


def zip_dir(root: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*.py")):
            archive.write(path, path.relative_to(root).as_posix())
    return buffer.getvalue()


def upload(client: httpx.Client, root: Path, files: list[str], as_zip: bool) -> dict:
    started = time.perf_counter()
    if as_zip:
        response = client.post("/api/uploads", files=[
            ("archive", ("project.zip", zip_dir(root), "application/zip"))])
    else:
        parts = []
        for name in files:
            parts.append(("files", (Path(name).name, (root / name).read_bytes(),
                                    "text/x-python")))
        response = client.post("/api/uploads", files=parts)
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    payload = response.json()
    payload["elapsed_s"] = round(elapsed, 2)
    return payload


def index_findings(report: dict) -> dict[str, list[dict]]:
    by_rule: dict[str, list[dict]] = {}
    for finding in report["final_findings"]:
        by_rule.setdefault(finding["rule"], []).append(finding)
    return by_rule


def lines_of(root: Path, relative: str) -> int:
    return len((root / relative).read_text(encoding="utf-8").splitlines())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--server-log", type=Path)
    parser.add_argument("--skip-browser", action="store_true")
    args = parser.parse_args()

    results: dict = {"cases": [], "security": {}, "browser": {}}
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=120) as client:
        config = client.get("/api/uploads/config").json()
        results["config"] = config

        for name, spec in EXPECTATIONS.items():
            sample = SAMPLES / name
            as_zip = len(spec["files"]) > 1
            first = upload(client, sample, spec["files"], as_zip)
            report = first["report"]
            by_rule = index_findings(report)

            rows = []
            for expected in spec["expected"]:
                hits = by_rule.get(expected["rule"], [])
                hit = next((h for h in hits if h["file_path"].endswith(
                    expected["file"])), None)
                rows.append({
                    **expected,
                    "detected": hit is not None,
                    "line": hit["start_line"] if hit else None,
                    "summary": hit["summary"] if hit else "",
                    "explanation": hit["explanation"] if hit else "",
                    "suggestion": hit["suggested_fix"] if hit else "",
                    "in_range": bool(hit) and 1 <= hit["start_line"] <= lines_of(
                        sample, next(f for f in spec["files"]
                                     if f.endswith(expected["file"]))),
                })

            extra_verdicts = spec.get("extra_verdicts", {})
            unlisted = [f for f in report["final_findings"]
                        if f["rule"] not in {e["rule"] for e in spec["expected"]}]
            for finding in unlisted:
                verdict = extra_verdicts.get(finding["rule"], {
                    "verdict": "needs_review", "actionable": "unknown",
                    "note": "not pre-classified"})
                rows.append({
                    "rule": finding["rule"], "file": finding["file_path"],
                    "line": finding["start_line"], "detected": True,
                    **verdict, "summary": finding["summary"],
                    "explanation": finding["explanation"],
                    "suggestion": finding["suggested_fix"],
                    "in_range": 1 <= finding["start_line"] <= lines_of(
                        sample, next((f for f in spec["files"]
                                      if f.endswith(finding["file_path"])),
                                     spec["files"][0])),
                })

            fixed = upload(client, FIXED / name, spec["files"], as_zip)
            fixed_by_rule = index_findings(fixed["report"])
            disappeared = {rule: rule not in fixed_by_rule
                           for rule in spec["should_disappear"]}
            fixed_rules = sorted(fixed_by_rule)

            finding_id = report["final_findings"][0]["id"]
            decision = client.post(
                f"/api/reviews/{report['review_id']}/findings/{finding_id}/decision",
                json={"decision": "accepted"})
            rejected = client.post(
                f"/api/reviews/{report['review_id']}/findings/{finding_id}/decision",
                json={"decision": "rejected"})
            markdown = client.get(
                f"/api/reviews/{report['review_id']}/export?format=markdown")
            exported = client.get(
                f"/api/reviews/{report['review_id']}/export?format=json")

            loc = sum(lines_of(sample, f) for f in spec["files"])
            verdicts = [row["verdict"] for row in rows]
            metrics = {
                "findings_total": len(report["final_findings"]),
                "valid": verdicts.count("valid"),
                "duplicate": verdicts.count("duplicate"),
                "false_positive": verdicts.count("false_positive"),
                "needs_review": verdicts.count("needs_review"),
                # a duplicate rule hit is the same issue, already counted as valid
                "distinct_valid_issues": verdicts.count("valid"),
                "denominator": "all findings returned by the enabled checks",
            }
            results["cases"].append({
                "id": name,
                "title": spec["title"],
                "license": spec["license"],
                "files": len(spec["files"]),
                "lines": loc,
                "upload_mode": "zip" if as_zip else "files",
                "review_status": report["review_status"],
                "analysis": report["upload_analysis"],
                "ruff_findings": sum(1 for f in report["final_findings"]
                                     if f["source"] == "ruff"),
                "heuristic_findings": sum(1 for f in report["final_findings"]
                                          if f["source"] == "heuristic"),
                "total_findings": len(report["final_findings"]),
                "unlisted_findings": [f["rule"] for f in unlisted],
                "human_review": rows,
                "missed": spec["missed"],
                "fixed_status": fixed["report"]["review_status"],
                "disappeared": disappeared,
                "fixed_rules": fixed_rules,
                "accept_status": decision.status_code,
                "reject_status": rejected.status_code,
                "export_markdown_status": markdown.status_code,
                "export_json_status": exported.status_code,
                "export_has_attachment": "attachment" in exported.headers.get(
                    "content-disposition", ""),
                "metrics": metrics,
                "elapsed_s": first["elapsed_s"],
            })

    results["security"] = security_checks(args.server_log)
    results["public_instance"] = public_instance_check()
    if not args.skip_browser:
        results["browser"] = browser_case(args.base_url, SAMPLES / "A_csv_report")

    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    (ROOT / "REPORT.md").write_text(render(results), encoding="utf-8")
    for case in results["cases"]:
        print(f"{case['id']}: {case['total_findings']} findings "
              f"({case['ruff_findings']} ruff, {case['heuristic_findings']} heuristic), "
              f"status={case['review_status']}, {case['elapsed_s']}s")
    print("security:", results["security"])
    return 0


def public_instance_check() -> dict:
    """The public demo must not expose uploads while V1.1 is undeployed."""
    url = "https://pr-guardian-ai-uodn.onrender.com"
    try:
        import httpx as _httpx

        with _httpx.Client(timeout=45) as client:
            config = client.get(url + "/api/uploads/config")
            page = client.get(url + "/upload")
        return {"reachable": True, "config_status": config.status_code,
                "page_status": page.status_code,
                "uploads_exposed": config.status_code == 200}
    except Exception as exc:  # noqa: BLE001 - offline is a valid outcome to record
        return {"reachable": False, "error": type(exc).__name__,
                "note": "public instance not verified from this environment"}


def security_checks(server_log: Path | None) -> dict:
    import tempfile

    leftovers = list(Path(tempfile.gettempdir()).glob("pr-guardian-upload-*"))
    log_hits = 0
    if server_log and server_log.is_file():
        text = server_log.read_text(encoding="utf-8", errors="ignore")
        for marker in ("SKIP_HEADER_ROW", "OrderedDict", "discount_batches",
                       "shell=True"):
            log_hits += text.count(marker)
    return {
        "temp_dirs_left": len(leftovers),
        "source_fragments_in_server_log": log_hits,
        "server_log_checked": bool(server_log and server_log.is_file()),
    }


def browser_case(base_url: str, sample: Path) -> dict:
    out = ROOT / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome")
        except Exception:  # noqa: BLE001 - channel availability differs by machine
            browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{base_url}/upload", wait_until="networkidle")
        page.set_input_files("#upload-input", files=[{
            "name": "csv_report.py", "mimeType": "text/x-python",
            "buffer": (sample / "csv_report.py").read_bytes(),
        }])
        page.click("#upload-run")
        page.wait_for_selector("#upload-results-panel:not([hidden])", timeout=60_000)
        page.wait_for_selector(".finding", timeout=30_000)
        status = page.inner_text("#upload-status")
        summary = page.inner_text("#upload-summary")
        findings = page.locator(".finding").count()
        page.screenshot(path=str(out / "A-browser-findings.png"), full_page=True)
        browser.close()
    return {"status_line": status, "summary": summary, "rendered_findings": findings}


def render(results: dict) -> str:
    lines = [
        "# V1.1 real-world acceptance",
        "",
        "Three self-authored projects (MIT), uploaded through the real HTTP API; case A",
        "also through the real browser page. Nothing was mocked, no model was called.",
        "",
        "## Summary",
        "",
        "| case | files | LOC | findings | ruff | heuristic | status | elapsed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case in results["cases"]:
        lines.append(
            f"| {case['id']} | {case['files']} | {case['lines']} | "
            f"{case['total_findings']} | {case['ruff_findings']} | "
            f"{case['heuristic_findings']} | {case['review_status']} | "
            f"{case['elapsed_s']}s |")
    lines += ["", "## Human review per case", ""]
    for case in results["cases"]:
        lines += [f"### {case['id']} — {case['title']}", "",
                  (f"Upload mode: `{case['upload_mode']}`; license: {case['license']}; "
                  f"analysis: AST `{case['analysis']['ast_status']}`, "
                  f"Ruff `{case['analysis']['ruff_status']}`, "
                  f"worker `{case['analysis']['worker_status']}`, "
                  f"AI `{case['analysis']['ai_status']}`."), "",
                  "| rule | file | line | detected | verdict | actionable | note |",
                  "|---|---|---|---|---|---|---|"]
        for row in case["human_review"]:
            lines.append(
                f"| {row['rule']} | {row['file']} | {row['line'] or '—'} | "
                f"{'yes' if row['detected'] else '**no**'} | {row['verdict']} | "
                f"{row['actionable']} | {row['note']} |")
        lines += ["", "Problems the checks did **not** find:", ""]
        lines += [f"- {item}" for item in case["missed"]]
        gone = ", ".join(rule for rule, ok in case["disappeared"].items() if ok)
        kept = ", ".join(rule for rule, ok in case["disappeared"].items() if not ok)
        lines += ["", (f"After the fix: disappeared = {gone or 'none'}; "
                      f"still present = {kept or 'none'}; "
                      f"review status = `{case['fixed_status']}`."), ""]
    security = results["security"]
    lines += ["## Metrics (denominator: every finding the checks returned)", "",
              "| case | findings | valid | duplicate | false positive | distinct valid |",
              "|---|---|---|---|---|---|"]
    for case in results["cases"]:
        m = case["metrics"]
        lines.append(f"| {case['id']} | {m['findings_total']} | {m['valid']} | "
                     f"{m['duplicate']} | {m['false_positive']} | "
                     f"{m['distinct_valid_issues']} |")
    lines += ["", "## Security", "",
              f"- temp analyser dirs left behind: **{security['temp_dirs_left']}**",
              (f"- source fragments found in the server log: "
              f"**{security['source_fragments_in_server_log']}** "
              f"(log checked: {security['server_log_checked']})")]
    public = results.get("public_instance") or {}
    lines += ["", "## Public instance", "",
              f"- reachable: {public.get('reachable')}",
              f"- `/api/uploads/config` status: {public.get('config_status', 'n/a')}",
              f"- `/upload` status: {public.get('page_status', 'n/a')}",
              f"- uploads exposed: **{public.get('uploads_exposed', 'unknown')}**"]
    browser = results.get("browser") or {}
    if browser:
        lines += ["", "## Browser flow (case A)", "",
                  f"- rendered findings: {browser.get('rendered_findings')}",
                  f"- status line: `{browser.get('status_line')}`", ""]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
