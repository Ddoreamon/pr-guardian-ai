"""Real-LLM smoke test on a handful of benchmark cases.

Purpose: prove the model path works end-to-end, and record *verifiable* detail —
per-call status, schema validity, whether the defect was actually located,
false alarms on clean PRs, provider-reported token usage and latency.

Conditions match the benchmark's `llm-only` method: the model sees the diff and
source file, never the ground-truth test file.

Cost: --limit caps the number of model calls (default 5). A USD estimate is only
produced when a price table is supplied via environment variables:
    PR_GUARDIAN_PRICE_INPUT_PER_M   USD per 1M prompt tokens
    PR_GUARDIAN_PRICE_OUTPUT_PER_M  USD per 1M completion tokens
Otherwise cost is reported as "not computed"; tokens are always recorded.

Without PR_GUARDIAN_API_KEY this exits 3 and prints NOT EVALUATED. It never
fabricates results.

Usage:
    python scripts/llm_smoke_test.py --limit 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmark.cases import build_cases
from benchmark.evaluate import materialise_case
from pr_guardian.git_reader import read_changed_files
from pr_guardian.llm_reviewer import review_with_llm
from pr_guardian.models import VerificationStatus
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


def cost_estimate(prompt_tokens: int, completion_tokens: int) -> float | None:
    """USD estimate, only when both prices are configured."""
    try:
        in_price = float(os.environ["PR_GUARDIAN_PRICE_INPUT_PER_M"])
        out_price = float(os.environ["PR_GUARDIAN_PRICE_OUTPUT_PER_M"])
    except (KeyError, ValueError):
        return None
    return round(prompt_tokens / 1_000_000 * in_price + completion_tokens / 1_000_000 * out_price, 6)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5, help="maximum model calls (cost cap)")
    ap.add_argument("--out", type=Path, default=Path("docs/llm-smoke-results.json"))
    args = ap.parse_args()

    load_dotenv()
    cfg = get_settings()
    if not cfg.api_key:
        print("NOT EVALUATED: PR_GUARDIAN_API_KEY is not set.")
        print("Add it to .env (gitignored) or export it, then re-run this script.")
        return 3

    print(f"provider : {cfg.base_url}")
    print(f"model    : {cfg.model}")
    print(f"cases    : {args.limit} (dev split only, test file hidden)\n")

    header = (f"{'case':14} {'bug?':5} {'call':5} {'schema':6} {'kept':4} "
              f"{'hit':4} {'false':5} {'tokens':7} {'lat(s)':6}")
    print(header)
    print("-" * len(header))

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

            usage = result.usage or {}
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

            validated = validate_findings(result.findings, files)
            kept = [f for f in validated if f.verification_status != VerificationStatus.INVALID]
            invalid = [f for f in validated if f.verification_status == VerificationStatus.INVALID]
            merged = len(result.findings) - len(validated)

            changed = {line for f in files for line in f.added_lines}
            target = f"src/{case.module}.py"
            hits = [
                f for f in kept
                if f.file_path == target
                and any(line in changed for line in range(f.start_line, f.end_line + 1))
            ]
            located = bool(hits)
            flagged = any(f.file_path == target for f in kept)
            false_alarm = (not case.has_bug) and flagged

            print(f"{case.case_id:14} {case.has_bug!s:5} "
                  f"{'ok' if result.error == '' else 'FAIL':5} "
                  f"{result.schema_valid!s:6} {len(kept):<4} {located!s:4} {false_alarm!s:5} "
                  f"{prompt_tokens + completion_tokens:<7} {result.latency_s:<6}")

            records.append({
                "case_id": case.case_id,
                "has_bug": case.has_bug,
                "category": case.category,
                "call_ok": result.error == "",
                "error": result.error,
                "schema_valid": result.schema_valid,
                "raw_findings": len(result.findings),
                "kept_findings": len(kept),
                "merged_duplicates": merged,
                "invalid_findings": [
                    {"file_path": f.file_path, "start_line": f.start_line, "summary": f.summary}
                    for f in invalid
                ],
                "detected_on_changed_line": located,
                "false_alarm": false_alarm,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "latency_s": result.latency_s,
                "findings": [
                    {
                        "file_path": f.file_path,
                        "start_line": f.start_line,
                        "end_line": f.end_line,
                        "severity": f.severity,
                        "category": f.category,
                        "status": f.verification_status.value,
                        "summary": f.summary,
                        "evidence": f.evidence,
                    }
                    for f in kept
                ],
            })

    total_prompt = sum(r["prompt_tokens"] for r in records)
    total_completion = sum(r["completion_tokens"] for r in records)
    buggy = [r for r in records if r["has_bug"]]
    clean = [r for r in records if not r["has_bug"]]
    detected = sum(1 for r in buggy if r["detected_on_changed_line"])
    false_alarms = sum(1 for r in clean if r["false_alarm"])
    schema_ok = sum(1 for r in records if r["schema_valid"])
    calls_ok = sum(1 for r in records if r["call_ok"])
    cost = cost_estimate(total_prompt, total_completion)

    summary = {
        "model": cfg.model,
        "base_url": cfg.base_url,
        "calls_made": calls,
        "calls_ok": calls_ok,
        "schema_valid_calls": schema_ok,
        "buggy_cases": len(buggy),
        "buggy_detected": detected,
        "clean_cases": len(clean),
        "clean_false_alarms": false_alarms,
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "total_tokens": total_prompt + total_completion,
        "cost_usd_estimate": cost,
        "cost_note": ("estimated from configured price table" if cost is not None
                      else "not computed: no price table configured"),
    }

    print("\n=== summary ===")
    print(f"calls: {calls} (ok={calls_ok}), schema valid: {schema_ok}/{calls}")
    print(f"buggy cases: {len(buggy)}, defect located: {detected}")
    print(f"clean cases: {len(clean)}, false alarms: {false_alarms}")
    print(f"tokens: prompt={total_prompt} completion={total_completion} "
          f"total={total_prompt + total_completion}")
    print("cost: " + (f"${cost:.6f}" if cost is not None else "not computed (no price table)"))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({**summary, "cases": records}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"results written to {args.out}")

    if calls_ok != calls:
        print("\nWARNING: some model calls failed; see 'error' fields above.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
