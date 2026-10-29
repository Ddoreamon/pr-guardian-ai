"""Release 1.0 demo evidence: one real run per scenario, tied to exact SHAs.

Three scenarios, each recorded with the mode that can actually produce it:

* A  bug PR        — Ruff + pytest evidence, no model needed (public-mode capable)
* B  clean PR      — completed review, no findings, honest wording
* C  partial AI    — real local model run with a one-call tool budget, or the
                     committed recorded trace when no key is available

The script never fabricates a model response: if a scenario needs a live model and no
key is configured, it records the committed artefact and says so in NOTES.md.

Run:
    python scripts/release_demo.py            # uses a live key only if present
    python scripts/release_demo.py --offline  # never calls a model
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.demo_cases import BASE_BRANCH, CASES_BY_ID, demo_repo
from pr_guardian import pipeline
from pr_guardian.settings import get_settings
from pr_guardian.version import __version__

OUT = Path("docs/release-1.0/demo")


def _sha(repo: Path, ref: str) -> str:
    import subprocess
    return subprocess.run(["git", "-C", str(repo), "rev-parse", ref],
                          capture_output=True, text=True, check=True).stdout.strip()


def _export_markdown(report) -> str:
    lines = [f"# Review {report.review_id}", "",
             f"- status: `{report.review_status}`",
             f"- model: `{report.model_name or 'not used'}`",
             (f"- findings: {len(report.final_findings)} "
             f"(source verified: {len(report.source_verified_findings)})"),
             ""]
    for finding in report.final_findings:
        lines.append(f"## {finding.severity} {finding.file_path}:"
                     f"{finding.start_line}-{finding.end_line}")
        lines.append(f"{finding.summary}")
        lines.append(f"evidence: `{finding.evidence_status}` · "
                     f"verification: `{finding.verification_status}`")
        lines.append("")
    if not report.final_findings:
        lines.append("No findings. This is the absence of a finding, not a proof that "
                     "the change is correct.")
    return "\n".join(lines) + "\n"


def run_case(case_id: str, *, use_llm: bool, max_calls: int | None, label: str,
             note: str) -> dict:
    case = CASES_BY_ID[case_id]
    repo = demo_repo.path()
    overrides = {}
    if max_calls is not None:
        overrides = {"max_tool_calls": max_calls, "max_tool_rounds": 2,
                     "tool_calls_enabled": True}
    settings = (dataclasses.replace(get_settings(), **overrides)
                if overrides else get_settings())
    report = pipeline.run_review(repo, BASE_BRANCH, case.branch, use_llm=use_llm,
                                 run_tests=True, settings=settings)
    folder = OUT / label
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8")
    (folder / "report.md").write_text(_export_markdown(report), encoding="utf-8")
    (folder / "NOTES.md").write_text(
        f"# Demo {label}\n\n"
        f"- case: `{case_id}` ({case.title})\n"
        f"- expectation: {case.expectation}\n"
        f"- mode: {note}\n"
        f"- base: `{_sha(repo, BASE_BRANCH)}`\n"
        f"- head: `{_sha(repo, case.branch)}`\n"
        f"- product version: {__version__}\n"
        f"- review_status: `{report.review_status}`\n"
        f"- findings: {len(report.final_findings)}\n"
        f"- source verified: {len(report.source_verified_findings)}\n",
        encoding="utf-8")
    return {"label": label, "case": case_id, "status": report.review_status,
            "findings": len(report.final_findings),
            "verified": len(report.source_verified_findings),
            "head": _sha(repo, case.branch), "mode": note}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    demo_repo.path()
    if demo_repo.error:
        print(f"demo repository failed: {demo_repo.error}", file=sys.stderr)
        return 2

    has_key = bool(os.environ.get("PR_GUARDIAN_API_KEY", "").strip())
    results = []

    # A: deterministic evidence only — exactly what the public demo can do
    results.append(run_case("boundary-bug", use_llm=False, max_calls=None,
                            label="A_bug_static",
                            note="public mode: Ruff + pytest evidence, no model call"))
    # B: clean change, deterministic pass
    results.append(run_case("clean", use_llm=False, max_calls=None,
                            label="B_clean_static",
                            note="public mode: Ruff + pytest evidence, no model call"))
    # C: partial AI review — real local run, or the committed trace
    if has_key and not args.offline:
        results.append(run_case("boundary-bug", use_llm=True, max_calls=1,
                                label="C_partial_ai",
                                note=("local AI mode: real DeepSeek call, tool budget "
                                      "forced to 1 so the review stops early")))
    else:
        results.append({"label": "C_partial_ai", "case": "boundary-bug",
                        "status": "recorded", "findings": None, "verified": None,
                        "head": "", "mode": "recorded artefact (no local key used)"})
        (OUT / "C_partial_ai").mkdir(parents=True, exist_ok=True)
        (OUT / "C_partial_ai" / "NOTES.md").write_text(
            "# Demo C — partial AI review\n\n"
            "No local key was available in this run, so no live call was made. The\n"
            "committed V4.4 trace set contains real partial reviews produced by the\n"
            "tool budget:\n\n"
            "- `docs/v4.4-baseline/results.json` (real DeepSeek run, status partial)\n"
            "- `docs/v4.4-baseline/REPORT.md`\n\n"
            "A recorded trace is labelled as such; it is never presented as a live\n"
            "model response.\n", encoding="utf-8")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.json").write_text(
        json.dumps({"version": __version__, "results": results}, indent=2),
        encoding="utf-8")
    for result in results:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
