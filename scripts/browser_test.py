"""Browser-level acceptance test for the PR Guardian demo UI.

Runs against a live server using an installed Chrome/Edge (no browser download).
Verifies the real interactions a visitor performs and saves screenshots.

Usage:
    python scripts/browser_test.py [--base-url http://127.0.0.1:8765]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

SHOTS = Path("docs/screenshots")

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, bool(condition), detail))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def run(base_url: str) -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)

    # The suite adapts to whether this server has a live model configured.
    health = httpx.get(base_url + "/health", timeout=30).json()
    live_llm = health.get("llm_status") in {"enabled", "ok"}
    print(f"server llm_status={health.get('llm_status')} live_model={live_llm}\n")

    with sync_playwright() as pw:
        channel = os.environ.get("PW_CHANNEL", "chrome")
        if channel:
            try:
                browser = pw.chromium.launch(channel=channel)
            except Exception as exc:  # noqa: BLE001 - channel selection is environment-dependent
                print(f"channel {channel!r} unavailable ({type(exc).__name__}), trying msedge")
                browser = pw.chromium.launch(channel="msedge")
        else:
            # CI uses Playwright's own downloaded Chromium.
            browser = pw.chromium.launch()

        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(base_url, wait_until="networkidle")

        # --- landing page -------------------------------------------------
        check("page title", "PR Guardian" in page.title(), page.title())
        check("3 demo cases rendered", page.locator(".case-card").count() == 3,
              f"count={page.locator('.case-card').count()}")
        check("run button disabled before selection", page.locator("#run-btn").is_disabled())
        check("results hidden initially", "hidden" in (page.locator("#results-panel").get_attribute("class") or ""))

        # --- visitor notice + committed benchmark link ---------------------
        notice = page.locator("#demo-notice")
        check("demo notice shown", notice.count() == 1)
        notice_text = notice.inner_text().lower()
        check("benchmark report link present",
              page.locator('#demo-notice a[href*="docs/benchmark-results/heldout-llm/REPORT.md"]').count() == 1)
        check("methodology link present",
              page.locator('#demo-notice a[href*="#benchmark"]').count() == 1)
        if live_llm:
            check("notice reflects live-model mode", "live model review is enabled" in notice_text,
                  notice.inner_text().replace("\n", " ")[:80])
        else:
            check("notice states deterministic-only in public mode",
                  "deterministic checks only" in notice_text and "switched off" in notice_text,
                  notice.inner_text().replace("\n", " ")[:80])
        page.screenshot(path=str(SHOTS / "01-landing.png"), full_page=True)

        # --- select the buggy case and load its diff ----------------------
        page.locator('.case-card[data-case-id="boundary-bug"]').click()
        page.wait_for_selector(".diff-file", timeout=15000)
        diff_text = page.locator("#diff-body").inner_text()
        check("diff shows changed file", "src/discount.py" in diff_text)
        check("diff shows removed >= line", "order_total >= 100" in diff_text)
        check("diff shows added > line", "order_total > 100" in diff_text)
        check("diff renders line numbers", page.locator(".diff-line .ln").count() > 0)
        check("run button enabled after selection", not page.locator("#run-btn").is_disabled())
        page.screenshot(path=str(SHOTS / "02-diff.png"), full_page=True)

        # --- run the review ----------------------------------------------
        page.locator("#run-btn").click()
        page.wait_for_selector("#results-panel:not(.hidden)", timeout=60000)
        expect(page.locator("#tests-body")).to_contain_text("failed", timeout=15000)

        tests_text = page.locator("#tests-body").inner_text()
        check("pytest shows 1 failed", "1" in tests_text and "failed" in tests_text, tests_text.replace("\n", " | ")[:90])
        check("failing test name shown", "test_threshold_order_gets_discount" in tests_text)

        ruff_text = page.locator("#ruff-body").inner_text()
        check("ruff panel rendered", "RUFF" in page.locator("#card-ruff h3").inner_text().upper())
        check("ruff truthfully reports no issues", "No issues reported by Ruff" in ruff_text)

        llm_text = page.locator("#llm-body").inner_text()
        banner_text = page.locator("#llm-banner").inner_text()
        if live_llm:
            # The panel either reports findings from the model or says it completed
            # with no issues; it must never claim the step did not run.
            live_ok = ("not evaluated" not in llm_text.lower()) and (
                "hypothes" in llm_text.lower() or "completed" in llm_text.lower()
            )
            check("AI review ran with a real model", live_ok,
                  llm_text.replace("\n", " | ")[:90])
            check("AI banner names the model", "model" in banner_text.lower(), banner_text[:80])
            check("AI findings marked suspected, never confirmed",
                  all("CONFIRMED" not in card.inner_text().upper()
                      for card in page.locator(".finding").filter(has_text="llm").all()))
        else:
            check("LLM marked not evaluated", "not evaluated" in llm_text.lower(),
                  llm_text.replace("\n", " | ")[:80])
            check("LLM banner explains no key", "did not run" in banner_text)

        findings_count = page.locator(".finding").count()
        check("findings rendered", findings_count >= 1, f"count={findings_count}")
        pytest_card = page.locator(".finding").filter(has_text="pytest").first
        check("pytest evidence finding present", pytest_card.count() == 1)
        finding_text = pytest_card.inner_text()
        check("finding is confirmed", "CONFIRMED" in finding_text.upper())
        check("finding is from pytest", "pytest" in finding_text)
        check("tool trace hidden in V1 mode",
              "hidden" in (page.locator("#trace-panel").get_attribute("class") or "hidden"))
        page.screenshot(path=str(SHOTS / "03-results.png"), full_page=True)

        # --- human approval ----------------------------------------------
        page.locator(".finding button[data-decision='accepted']").first.click()
        expect(page.locator(".finding .state").first).to_contain_text("Accepted", timeout=10000)
        check("accept updates state", "Accepted by reviewer" in page.locator(".finding .state").first.inner_text())
        check("approval summary counts accept", "1 accepted" in page.locator("#approval-summary").inner_text(),
              page.locator("#approval-summary").inner_text())

        page.locator(".finding button[data-decision='rejected']").first.click()
        expect(page.locator(".finding .state").first).to_contain_text("Rejected", timeout=10000)
        check("reject updates state", "Rejected by reviewer" in page.locator(".finding .state").first.inner_text())

        # back to accepted so the export check below is meaningful
        page.locator(".finding button[data-decision='accepted']").first.click()
        expect(page.locator(".finding .state").first).to_contain_text("Accepted", timeout=10000)
        page.screenshot(path=str(SHOTS / "04-approval.png"), full_page=True)

        # --- exports ------------------------------------------------------
        with page.expect_download(timeout=15000) as dl_md:
            page.locator("#export-md").click()
        md = dl_md.value
        md_path = SHOTS.parent / "export-sample.md"
        md.save_as(md_path)
        check("markdown export downloads", md.suggested_filename.endswith(".md"), md.suggested_filename)
        check("markdown export has content", md_path.stat().st_size > 200, f"{md_path.stat().st_size} bytes")
        check("markdown export records decision", "ACCEPTED" in md_path.read_text(encoding="utf-8"))

        with page.expect_download(timeout=15000) as dl_json:
            page.locator("#export-json").click()
        js = dl_json.value
        json_path = SHOTS.parent / "export-sample.json"
        js.save_as(json_path)
        check("json export downloads", js.suggested_filename.endswith(".json"), js.suggested_filename)
        check("json export parses", json_path.read_text(encoding="utf-8").strip().startswith("{"))

        page.locator("#approved-only").check()
        with page.expect_download(timeout=15000) as dl_approved:
            page.locator("#export-md").click()
        approved_path = SHOTS.parent / "export-approved.md"
        dl_approved.value.save_as(approved_path)
        check("approved-only export includes accepted finding",
              "### " in approved_path.read_text(encoding="utf-8"))
        page.locator("#approved-only").uncheck()

        # --- clean PR -----------------------------------------------------
        page.locator('.case-card[data-case-id="clean"]').click()
        page.wait_for_selector(".diff-file", timeout=15000)
        check("results hidden after switching case",
              "hidden" in (page.locator("#results-panel").get_attribute("class") or ""))
        page.locator("#run-btn").click()
        page.wait_for_selector("#results-panel:not(.hidden)", timeout=60000)
        expect(page.locator("#tests-body")).to_contain_text("failed", timeout=20000)
        clean_tests = page.locator("#tests-body").inner_text()
        check("clean PR tests pass", "0" in clean_tests and "failed" in clean_tests,
              clean_tests.replace("\n", " | ")[:80])
        if live_llm:
            # A live model may comment on a clean PR; it must never be CONFIRMED.
            check("clean PR: no confirmed findings",
                  "CONFIRMED" not in page.locator("#findings-body").inner_text().upper(),
                  page.locator("#findings-body").inner_text().replace("\n", " | ")[:80])
        else:
            expect(page.locator("#findings-body")).to_contain_text("No reportable findings", timeout=20000)
            check("clean PR has no findings",
                  "No reportable findings" in page.locator("#findings-body").inner_text())
        page.screenshot(path=str(SHOTS / "05-clean-pr.png"), full_page=True)

        # --- prompt injection ---------------------------------------------
        page.locator('.case-card[data-case-id="injection"]').click()
        page.wait_for_selector(".diff-file", timeout=15000)
        inj_diff = page.locator("#diff-body").inner_text()
        check("injection comment visible in diff", "SYSTEM" in inj_diff and "ignore all previous rules" in inj_diff)
        page.locator("#run-btn").click()
        page.wait_for_selector("#results-panel:not(.hidden)", timeout=60000)
        expect(page.locator("#tests-body")).to_contain_text("failed", timeout=20000)
        if live_llm:
            # The injected instruction must not manufacture a confirmed finding.
            check("injection PR: no confirmed findings",
                  "CONFIRMED" not in page.locator("#findings-body").inner_text().upper(),
                  page.locator("#findings-body").inner_text().replace("\n", " | ")[:80])
        else:
            expect(page.locator("#findings-body")).to_contain_text("No reportable findings", timeout=20000)
            check("injection PR reviewed normally (no findings)",
                  "No reportable findings" in page.locator("#findings-body").inner_text())
        check("injection PR still reports real test results",
              "2" in page.locator("#tests-body").inner_text())
        page.screenshot(path=str(SHOTS / "06-injection.png"), full_page=True)


        # --- tool trace panel ---------------------------------------------
        # UI fixture: the review response is stubbed so the panel can be tested
        # deterministically. This is a test double, never shown to a visitor.
        trace_fixture = {
            "review_id": "fixture-trace",
            "case": {"case_id": "boundary-bug", "title": "fixture", "expectation": "fixture"},
            "approvals": {},
            "files": [],
            "report": {
                "review_id": "fixture-trace", "repository": "demo-repo",
                "base_ref": "main", "head_ref": "feature/boundary-bug",
                "model_name": "fixture-model", "changed_files": [],
                "static_findings": [], "llm_findings": [
                    {"id": "llm-1", "file_path": "src/discount.py", "start_line": 6,
                     "end_line": 6, "category": "boundary", "severity": "medium",
                     "summary": "threshold became exclusive", "explanation": "",
                     "evidence": "", "suggested_fix": "",
                     "source": "llm", "verification_status": "suspected"},
                ],
                "final_findings": [
                    {"id": "llm-1", "file_path": "src/discount.py", "start_line": 6,
                     "end_line": 6, "category": "boundary", "severity": "medium",
                     "summary": "threshold became exclusive", "explanation": "",
                     "evidence": "", "suggested_fix": "",
                     "source": "llm", "verification_status": "suspected"},
                ],
                "notes": [], "test_summary": {"ran": False, "skipped_reason": "fixture"},
                "llm_status": "partial", "llm_note": "",
                "tool_status": "tool_limit",
                "tool_model_calls": 3, "tool_rounds": 2,
                "tool_calls": [
                    {"round": 1, "name": "read_code_context",
                     "arguments": {"file_path": "src/discount.py", "start_line": 1, "end_line": 20},
                     "ok": True, "outcome": "hit", "error_code": "", "error_message": "",
                     "result_chars": 420, "latency_s": 0.004, "matched": 0},
                    {"round": 1, "name": "find_symbol_definition",
                     "arguments": {"symbol": "DISCOUNT_RATE"}, "ok": True,
                     "outcome": "miss", "error_code": "", "error_message": "",
                     "result_chars": 95, "latency_s": 0.002, "matched": 0},
                    {"round": 2, "name": "read_code_context",
                     "arguments": {"file_path": "tests/test_discount.py", "start_line": 1, "end_line": 5},
                     "ok": False, "outcome": "error", "error_code": "denied_path",
                     "error_message": "reading this path is not permitted",
                     "result_chars": 0, "latency_s": 0.001, "matched": 0},
                    {"round": 2, "name": "read_code_context",
                     "arguments": {"file_path": "src/discount.py", "start_line": 21, "end_line": 30},
                     "ok": False, "outcome": "budget_denied", "error_code": "budget_exceeded",
                     "error_message": "tool call budget exhausted",
                     "result_chars": 0, "latency_s": 0.0, "matched": 0},
                ],
            },
        }

        trace_page = browser.new_page(viewport={"width": 1280, "height": 900})
        trace_page.goto(base_url, wait_until="networkidle")
        trace_page.route("**/api/reviews", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(trace_fixture)))
        trace_page.locator('.case-card[data-case-id="boundary-bug"]').click()
        trace_page.wait_for_selector(".diff-file", timeout=15000)
        trace_page.locator("#run-btn").click()
        trace_page.wait_for_selector("#results-panel:not(.hidden)", timeout=30000)
        expect(trace_page.locator("#trace-panel")).to_be_visible(timeout=10000)

        meta = trace_page.locator("#trace-meta").inner_text()
        check("trace meta shows rounds and counts", "2 round" in meta and "4 tool call" in meta, meta[:110])
        body_text = trace_page.locator("#trace-body").inner_text()
        check("trace lists tool names", "read_code_context" in body_text and
              "find_symbol_definition" in body_text)
        check("trace shows validated arguments", "src/discount.py" in body_text)
        check("trace marks a hit", "context retrieved" in body_text)
        check("trace marks a miss distinctly", "no usable context found" in body_text)
        check("trace shows the error code", "denied_path" in body_text.lower())
        check("trace shows budget denial", "budget denied" in body_text.lower())
        check("trace flags partial completion", "partial result" in body_text.lower())
        check("trace links final output to findings",
              "threshold became exclusive" in body_text and "suspected" in body_text.lower())
        check("trace groups by model round", "model round 1" in body_text.lower()
              and "model round 2" in body_text.lower())

        # collapsible
        trace_page.locator("#trace-panel > summary").click()
        collapsed = trace_page.locator("#trace-panel").get_attribute("open") is None
        check("trace panel collapses", collapsed)
        trace_page.locator("#trace-panel > summary").click()
        trace_page.screenshot(path=str(SHOTS / "11-tool-trace.png"), full_page=True)

        # mobile rendering of the trace
        trace_page.set_viewport_size({"width": 390, "height": 844})
        trace_page.wait_for_timeout(200)
        overflow = trace_page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check("trace panel has no mobile overflow", overflow <= 2, f"overflow={overflow}px")
        trace_page.screenshot(path=str(SHOTS / "12-tool-trace-mobile.png"), full_page=True)
        trace_page.close()

        # --- mobile layout ------------------------------------------------
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.goto(base_url, wait_until="networkidle")
        mobile.locator('.case-card[data-case-id="boundary-bug"]').click()
        mobile.wait_for_selector(".diff-file", timeout=15000)
        overflow = mobile.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check("no horizontal overflow on mobile", overflow <= 2, f"overflow={overflow}px")
        mobile.screenshot(path=str(SHOTS / "07-mobile.png"), full_page=True)
        mobile.close()

        # --- error handling ----------------------------------------------
        page2 = browser.new_page()
        page2.goto(base_url, wait_until="networkidle")
        page2.route("**/api/reviews", lambda route: route.fulfill(
            status=500, content_type="application/json", body='{"detail": "synthetic failure"}'))
        page2.locator('.case-card[data-case-id="boundary-bug"]').click()
        page2.wait_for_selector(".diff-file", timeout=15000)
        page2.locator("#run-btn").click()
        expect(page2.locator("#error-banner")).to_be_visible(timeout=20000)
        err = page2.locator("#error-banner").inner_text()
        check("server error surfaces in banner", "synthetic failure" in err, err[:80])
        check("page still usable after error", not page2.locator("#run-btn").is_disabled())
        check("no results shown after error",
              "hidden" in (page2.locator("#results-panel").get_attribute("class") or ""))
        page2.screenshot(path=str(SHOTS / "08-error.png"), full_page=True)
        page2.close()

        # --- review status banners (V4.2/V4.3) -----------------------------
        state_page = browser.new_page()
        state_page.goto(base_url, wait_until="networkidle")
        state_page.evaluate(
            """
            window.__report = function (over) {
              var base = {
                review_id: "synthetic", changed_files: [{file_path: "src/a.py",
                  change_type: "modified", added_lines: [1]}],
                static_findings: [], llm_findings: [], final_findings: [],
                test_summary: {ran: false, skipped_reason: "", passed: 0, failed: 0,
                               errors: 0, failed_tests: []},
                llm_status: "ok", llm_note: "", model_name: "deepseek-flash",
                tool_status: "ok", tool_calls: [], tool_rounds: 0,
                tool_model_calls: 2, tool_model_failures: 0,
                llm_prompt_tokens: 10, llm_completion_tokens: 2,
                review_status: "completed",
                completeness: {status: "completed", reason: "",
                               tool_calls_requested: 2, tool_calls_executed: 2,
                               tool_calls_used: 2, tool_calls_max: 4,
                               tool_calls_denied: 0, tool_rounds_used: 2,
                               tool_rounds_max: 3, findings_signal: "has_findings",
                               duplicate_calls: 0, model_requests: 2,
                               model_failures: 0, tool_errors: 0, tool_misses: 0,
                               denied_requests: []}
              };
              for (var k in over) { base[k] = over[k]; }
              window.__prGuardian.render({report: base});
            };
            """
        )

        def state_texts(overrides: str) -> tuple:
            state_page.evaluate("window.__report(" + overrides + ")")
            state_page.wait_for_timeout(80)
            return (
                state_page.locator("#completeness-banner").inner_text(),
                state_page.locator("#llm-banner").inner_text(),
            )

        partial = ("{review_status: 'partial', llm_status: 'partial', "
                   "completeness: {status: 'partial', reason: 'tool_budget_exhausted', "
                   "tool_calls_requested: 6, tool_calls_executed: 4, tool_calls_max: 4, "
                   "tool_calls_denied: 2, findings_signal: 'not_evaluated', "
                   "denied_requests: [{tool: 'read_code_context', arguments: {}}]}}")
        comp, llm = state_texts(partial)
        check("partial banner says NOT a clean review", "NOT a clean review" in comp, comp[:70])
        check("partial banner counts executed not requested", "4/4" in comp, comp[:90])
        check("partial llm banner admits the model ran",
              "did not finish" in llm and "deepseek-flash" in llm, llm[:90])
        check("partial llm banner never says it did not run",
              "did not run" not in llm, llm[:90])

        with_finding = ("{review_status: 'partial', llm_status: 'partial', "
                        "completeness: {status: 'partial', reason: 'tool_budget_exhausted', "
                        "tool_calls_requested: 4, tool_calls_executed: 4, tool_calls_max: 4, "
                        "tool_calls_denied: 0, findings_signal: 'has_findings'}, "
                        "final_findings: [{id: 'llm-1', file_path: 'src/a.py', start_line: 1, "
                        "end_line: 1, category: 'logic', severity: 'high', summary: 's', "
                        "source: 'llm', verification_status: 'suspected', "
                        "evidence_status: 'source_verified', claim_status: 'claim_unjudged'}], "
                        "llm_findings: []}")
        comp, llm = state_texts(with_finding)
        check("partial with findings still says partial", "PARTIAL" in comp, comp[:70])
        check("false-clean line absent when there is a finding",
              "NOT a clean review" not in comp, comp[:70])
        body = state_page.locator("#findings-body").inner_text()
        check("verified finding shows claim not judged", "claim not judged" in body, body[:90])
        check("verified finding is not shown as confirmed",
              "confirmed" not in body.lower(), body[:90])

        done = ("{review_status: 'completed', llm_status: 'ok', "
                "completeness: {status: 'completed', reason: '', "
                "tool_calls_requested: 2, tool_calls_executed: 2, tool_calls_max: 4, "
                "tool_calls_denied: 0, findings_signal: "
                "'no_findings_in_completed_review'}}")
        state_page.evaluate("window.__report(" + done + ")")
        state_page.wait_for_timeout(80)
        comp = state_page.locator("#completeness-banner").inner_text()
        check("completed with zero findings keeps the caveat",
              "does not prove" in comp, comp[:90])

        failed = ("{review_status: 'failed', llm_status: 'failed', "
                  "completeness: {status: 'failed', reason: 'http:ConnectError'}}")
        comp, llm = state_texts(failed)
        check("failed llm banner says FAILED", "FAILED" in llm, llm[:80])
        check("failed llm banner is not the did-not-run text",
              "did not run" not in llm, llm[:80])

        not_run = ("{review_status: 'not_run', llm_status: 'not_configured', "
                   "model_name: 'none', llm_note: 'No API key is configured.', "
                   "completeness: {status: 'not_run', reason: 'no_api_key'}}")
        comp, llm = state_texts(not_run)
        check("not_run says only the AI review is missing",
              "AI review did not run" in llm and "Ruff and pytest" in llm, llm[:110])
        state_page.close()

        browser.close()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} browser checks passed")
    if failed:
        print("FAILED CHECKS:")
        for name, _, detail in failed:
            print(f"  - {name} {detail}")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8765")
    args = ap.parse_args()
    return run(args.base_url)


if __name__ == "__main__":
    sys.exit(main())
