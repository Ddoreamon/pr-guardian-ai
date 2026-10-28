"""Offline evaluation of the V4.5 sufficiency gate. No model calls.

Two data sources:

* **dev** — the six V4.4 cases with their *real recorded* tool traces, replayed
  through `TrustedCodebase` to rebuild the evidence registry;
* **held-out** — six new cases with scripted evidence profiles (which files are
  delivered), plus negative controls (wrong SHA, truncated read, search only).

The gate never sees a gold label: it derives obligations from the diff, ASTs and the
index, then checks them against the delivered evidence.
"""

from __future__ import annotations

import json
import logging
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.evidence import EvidenceRegistry
from pr_guardian.git_reader import read_changed_files
from pr_guardian.sufficiency import (
    ASSESSMENT_VERSION,
    INSUFFICIENT,
    SUFFICIENT,
    UNKNOWN,
    assess,
    derive_obligations,
)
from pr_guardian.tool_calling import ToolCallRecord
from scripts.budget_eval_cases import CASES as DEV_CASES
from scripts.budget_eval_cases import build_case as build_dev
from scripts.sufficiency_cases import CASES as HELD_CASES
from scripts.sufficiency_cases import PROFILES
from scripts.sufficiency_cases import build_case as build_held

LOGGER = logging.getLogger("pr_guardian.sufficiency_eval")

DEV_RESULTS = ["docs/v4.4-baseline/results.json", "docs/v4.4-planner/results.json"]


def _registry_from_calls(codebase, calls: list[dict]) -> EvidenceRegistry:
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    for index, call in enumerate(calls):
        if call["outcome"] != "hit":
            continue
        name, args = call["tool"], call["arguments"]
        try:
            if name == "read_code_context":
                payload = codebase.read_code_context(
                    args.get("file_path"), args.get("start_line"), args.get("end_line"))
            elif name == "find_symbol_definition":
                payload = codebase.find_symbol_definition(args.get("symbol"))
            else:
                payload = codebase.search_code(args.get("query"), args.get("top_k", 5))
        except Exception:  # a failed replay is simply absent evidence
            LOGGER.warning("replay skipped a call: %s", name, exc_info=True)
            continue
        registry.record(ToolCallRecord(call["round"], name, args, True, "hit",
                                       "", "", 1, 0.0, 0, f"replay-{index}"), payload)
    return registry


def _registry_from_files(codebase, items: list,
                         limit: int | None = None) -> EvidenceRegistry:
    """Each item is a path (whole file) or a (path, start, end) span."""
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    for index, item in enumerate(items):
        if isinstance(item, tuple):
            path, start, end = item
        else:
            path, start = item, 1
            try:
                end = len(codebase.read_source(path).splitlines())
            except Exception:  # the profile cannot deliver a file it cannot read
                LOGGER.warning("profile could not deliver %s", path, exc_info=True)
                continue
            end = min(end, limit) if limit else end
        payload = codebase.read_code_context(path, max(1, start), max(1, end))
        registry.record(ToolCallRecord(1, "read_code_context",
                                       {"file_path": path, "start_line": 1,
                                        "end_line": end}, True, "hit", "", "", 1,
                                       0.0, 0, f"profile-{index}"), payload)
    return registry


def _allowed(codebase) -> set[str]:
    return set(codebase.source_files())


def _kinds(obligations) -> set[str]:
    return {o.kind for o in obligations}


def eval_dev(root: Path) -> list[dict]:
    rows: list[dict] = []
    for rel in DEV_RESULTS:
        path = Path(rel)
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        arm = "planner" if data.get("planner") else "baseline"
        for run in data["runs"]:
            case = next(c for c in DEV_CASES if c["id"] == run["case"])
            repo = build_dev(root, case)
            head = TrustedCodebase.from_git(repo, case["branch"])
            base = TrustedCodebase.from_git(repo, "main")
            files, _ = read_changed_files(repo, "main", case["branch"])
            started = time.perf_counter()
            obligations = derive_obligations(head, base, files)
            registry = _registry_from_calls(head, run["calls"])
            result = assess(registry, obligations, head.snapshot_sha, _allowed(head))
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            rows.append({
                "set": "dev", "arm": arm, "case": case["id"], "label": case["label"],
                "status": result.status, "expected": None,
                "kinds": sorted(_kinds(obligations)),
                "required": sum(1 for o in obligations if o.required),
                "missing": result.missing_obligations,
                "unknown": result.unknown_obligations,
                "fulfilled": len(result.fulfilled_obligations),
                "assessment_ms": elapsed,
                "explanation": result.explanation,
            })
    return rows


