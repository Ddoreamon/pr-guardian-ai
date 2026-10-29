"""Verify which commit a deployment actually serves, without trusting version strings.

A version string in `/health` is a claim made by code; the code that answers can be an
older build. This script fingerprints the static assets the deployment serves and
compares them with the same files at a named Git ref:

    python scripts/verify_deployed_build.py --base-url https://host --ref v1.0.0-rc2

Evidence collected:
  * `/health` version, public_mode, llm_status (claims only);
  * SHA-256 of `/static/app.js` and `/static/style.css` from the deployment;
  * SHA-256 of the same files at the ref (from the local clone);
  * the ref's commit SHA and the commit recorded in the working tree.

Exit codes: 0 = deploys the named ref, 1 = mismatch, 2 = could not verify.

Why this works: a static asset is served verbatim from the image that answered the
request, so its hash identifies the build. It needs no application code change and no
Render API token. Render's own deploy record (dashboard "Deployed commit", or
`GET /v1/services/{id}/deploys`) is the primary source; this is the independent check
that the running image matches that record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys

import httpx

ASSETS = ("/static/app.js", "/static/style.css")


def sha256(data: bytes) -> str:
    """Line endings are normalised: a Windows checkout and a Linux image must agree."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def git(*args: str) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: "
                         f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8", "replace").strip()


def local_hashes(ref: str) -> dict[str, str]:
    out = {}
    for asset in ASSETS:
        path = "app" + asset.replace("/static", "/static")
        out[asset] = sha256(subprocess.run(["git", "show", f"{ref}:{path}"],
                                           capture_output=True, check=True).stdout)
    return out


def remote_hashes(base_url: str) -> dict[str, str]:
    out = {}
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=30,
                      follow_redirects=True) as client:
        for asset in ASSETS:
            response = client.get(asset)
            response.raise_for_status()
            out[asset] = sha256(response.content)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--ref", required=True, help="tag or commit the build should be")
    parser.add_argument("--expect-text", default="",
                        help="optional phrase the rendered page must contain")
    args = parser.parse_args()

    report: dict = {"base_url": args.base_url, "ref": args.ref,
                    "ref_commit": git("rev-parse", f"{args.ref}^{{commit}}"),
                    "worktree_commit": git("rev-parse", "HEAD")}
    try:
        report["local"] = local_hashes(args.ref)
        report["remote"] = remote_hashes(args.base_url)
        with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30) as client:
            health = client.get("/health")
            health.raise_for_status()
            report["health"] = health.json()
            if args.expect_text:
                page = client.get("/")
                page.raise_for_status()
                report["page_contains_expected_text"] = args.expect_text in page.text
    except Exception as exc:  # noqa: BLE001 - any failure means "cannot verify"
        report["status"] = "unverifiable"
        report["error"] = str(exc)
        print(json.dumps(report, indent=2))
        return 2

    mismatches = [asset for asset in ASSETS
                  if report["local"][asset] != report["remote"][asset]]
    if report.get("page_contains_expected_text") is False:
        mismatches.append("page text")
    report["status"] = "match" if not mismatches else "mismatch"
    report["mismatches"] = mismatches
    report["limitation"] = ("static assets identify the image but cannot distinguish "
                            "commits that only changed templates or tests; the Render "
                            "deploy record is the primary source for the commit SHA")
    report["health_version"] = report["health"].get("version")
    report["public_mode"] = report["health"].get("public_mode")
    report["llm_status"] = report["health"].get("llm_status")
    print(json.dumps(report, indent=2))
    return 0 if not mismatches else 1


if __name__ == "__main__":
    sys.exit(main())
