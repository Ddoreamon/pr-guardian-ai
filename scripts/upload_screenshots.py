"""Capture the Upload & review screenshots for the V1.1 documentation.

Screenshots are evidence of the real page, produced by driving the real server with
Playwright. Nothing is mocked: the upload is analysed by the real worker.

    python scripts/upload_screenshots.py --base-url http://127.0.0.1:8801
"""

from __future__ import annotations

import argparse
import io
import os
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("docs/screenshots")

SAMPLE = (
    "import os\n"
    "import subprocess\n"
    "\n"
    "API_TOKEN = \"sk-live-example-not-a-real-key\"\n"
    "\n"
    "\n"
    "def run(cmd, items=[]):\n"
    "    items.append(cmd)\n"
    "    try:\n"
    "        return subprocess.run(cmd, shell=True)\n"
    "    except:\n"
    "        return None\n"
    "\n"
    "\n"
    "def evaluate(text):\n"
    "    return eval(text)\n"
)


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("demo_pkg/tasks.py", SAMPLE)
        archive.writestr("demo_pkg/clean.py", "def add(a, b):\n    return a + b\n")
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        channel = os.environ.get("PW_CHANNEL", "chrome")
        try:
            browser = pw.chromium.launch(channel=channel)
        except Exception:  # noqa: BLE001 - channel availability is environment-specific
            browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{args.base_url}/upload", wait_until="networkidle")
        page.screenshot(path=str(OUT / "13-upload-empty.png"), full_page=True)

        page.set_input_files("#upload-input", files=[{
            "name": "tasks.zip", "mimeType": "application/zip", "buffer": _zip(),
        }])
        page.wait_for_selector(".upload-item", timeout=10_000)
        page.screenshot(path=str(OUT / "14-upload-selected.png"), full_page=True)

        page.click("#upload-run")
        page.wait_for_selector("#upload-results-panel:not([hidden])", timeout=60_000)
        page.wait_for_selector(".finding", timeout=30_000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / "15-upload-findings.png"), full_page=True)

        summary = page.inner_text("#upload-summary")
        status = page.inner_text("#upload-status")
        print("summary:", summary)
        print("status:", status)
        browser.close()
    for name in ("13-upload-empty.png", "14-upload-selected.png",
                 "15-upload-findings.png"):
        path = OUT / name
        print(name, path.stat().st_size, "bytes", "exists" if path.exists() else "MISSING")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