def eval_held(root: Path) -> list[dict]:
    rows: list[dict] = []
    for case in HELD_CASES:
        repo = build_held(root, case)
        branch = "case/" + case["id"].lower()
        head = TrustedCodebase.from_git(repo, branch)
        base = TrustedCodebase.from_git(repo, "main")
        files, _ = read_changed_files(repo, "main", branch)
        started = time.perf_counter()
        obligations = derive_obligations(head, base, files)
        derive_ms = round((time.perf_counter() - started) * 1000, 2)

        for name, delivered, expected in PROFILES[case["id"]]:
            registry = _registry_from_files(head, delivered)
            started = time.perf_counter()
            result = assess(registry, obligations, head.snapshot_sha, _allowed(head))
            assess_ms = round((time.perf_counter() - started) * 1000, 2)
            rows.append({
                "set": "held-out", "arm": name, "case": case["id"],
                "label": case.get("label", ""),
                "status": result.status, "expected": expected,
                "kinds": sorted(_kinds(obligations)),
                "expected_kinds": sorted(case["expected_kinds"]),
                "required": sum(1 for o in obligations if o.required),
                "missing": result.missing_obligations,
                "unknown": result.unknown_obligations,
                "fulfilled": len(result.fulfilled_obligations),
                "assessment_ms": assess_ms,
                "derive_ms": derive_ms,
                "explanation": result.explanation,
            })

        # negative controls: wrong SHA, truncated read, search only
        registry = EvidenceRegistry(ref="f" * 40)
        result = assess(registry, obligations, head.snapshot_sha, _allowed(head))
        rows.append({"set": "negative", "arm": "wrong-sha", "case": case["id"],
                     "status": result.status, "expected": INSUFFICIENT,
                     "kinds": sorted(_kinds(obligations)), "required": 1,
                     "missing": result.missing_obligations, "unknown": [],
                     "fulfilled": 0, "assessment_ms": 0.0, "derive_ms": derive_ms,
                     "explanation": result.explanation})

        first = [f.file_path for f in files][:1]
        truncated = _registry_from_files(head, first, limit=1) if first else None
        if truncated is not None:
            result = assess(truncated, obligations, head.snapshot_sha, _allowed(head))
            rows.append({"set": "negative", "arm": "truncated-read",
                         "case": case["id"], "status": result.status,
                         "expected": INSUFFICIENT, "kinds": sorted(_kinds(obligations)),
                         "required": 1, "missing": result.missing_obligations,
                         "unknown": [], "fulfilled": len(result.fulfilled_obligations),
                         "assessment_ms": 0.0, "derive_ms": derive_ms,
                         "explanation": result.explanation})

        search_only = EvidenceRegistry(ref=head.snapshot_sha)
        payload = head.search_code("handler", 5)
        search_only.record(ToolCallRecord(1, "search_code", {"query": "handler"}, True,
                                          "hit", "", "", 1, 0.0, 1, "search-1"), payload)
        result = assess(search_only, obligations, head.snapshot_sha, _allowed(head))
        rows.append({"set": "negative", "arm": "search-only", "case": case["id"],
                     "status": result.status, "expected": INSUFFICIENT,
                     "kinds": sorted(_kinds(obligations)), "required": 1,
                     "missing": result.missing_obligations, "unknown": [],
                     "fulfilled": len(result.fulfilled_obligations),
                     "assessment_ms": 0.0, "derive_ms": derive_ms,
                     "explanation": result.explanation})
    return rows


