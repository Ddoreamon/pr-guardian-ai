"""Real-LLM smoke test on a handful of benchmark cases.

Purpose: prove the model path works end-to-end before spending money on a full
benchmark run. It uses the same conditions as the benchmark's `llm-only` method:
the model sees the diff and source file, never the ground-truth test file.

Cost control:  --limit caps the number of model calls (default 5).

If PR_GUARDIAN_API_KEY is not set, this exits with status 3 and prints
"NOT EVALUATED" — it never fabricates results.

Usage:
    python scripts/llm_smoke_test.py --limit 5
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmark.cases import build_cases
from benchmark.evaluate import materialise_case
from pr_guardian.git_reader import read_changed_files
from pr_guardian.llm_reviewer import review_with_llm
from pr_guardian.settings import get_settings, load_dotenv
from pr_guardian.validator import validate_findings


def pick_cases(limit: int):
    """A small, mixed sample from the dev split only (never held-out)."""
    dev = [c for c in build_cases() if c.dev_split]
    bugs = [c for c in dev if c.has_bug]
    clean = [c for c in dev if not c.has_bug]
    picked = []
    while len(picked) < limit and (bugs or clean):
        if bugs:
            picked.append(bugs.pop(0))
        if len(picked) < limit and clean:
            picked.append(clean.pop(0))
    return picked[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5, help="maximum model calls (cost cap)")
    ap.add_argument("--out", type=Path, default=Path("docs/llm-smoke-results.json"))
    args = ap.parse_args()

    load_dotenv()
    cfg = get_settings()
    if not cfg.api_key:
        print("NOT EVALUATED: PR_GUARDIAN_API_KEY is not set.")
        print("Set it in .env or the environment, then re-run this script.")
        return 3

    print(f"provider base_url={cfg.base_url}")
    print(f"model={cfg.model}")
    print(f"cases={args.limit} (dev split only)\n")

    records = []
    calls = 0
    with tempfile.TemporaryDirectory() as tmp:
        for case in pick_cases(args.limit):
            if calls >= args.limit:
                break
            repo = materialise_case(case, Path(tmp), include_tests=False)
            files, diff_text = read_changed_files(repo, "main", "pr")
            result = review_with_llm(diff_text, files, settings=cfg)
            calls += 1

            validated = validate_findings(result.findings, files)
            kept = [f for f in validated if f.verification_status.value != "invalid"]
            changed = {line for f in files for line in f.added_lines}
            located = any(
                f.file_path == f"src/{case.module}.py"
                and any(line in changed for line in range(f.start_line, f.end_line + 1))
                for f in kept
            )
            record = {
                "case_id": case.case_id,
                "has_bug": case.has_bug,
                "category": case.category,
                "schema_valid": result.schema_valid,
                "raw_findings": len(result.findings),
                "kept_findings": len(kept),
                "notes": result.notes,
                "detected_on_changed_line": located,
                "findings": [
                    {
                        "file_path": f.file_path,
                        "start_line": f.start_line,
                        "source": f.source.value,
                        "status": f.verification_status.value,
                        "summary": f.summary,
                    }
                    for f in kept
                ],
            }
            records.append(record)
            verdict = "detected" if located else "not detected"
            print(f"{case.case_id:14} has_bug={case.has_bug!s:5} schema_valid={result.schema_valid!s:5} "
                  f"findings={len(kept)} -> {verdict}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "model": cfg.model,
                "base_url": cfg.base_url,
                "calls_made": calls,
                "cases": records,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    detected = sum(1 for r in records if r["has_bug"] and r["detected_on_changed_line"])
    buggy = sum(1 for r in records if r["has_bug"])
    false_alarms = sum(1 for r in records if not r["has_bug"] and r["kept_findings"])
    clean = sum(1 for r in records if not r["has_bug"])
    print(f"\ncalls made: {calls}")
    print(f"buggy cases: {buggy}, detected on a changed line: {detected}")
    print(f"clean cases: {clean}, with any finding: {false_alarms}")
    print(f"results written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
