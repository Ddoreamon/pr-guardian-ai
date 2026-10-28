"""Offline shadow replay of a saved review report. Never touches the report.

Usage:
    python scripts/shadow_replay.py --dev                # rebuild the dev samples
    python scripts/shadow_replay.py --sample report.json --repo /path/to/repo \
        --base main --head feature/x

Output (JSON): sufficiency status, missing/unknown obligations, replay verification
status, review SHA, assessment version, elapsed time. Nothing is written back to the
report; findings and review_status are read-only inputs that the replay never uses.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.git_reader import read_changed_files
from pr_guardian.shadow import assess_report
from pr_guardian.sufficiency import (
    ASSESSMENT_VERSION,
    classify_diff,
    derive_obligations,
)

OUT = Path("docs/v4.5-shadow")


def analyse(report: dict, repo: Path, base_ref: str, head_ref: str) -> dict:
    started = time.perf_counter()
    head = TrustedCodebase.from_git(repo, head_ref)
    base = TrustedCodebase.from_git(repo, base_ref)
    files, _diff = read_changed_files(repo, base_ref, head_ref)
    obligations = derive_obligations(head, base, files)
    payload = assess_report(report, head, base, files,
                            obligations=obligations, classifier=classify_diff)
    payload.update({
        "review_id": report.get("review_id", ""),
        "review_sha": head.snapshot_sha,
        "report_sha": report.get("snapshot_sha", ""),
        "assessment_version": ASSESSMENT_VERSION,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "obligation_kinds": sorted({o.kind for o in obligations}),
    })
    return payload


def _dev_cases(root: Path) -> list[dict]:
    """Build reports from the robustness/dev repositories with real tool payloads."""
    from pr_guardian.evidence import EvidenceRegistry
    from pr_guardian.tool_calling import ToolCallRecord
    from scripts.robustness_cases import CASES, build_case

    samples = []
    for case in CASES:
        if case["id"] not in {"R4-alias", "R9-default", "R10-registry"}:
            continue
        repo = build_case(root, case)
        branch = "case/" + case["id"].lower()
        head = TrustedCodebase.from_git(repo, branch)
        profiles = {
            "R4-alias": {
                "complete": ["src/fmt.py", "src/report.py", "src/alias_report.py"],
                "partial": ["src/fmt.py"],
            },
            "R9-default": {"partial": ["src/fmt.py"]},
            "R10-registry": {"partial": ["src/registry.py"]},
        }[case["id"]]
        for name, files in profiles.items():
            registry = EvidenceRegistry(ref=head.snapshot_sha)
            for index, path in enumerate(files):
                end = len(head.read_source(path).splitlines())
                payload = head.read_code_context(path, 1, max(1, end))
                registry.record(ToolCallRecord(1, "read_code_context",
                                               {"file_path": path, "start_line": 1,
                                                "end_line": end}, True, "hit", "", "",
                                               1, 0.0, 0, f"{case['id']}-{index}"), payload)
            samples.append({
                "report": {
                    "review_id": f"{case['id']}/{name}",
                    "snapshot_sha": head.snapshot_sha,
                    "review_status": "completed",
                    "findings": [],
                    "evidence_sources": registry.summary(),
                },
                "repo": repo, "base": "main", "head": branch,
            })
    return samples


def render(payload: dict) -> str:
    s = payload["sufficiency"]
    lines = [
        f"## {payload['review_id']}",
        "",
        f"- review SHA: `{payload['review_sha']}`",
        f"- report SHA: `{payload['report_sha']}`",
        f"- assessment: `{payload['assessment_version']}`",
        f"- replay verification: **{payload['replay_status']}**",
        f"- sufficiency: **{s['status']}** (diff state: `{s['diff_state']}`)",
        f"- missing obligations: {s['missing_obligations'] or 'none'}",
        f"- unknown obligations: {s['unknown_obligations'] or 'none'}",
        f"- elapsed: {payload['elapsed_ms']} ms",
        "",
        f"> {s['explanation']}",
        "",
    ]
    if payload["replay_notes"]:
        lines.append("Replay notes:")
        lines.extend(f"- {note}" for note in payload["replay_notes"])
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--sample", type=Path)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--base", default="main")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()

    if args.dev:
        root = Path(tempfile.mkdtemp(prefix="pr-guardian-shadow-"))
        results = []
        for sample in _dev_cases(root):
            results.append(analyse(sample["report"], sample["repo"], sample["base"],
                                   sample["head"]))
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "results.json").write_text(
            json.dumps({"results": results}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        (OUT / "REPORT.md").write_text(
            "# V4.5 shadow replay — dev samples\n\n"
            + "\n".join(render(result) for result in results), encoding="utf-8")
        for result in results:
            print(json.dumps({k: result[k] for k in (
                "review_id", "review_sha", "replay_status", "elapsed_ms")} |
                {"status": result["sufficiency"]["status"],
                 "missing": len(result["sufficiency"]["missing_obligations"])}))
        return 0

    if not args.sample or not args.repo:
        parser.error("--sample and --repo are required unless --dev is used")
    report = json.loads(args.sample.read_text(encoding="utf-8"))
    result = analyse(report, args.repo, args.base, args.head)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