def summarise(rows: list[dict]) -> dict:
    scored = [r for r in rows if r.get("expected")]
    correct = sum(1 for r in scored if r["status"] == r["expected"])
    false_sufficient = [r for r in scored
                        if r["expected"] != SUFFICIENT and r["status"] == SUFFICIENT]
    false_insufficient = [r for r in scored
                          if r["expected"] == SUFFICIENT and r["status"] != SUFFICIENT]
    unknown_rows = [r for r in rows if r["status"] == UNKNOWN]

    kind_tp = kind_fp = kind_fn = 0
    for r in rows:
        if "expected_kinds" not in r:
            continue
        got, want = set(r["kinds"]), set(r["expected_kinds"])
        kind_tp += len(got & want)
        kind_fp += len(got - want)
        kind_fn += len(want - got)
    precision = kind_tp / (kind_tp + kind_fp) if kind_tp + kind_fp else None
    recall = kind_tp / (kind_tp + kind_fn) if kind_tp + kind_fn else None

    return {
        "assessment_version": ASSESSMENT_VERSION,
        "profiles_scored": len(scored),
        "status_accuracy": round(correct / len(scored), 4) if scored else None,
        "false_sufficient": len(false_sufficient),
        "false_insufficient": len(false_insufficient),
        "false_sufficient_cases": [f"{r['case']}/{r['arm']}" for r in false_sufficient],
        "false_insufficient_cases": [f"{r['case']}/{r['arm']}" for r in false_insufficient],
        "unknown_rate": round(len(unknown_rows) / len(rows), 4) if rows else None,
        "obligation_kind_precision": None if precision is None else round(precision, 4),
        "obligation_kind_recall": None if recall is None else round(recall, 4),
        "mean_assessment_ms": round(statistics.mean(r["assessment_ms"] for r in rows), 2),
        "mean_derive_ms": round(statistics.mean(r["derive_ms"] for r in rows
                                                if r.get("derive_ms")), 2)
        if any(r.get("derive_ms") for r in rows) else None,
        "cases": len(rows),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("docs/v4.5-eval"))
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="pr-guardian-v45-"))
    rows = eval_dev(root) + eval_held(root)
    payload = {"summary": summarise(rows), "rows": rows}
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    (out / "REPORT.md").write_text(render(payload), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    return 0


def render(payload: dict) -> str:
    s = payload["summary"]
    lines = [
        "# V4.5 sufficiency gate — offline evaluation",
        "",
        f"Assessment version `{s['assessment_version']}`. No model calls: dev evidence",
        "comes from the recorded V4.4 traces, held-out evidence from scripted profiles.",
        "",
        "## Summary",
        "",
        "| metric | value |", "|---|---|",
        f"| scored profiles | {s['profiles_scored']} |",
        f"| status accuracy | {s['status_accuracy']} |",
        f"| false sufficient | **{s['false_sufficient']}** {s['false_sufficient_cases']} |",
        f"| false insufficient | {s['false_insufficient']} {s['false_insufficient_cases']} |",
        f"| unknown rate | {s['unknown_rate']} |",
        f"| obligation kind precision | {s['obligation_kind_precision']} |",
        f"| obligation kind recall | {s['obligation_kind_recall']} |",
        f"| mean assessment cost | {s['mean_assessment_ms']} ms |",
        f"| mean derivation cost | {s['mean_derive_ms']} ms |",
        "",
        "## Per profile",
        "",
        "| set | case | profile | status | expected | kinds | required | missing | fulfilled |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in payload["rows"]:
        lines.append(f"| {r['set']} | {r['case']} | {r['arm']} | {r['status']} | "
                     f"{r['expected'] or '—'} | {', '.join(r['kinds'])} | "
                     f"{r['required']} | {len(r['missing'])} | {r['fulfilled']} |")
    lines += ["", "## Explanations", ""]
    for r in payload["rows"]:
        if r["status"] != SUFFICIENT:
            lines.append(f"* **{r['set']}/{r['case']}/{r['arm']}** — {r['explanation']}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
