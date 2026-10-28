"""Real DeepSeek evidence chain: tool calls -> registry -> citations -> validator.

Development case only (not the frozen benchmark). Recorded honestly: if the model
returns no usable citations, the report says so.

Run:
    python scripts/evidence_smoke.py --out docs/v4-evidence-smoke
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pr_guardian import pipeline
from pr_guardian.rag_smoke_case import (
    BUG_BRANCH,
    CLEAN_BRANCH,
    build_case,
)
from pr_guardian.settings import Settings


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


def settings_for(backend: str) -> Settings:
    return Settings(
        api_key=os.environ.get("PR_GUARDIAN_API_KEY", "").strip(),
        base_url=os.environ.get("PR_GUARDIAN_BASE_URL", "https://api.deepseek.com"),
        model=os.environ.get("PR_GUARDIAN_MODEL", "deepseek-flash"),
        public_mode=False, allow_live_llm=True, allow_test_execution=False,
        max_live_llm_calls=50, tool_calls_enabled=True,
        max_tool_rounds=4, max_tool_calls=6, tool_backend=backend,
    )


def run(repo: Path, branch: str, case: str, backend: str) -> dict:
    started = time.perf_counter()
    report = pipeline.run_review(repo, "main", branch, use_llm=True, run_tests=False,
                                 settings=settings_for(backend))
    wall = round(time.perf_counter() - started, 3)
    return {
        "case": case,
        "backend": backend,
        "branch": branch,
        "snapshot_sha": _git(repo, "rev-parse", branch).strip(),
        "model_name": report.model_name,
        "llm_status": report.llm_status,
        "tool_status": report.tool_status,
        "model_calls": report.tool_model_calls,
        "model_failures": report.tool_model_failures,
        "prompt_tokens": report.llm_prompt_tokens,
        "completion_tokens": report.llm_completion_tokens,
        "wall_s": wall,
        "evidence_verified": report.evidence_verified,
        "evidence_unverified": report.evidence_unverified,
        "registry_fingerprint": report.evidence_registry_fingerprint,
        "evidence_sources": report.evidence_sources,
        "tool_calls": [
            {"round": c.round, "call_id": c.call_id, "name": c.name,
             "arguments": c.arguments, "outcome": c.outcome, "matched": c.matched}
            for c in report.tool_calls
        ],
        "llm_findings": [f.model_dump(mode="json") for f in report.llm_findings],
        "final_findings": [f.model_dump(mode="json") for f in report.final_findings],
        "trusted_findings": [f.id for f in report.trusted_findings],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("docs/v4-evidence-smoke"))
    args = ap.parse_args()
    if not os.environ.get("PR_GUARDIAN_API_KEY", "").strip():
        print("PR_GUARDIAN_API_KEY is not set: Not Evaluated", file=sys.stderr)
        return 2

    root = Path(tempfile.mkdtemp()) / "case"
    build_case(root)
    payload = {
        "corpus_label": "<evidence-dev-case>",
        "runs": [
            run(root, BUG_BRANCH, "bug", "mcp"),
            run(root, CLEAN_BRANCH, "clean", "mcp"),
        ],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.out / "REPORT.md").write_text(render(payload), encoding="utf-8")
    print(json.dumps({
        "runs": [{"case": r["case"], "status": r["llm_status"],
                   "model": r["model_name"], "calls": r["model_calls"],
                   "tokens": [r["prompt_tokens"], r["completion_tokens"]],
                   "findings": len(r["llm_findings"]),
                   "verified": r["evidence_verified"],
                   "unverified": r["evidence_unverified"],
                   "citations": sum(len(f["evidence_refs"]) for f in r["llm_findings"]),
                   "fingerprint": r["registry_fingerprint"]}
                  for r in payload["runs"]],
    }, indent=2, ensure_ascii=False))
    return 0


def render(payload: dict) -> str:
    lines = [
        "# V4.1 evidence chain — real DeepSeek run",
        "",
        "Development case, not the frozen benchmark. The question is provenance:",
        "does every citation point at code the model actually received?",
        "",
    ]
    for run in payload["runs"]:
        lines += [
            f"## {run['case']} ({run['backend']})",
            "",
            f"* branch `{run['branch']}` at `{run['snapshot_sha']}`",
            (f"* model `{run['model_name']}`, llm_status `{run['llm_status']}`, "
            f"tool_status `{run['tool_status']}`"),
            (f"* model calls {run['model_calls']} (failures {run['model_failures']}), "
            f"tokens {run['prompt_tokens']}/{run['completion_tokens']}, "
            f"wall {run['wall_s']}s"),
            (f"* evidence: {run['evidence_verified']} verified, "
            f"{run['evidence_unverified']} unverified, "
            f"registry `{run['registry_fingerprint']}`"),
            "",
            "| round | tool_call_id | tool | outcome |",
            "|---|---|---|---|",
        ]
        for call in run["tool_calls"]:
            lines.append(f"| {call['round']} | `{call['call_id']}` | {call['name']} | "
                         f"{call['outcome']} |")
        lines += ["", "| finding | evidence_status | claim_status | citations |",
                  "|---|---|---|---|"]
        for finding in run["llm_findings"]:
            cites = ", ".join(
                f"`{r['file_path']}:{r['start_line']}-{r['end_line']}` "
                f"({r['evidence_status']}, {r['tool_call_id'] or 'no id'})"
                for r in finding.get("evidence_refs", [])) or "_none_"
            lines.append(f"| {finding['summary'][:70]} | {finding['evidence_status']} | "
                         f"{finding['claim_status']} | {cites} |")
        if not run["llm_findings"]:
            lines.append("| _no findings_ | | | |")
        lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
