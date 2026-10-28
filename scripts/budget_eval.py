"""Run the frozen V4.4 dev cases against a real model and measure the budget.

Each case is a deterministic repository (fixed commit dates) with a hidden test
that proves its label. The agent cannot read `tests/`, so the label never leaks.

Metrics per case: provider requests/failures, tool requested/executed/denied,
errors/misses, required-evidence coverage (citable full-source evidence only),
review_status, finding correctness, source verification, tokens and latency.

Run:
    python scripts/budget_eval.py --out docs/v4.4-baseline
    python scripts/budget_eval.py --out docs/v4.4-ab --planner
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pr_guardian import pipeline
from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.repo_index import build_index
from pr_guardian.settings import Settings
from scripts.budget_eval_cases import CASES, build_case

FROZEN = Path("docs/v4.4-dev-eval/cases.json")


def _frozen_requirements() -> dict[str, list[str]]:
    """Required evidence comes from the frozen manifest, not from the generator."""
    if not FROZEN.is_file():
        return {}
    data = json.loads(FROZEN.read_text(encoding="utf-8"))
    return {c["id"]: c["required_evidence"] for c in data.get("cases", [])}


def _parse_range(text: str) -> tuple[str, int, int]:
    path, _, span = text.partition(":")
    start, _, end = span.partition("-")
    return path, int(start), int(end)


def _covered(required: tuple[str, int, int], sources: list[dict]) -> bool:
    """A required range counts only when full source lines were delivered."""
    path, start, end = required
    for entry in sources:
        if entry.get("kind") not in {"read", "symbol"}:
            continue
        if entry.get("file_path") != path:
            continue
        if entry.get("start_line", 0) <= start and end <= entry.get("end_line", 0):
            return True
    return False


def _gold_span(repo: Path, ref: str, gold: dict) -> tuple[int, int] | None:
    codebase = TrustedCodebase.from_git(repo, ref)
    index = build_index(codebase)
    for chunk in index.chunks:
        if chunk.file_path == gold["file_path"] and chunk.qualname == gold["qualname"]:
            return chunk.start_line, chunk.end_line
    return None


def _judge(findings: list, gold: dict, span, label: str, head_file: str) -> dict:
    """Anchored detection: did the review flag the right change?

    A finding counts when it is anchored in the changed file or in the gold file —
    a reviewer may legitimately report the defect at the call site rather than at
    the callee. `gold_hit` additionally records whether the callee itself was cited.
    """
    if label == "clean":
        return {"finding_correct": not findings,
                "finding_false_positive": bool(findings), "gold_hit": None}
    anchored = any(f.file_path in {head_file, gold["file_path"]} for f in findings)
    gold_hit = False
    for finding in findings:
        if finding.file_path != gold["file_path"]:
            continue
        if span and not (finding.start_line <= span[1] and span[0] <= finding.end_line):
            continue
        gold_hit = True
    return {"finding_correct": anchored,
            "finding_false_positive": bool(findings) and not anchored,
            "gold_hit": gold_hit}


def run_case(case: dict, root: Path, planner: bool) -> dict:
    repo = build_case(root, case)
    ref = case["branch"]
    sha = __import__("subprocess").run(
        ["git", "-C", str(repo), "rev-parse", ref], capture_output=True,
        text=True, check=True).stdout.strip()
    settings = Settings(
        api_key=os.environ.get("PR_GUARDIAN_API_KEY", "").strip(),
        base_url=os.environ.get("PR_GUARDIAN_BASE_URL", "https://api.deepseek.com"),
        model=os.environ.get("PR_GUARDIAN_MODEL", "deepseek-flash"),
        public_mode=False, allow_live_llm=True, allow_test_execution=False,
        max_live_llm_calls=50, tool_calls_enabled=True,
        max_tool_rounds=3, max_tool_calls=4, tool_backend="local",
        evidence_planner=planner,
    )
    started = time.perf_counter()
    report = pipeline.run_review(repo, "main", ref, use_llm=True, run_tests=False,
                                 settings=settings)
    latency = round(time.perf_counter() - started, 2)

    required_ranges = _frozen_requirements().get(case["id"], case["required_evidence"])
    required = [_parse_range(r) for r in required_ranges]
    obtained = sum(1 for r in required if _covered(r, report.evidence_sources))
    span = _gold_span(repo, ref, case["gold"])
    verdict = _judge(report.final_findings, case["gold"], span, case["label"],
                     case["head_file"])
    c = report.completeness
    return {
        "case": case["id"], "label": case["label"], "sha": sha,
        "planner": planner,
        "model_requests": c.model_requests, "model_failures": c.model_failures,
        "tool_calls_requested": c.tool_calls_requested,
        "tool_calls_executed": c.tool_calls_executed,
        "tool_calls_denied": c.tool_calls_denied,
        "tool_errors": c.tool_errors, "tool_misses": c.tool_misses,
        "required_evidence_obtained": obtained,
        "evidence_required_total": len(required),
        "review_status": report.review_status,
        "findings_total": len(report.final_findings),
        "findings_source_verified": len(report.source_verified_findings),
        "finding_correct": verdict["finding_correct"],
        "finding_gold_file_hit": verdict["gold_hit"],
        "finding_false_positive": verdict["finding_false_positive"],
        "prompt_tokens": report.llm_prompt_tokens,
        "completion_tokens": report.llm_completion_tokens,
        "latency_s": latency,
        "calls": [
            {"round": t.round, "tool": t.name, "arguments": t.arguments,
             "outcome": t.outcome}
            for t in report.tool_calls
        ],
        "findings": [
            {"file_path": f.file_path, "start_line": f.start_line,
             "end_line": f.end_line, "summary": f.summary[:160],
             "evidence_status": f.evidence_status,
             "verification_status": f.verification_status}
            for f in report.final_findings
        ],
    }


def aggregate(rows: list[dict]) -> dict:
    bugs = [r for r in rows if r["label"] == "bug"]
    cleans = [r for r in rows if r["label"] == "clean"]
    tp = sum(1 for r in bugs if r["finding_correct"])
    fn = len(bugs) - tp
    fp = sum(1 for r in cleans if r["finding_false_positive"])
    tn = len(cleans) - fp
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else None)
    return {
        "cases": len(rows),
        "accuracy": round((tp + tn) / len(rows), 4),
        "precision": None if precision is None else round(precision, 4),
        "recall": None if recall is None else round(recall, 4),
        "f1": None if f1 is None else round(f1, 4),
        "clean_false_alarm_rate": round(fp / len(cleans), 4) if cleans else None,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "evidence_coverage": round(
            sum(r["required_evidence_obtained"] for r in rows)
            / max(1, sum(r["evidence_required_total"] for r in rows)), 4),
        "executed_calls": sum(r["tool_calls_executed"] for r in rows),
        "requested_calls": sum(r["tool_calls_requested"] for r in rows),
        "denied_calls": sum(r["tool_calls_denied"] for r in rows),
        "partial_rate": round(sum(1 for r in rows if r["review_status"] == "partial")
                              / len(rows), 4),
        "verified_findings": sum(r["findings_source_verified"] for r in rows),
        "gold_file_hits": sum(1 for r in rows if r.get("finding_gold_file_hit")),
        "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
        "completion_tokens": sum(r["completion_tokens"] for r in rows),
        "mean_latency_s": round(statistics.mean(r["latency_s"] for r in rows), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--planner", action="store_true")
    ap.add_argument("--cases", nargs="*", default=None)
    args = ap.parse_args()
    if not os.environ.get("PR_GUARDIAN_API_KEY", "").strip():
        print("PR_GUARDIAN_API_KEY is not set: Not Evaluated", file=sys.stderr)
        return 2

    root = Path(tempfile.mkdtemp(prefix="pr-guardian-v44-run-"))
    selected = [c for c in CASES if not args.cases or c["id"] in args.cases]
    rows = [run_case(case, root, args.planner) for case in selected]
    payload = {"planner": args.planner, "runs": rows, "summary": aggregate(rows)}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.out / "REPORT.md").write_text(render(payload), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    return 0


def render(payload: dict) -> str:
    s = payload["summary"]
    lines = [
        "# V4.4 evaluation — " + ("evidence-aware planner" if payload["planner"]
                                   else "baseline"),
        "",
        "Real `deepseek-flash`, six frozen dev cases, budgets 4 calls / 3 rounds.",
        "Bug labels are proven by a hidden test the agent cannot read.",
        "",
        "## Summary",
        "",
        "| metric | value |", "|---|---|",
        f"| cases | {s['cases']} |",
        f"| TP / FP / FN / TN | {s['tp']} / {s['fp']} / {s['fn']} / {s['tn']} |",
        f"| accuracy | {s['accuracy']:.2%} |",
        f"| precision | {'n/a' if s['precision'] is None else format(s['precision'], '.2%')} |",
        f"| recall | {'n/a' if s['recall'] is None else format(s['recall'], '.2%')} |",
        f"| F1 | {'n/a' if s['f1'] is None else format(s['f1'], '.2%')} |",
        f"| clean false-alarm rate | {s['clean_false_alarm_rate']:.2%} |",
        f"| required evidence coverage | {s['evidence_coverage']:.2%} |",
        (f"| tool calls requested / executed / denied | {s['requested_calls']} / "
        f"{s['executed_calls']} / {s['denied_calls']} |"),
        f"| partial rate | {s['partial_rate']:.2%} |",
        f"| source-verified findings | {s['verified_findings']} |",
        f"| prompt / completion tokens | {s['prompt_tokens']} / {s['completion_tokens']} |",
        f"| mean latency | {s['mean_latency_s']}s |",
        "",
        "## Per case",
        "",
        "| case | label | status | calls req/exec/den | evidence | findings | correct | verified | latency |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in payload["runs"]:
        lines.append(
            f"| {r['case']} | {r['label']} | {r['review_status']} | "
            f"{r['tool_calls_requested']}/{r['tool_calls_executed']}/"
            f"{r['tool_calls_denied']} | "
            f"{r['required_evidence_obtained']}/{r['evidence_required_total']} | "
            f"{r['findings_total']} | {'yes' if r['finding_correct'] else 'no'} | "
            f"{r['findings_source_verified']} | {r['latency_s']}s |")
    lines += ["", "## Tool calls", ""]
    for r in payload["runs"]:
        lines.append(f"### {r['case']}")
        lines.append("")
        lines.append("| round | tool | arguments | outcome |")
        lines.append("|---|---|---|---|")
        for c in r["calls"]:
            lines.append(f"| {c['round']} | {c['tool']} | "
                         f"`{json.dumps(c['arguments'], ensure_ascii=False)[:90]}` | "
                         f"{c['outcome']} |")
        lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
