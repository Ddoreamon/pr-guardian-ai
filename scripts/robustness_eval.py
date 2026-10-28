"""Run the V4.5 gate against the robustness set and report every verdict.

Evidence profiles deliver whole files (or nothing) through TrustedCodebase, so the
registry is built by the same code the agent uses. No model call, no gold path in
the derivation.
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.evidence import EvidenceRegistry
from pr_guardian.git_reader import read_changed_files
from pr_guardian.sufficiency import assess, derive_obligations
from pr_guardian.tool_calling import ToolCallRecord
from scripts.robustness_cases import CASES, PROFILES, build_case

OUT = Path("docs/v4.5-robustness")
LOGGER = logging.getLogger("pr_guardian.robustness_eval")


def _deliver(codebase, files: list[str]) -> EvidenceRegistry:
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    for index, path in enumerate(files):
        try:
            end = len(codebase.read_source(path).splitlines())
        except Exception:  # a file that cannot be read is simply not evidence
            LOGGER.warning("profile could not deliver %s", path, exc_info=True)
            continue
        payload = codebase.read_code_context(path, 1, max(1, end))
        registry.record(ToolCallRecord(1, "read_code_context",
                                       {"file_path": path, "start_line": 1,
                                        "end_line": end}, True, "hit", "", "", 1,
                                       0.0, 0, f"profile-{index}"), payload)
    return registry


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="pr-guardian-v45-rob-"))
    rows = []
    for case in CASES:
        repo = build_case(root, case)
        branch = "case/" + case["id"].lower()
        head = TrustedCodebase.from_git(repo, branch)
        base = TrustedCodebase.from_git(repo, "main")
        files, _ = read_changed_files(repo, "main", branch)
        started = time.perf_counter()
        obligations = derive_obligations(head, base, files)
        derive_ms = round((time.perf_counter() - started) * 1000, 1)
        allowed = set(head.source_files())

        for name, delivered, expected in PROFILES[case["id"]]:
            registry = _deliver(head, delivered)
            started = time.perf_counter()
            result = assess(registry, obligations, head.snapshot_sha, allowed)
            assess_ms = round((time.perf_counter() - started) * 1000, 1)
            rows.append({
                "case": case["id"], "profile": name, "delivered": delivered,
                "status": result.status, "expected": expected,
                "false_sufficient": expected != "sufficient" and result.status == "sufficient",
                "false_insufficient": expected == "sufficient" and result.status != "sufficient",
                "kinds": sorted({o.kind for o in obligations}),
                "expected_kinds": sorted(case["expect_kinds"]),
                "required": sum(1 for o in obligations if o.required),
                "missing": result.missing_obligations,
                "unknown": result.unknown_obligations,
                "explanation": result.explanation,
                "derive_ms": derive_ms, "assess_ms": assess_ms,
            })

    scored = rows
    fs = [r for r in scored if r["false_sufficient"]]
    fi = [r for r in scored if r["false_insufficient"]]
    kind_tp = sum(len(set(r["kinds"]) & set(r["expected_kinds"])) for r in scored)
    kind_fp = sum(len(set(r["kinds"]) - set(r["expected_kinds"])) for r in scored)
    kind_fn = sum(len(set(r["expected_kinds"]) - set(r["kinds"])) for r in scored)
    summary = {
        "profiles": len(scored),
        "cases": len(CASES),
        "status_accuracy": round(sum(1 for r in scored if r["status"] == r["expected"])
                                 / len(scored), 4),
        "false_sufficient": len(fs),
        "false_sufficient_cases": [f"{r['case']}/{r['profile']}" for r in fs],
        "false_insufficient": len(fi),
        "false_insufficient_cases": [f"{r['case']}/{r['profile']}" for r in fi],
        "unknown_rate": round(sum(1 for r in scored if r["status"] == "unknown")
                              / len(scored), 4),
        "kind_precision": round(kind_tp / (kind_tp + kind_fp), 4) if kind_tp + kind_fp else None,
        "kind_recall": round(kind_tp / (kind_tp + kind_fn), 4) if kind_tp + kind_fn else None,
        "mean_derive_ms": round(sum(r["derive_ms"] for r in rows) / len(rows), 1),
        "mean_assess_ms": round(sum(r["assess_ms"] for r in rows) / len(rows), 1),
    }
    payload = {"summary": summary, "rows": rows}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    (OUT / "REPORT.md").write_text(render(payload), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def render(payload: dict) -> str:
    s = payload["summary"]
    lines = [
        "# V4.5 robustness — where dependency analysis can fail",
        "",
        "Separate from the V4.5 held-out set. Profiles deliver whole files (or nothing)",
        "through `TrustedCodebase`; no model call, no gold path in the derivation.",
        "",
        "## Summary",
        "",
        "| metric | value |", "|---|---|",
        f"| cases / profiles | {s['cases']} / {s['profiles']} |",
        f"| status accuracy | {s['status_accuracy']:.2%} |",
        f"| **false sufficient** | **{s['false_sufficient']}** {s['false_sufficient_cases']} |",
        f"| false insufficient | {s['false_insufficient']} {s['false_insufficient_cases']} |",
        f"| unknown rate | {s['unknown_rate']:.2%} |",
        f"| obligation kind precision / recall | {s['kind_precision']} / {s['kind_recall']} |",
        f"| derivation / assessment cost | {s['mean_derive_ms']} ms / {s['mean_assess_ms']} ms |",
        "",
        "## Per profile",
        "",
        "| case | profile | delivered | status | expected | kinds | required | missing | FS | FI |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in payload["rows"]:
        lines.append(
            f"| {r['case']} | {r['profile']} | {', '.join(r['delivered']) or '—'} | "
            f"{r['status']} | {r['expected']} | {', '.join(r['kinds'])} | "
            f"{r['required']} | {len(r['missing'])} | "
            f"{'**yes**' if r['false_sufficient'] else ''} | "
            f"{'yes' if r['false_insufficient'] else ''} |")
    lines += ["", "## Non-sufficient explanations", ""]
    for r in payload["rows"]:
        if r["status"] != "sufficient":
            lines.append(f"* **{r['case']}/{r['profile']}** — {r['explanation']}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
