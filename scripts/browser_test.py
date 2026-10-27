"""Browser-level acceptance test for the PR Guardian demo UI.

Runs against a live server using an installed Chrome/Edge (no browser download).
Verifies the real interactions a visitor performs and saves screenshots.

Usage:
    python scripts/browser_test.py [--base-url http://127.0.0.1:8765]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

SHOTS = Path("docs/screenshots")

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, bool(condition), detail))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def run(base_url: str) -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome")
        except Exception as exc:  # noqa: BLE001 - fall back to Edge if Chrome channel unavailable
            print(f"chrome channel unavailable ({type(exc).__name__}), trying msedge")
            browser = pw.chromium.launch(channel="msedge")

        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(base_url, wait_until="networkidle")

        # --- landing page -------------------------------------------------
        check("page title", "PR Guardian" in page.title(), page.title())
        check("3 demo cases rendered", page.locator(".case-card").count() == 3,
              f"count={page.locator('.case-card').count()}")
        check("run button disabled before selection", page.locator("#run-btn").is_disabled())
        check("results hidden initially", "hidden" in (page.locator("#results-panel").get_attribute("class") or ""))
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
        check("LLM marked not evaluated", "not evaluated" in llm_text.lower(), llm_text.replace("\n", " | ")[:80])
        check("LLM banner explains no key", "did not run" in page.locator("#llm-banner").inner_text())

        check("finding rendered", page.locator(".finding").count() == 1,
              f"count={page.locator('.finding').count()}")
        finding_text = page.locator(".finding").first.inner_text()
        check("finding is confirmed", "CONFIRMED" in finding_text.upper())
        check("finding is from pytest", "pytest" in finding_text)
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
        expect(page.locator("#findings-body")).to_contain_text("No reportable findings", timeout=20000)
        clean_tests = page.locator("#tests-body").inner_text()
        check("clean PR tests pass", "0" in clean_tests and "failed" in clean_tests, clean_tests.replace("\n", " | ")[:80])
        check("clean PR has no findings", "No reportable findings" in page.locator("#findings-body").inner_text())
        page.screenshot(path=str(SHOTS / "05-clean-pr.png"), full_page=True)

        # --- prompt injection ---------------------------------------------
        page.locator('.case-card[data-case-id="injection"]').click()
        page.wait_for_selector(".diff-file", timeout=15000)
        inj_diff = page.locator("#diff-body").inner_text()
        check("injection comment visible in diff", "SYSTEM" in inj_diff and "ignore all previous rules" in inj_diff)
        page.locator("#run-btn").click()
        page.wait_for_selector("#results-panel:not(.hidden)", timeout=60000)
        expect(page.locator("#findings-body")).to_contain_text("No reportable findings", timeout=20000)
        check("injection PR reviewed normally (no findings)",
              "No reportable findings" in page.locator("#findings-body").inner_text())
        check("injection PR still reports real test results",
              "2" in page.locator("#tests-body").inner_text())
        page.screenshot(path=str(SHOTS / "06-injection.png"), full_page=True)

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
