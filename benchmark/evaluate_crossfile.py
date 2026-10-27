"""Compare V1 (no tools) against V2 (read-only tool calling) on cross-file cases.

Matching rule is identical to the main benchmark and is frozen before measuring:
a finding counts as a true positive only if it lands on a changed line of the
case's changed file. Clean cases count any such finding as a false positive.

Methods never see the hidden ground-truth tests.

Run:
    python -m benchmark.evaluate_crossfile --split dev
    python -m benchmark.evaluate_crossfile --split heldout --out docs/benchmark-results/crossfile
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from benchmark.crossfile_cases import CrossFileCase, build_cases, files_for
from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.git_reader import read_changed_files
from pr_guardian.llm_reviewer import review_with_llm
from pr_guardian.models import ReviewFinding
from pr_guardian.settings import Settings, get_settings
from pr_guardian.tool_calling import review_with_tools

METHOD_V1 = "v1-no-tools"
METHOD_V2 = "v2-tools"


@dataclass
class CaseOutcome:
    case_id: str
    split: str
    category: str
    has_bug: bool
    flagged: bool
    findings: int
    tp: int
    fp: int
    located: bool | None
    tool_calls: int = 0
    tool_calls_ok: int = 0
    tool_rounds: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    status: str = "ok"
    error: str = ""
    tool_errors: str = ""
    finding_notes: str = ""


@dataclass
class MethodSummary:
    method: str
    split: str
    n_cases: int
    n_bug: int
    n_clean: int
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    clean_false_alarm_rate: float | None = None
    location_accuracy: float | None = None
    tool_calls: int = 0
    tool_calls_ok: int = 0
    tool_success_rate: float | None = None
    schema_valid_rate: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    mean_latency_s: float | None = None
    note: str = ""
    cases: list[CaseOutcome] = field(default_factory=list)


def _div(a: int, b: int) -> float | None:
    return None if b == 0 else round(a / b, 4)


def _f1(p, r):
    if p is None or r is None or (p + r) == 0:
        return None
    return round(2 * p * r / (p + r), 4)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=60)


def materialise(case: CrossFileCase, root: Path) -> Path:
    """base commit (all files, no tests) then a PR commit changing one file."""
    repo = root / f"{case.case_id}-{uuid.uuid4().hex[:8]}"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "xf@example.com")
    _git(repo, "config", "user.name", "xf")
    for rel, content in files_for(case, apply_pr=False).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "pr")
    (repo / case.changed_file).write_text(case.content_pr, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pr")
    return repo


def _definitions(text: str) -> list[tuple[str, int, int]]:
    """Top-level and nested function/class spans: (name, first_line, last_line)."""
    tree = ast.parse(text)
    spans: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", None)
            if end:
                spans.append((node.name, node.lineno, end))
    return spans


def changed_regions(case: CrossFileCase, added_lines: list[int]) -> list[tuple[int, int]]:
    """Line regions a finding may point at to count as touching the change.

    Rule (frozen before the held-out run): a finding matches if it falls inside a
    definition that the change touched. For added lines this is the enclosing
    definition in the new file; for a pure deletion there are no added lines, so
    the enclosing definition is taken from the base file and located again by
    name in the new file. If nothing can be resolved, the whole file is allowed.
    """
    base_text = files_for(case, apply_pr=False)[case.changed_file]
    pr_text = case.content_pr
    pr_defs = _definitions(pr_text)
    regions: list[tuple[int, int]] = []

    for _name, first, last in pr_defs:
        if any(first <= line <= last for line in added_lines):
            regions.append((first, last))

    if not regions:
        # pure deletion: map the old position onto the definition by name
        base_defs = _definitions(base_text)
        deleted = _deleted_old_lines(base_text, pr_text)
        touched = {name for name, first, last in base_defs
                   if any(first <= line <= last for line in deleted)}
        for name, first, last in pr_defs:
            if name in touched:
                regions.append((first, last))

    if not regions:
        regions.append((1, max(len(pr_text.splitlines()), 1)))
    return regions


def _deleted_old_lines(base_text: str, pr_text: str) -> list[int]:
    """Old-file line numbers that disappeared (small files: simple diff)."""
    import difflib

    base_lines = base_text.splitlines()
    diff = difflib.SequenceMatcher(a=base_lines, b=pr_text.splitlines())
    removed: list[int] = []
    for tag, i1, _i2, _j1, _j2 in diff.get_opcodes():
        if tag in ("delete", "replace"):
            removed.extend(range(i1 + 1, i1 + (_i2 - i1) + 1))
    return removed


def match(case: CrossFileCase, findings: list[ReviewFinding],
          changed_lines: list[int]) -> tuple[int, int, bool]:
    on_target = [f for f in findings if f.file_path == case.changed_file]
    regions = changed_regions(case, changed_lines)

    def hit(f: ReviewFinding) -> bool:
        return any(
            r_first <= line <= r_last
            for line in range(f.start_line, f.end_line + 1)
            for r_first, r_last in regions
        )

    if not case.has_bug:
        return 0, len(on_target), False
    hits = [f for f in on_target if hit(f)]
    misses = [f for f in on_target if not hit(f)]
    located = bool(hits)
    return (1 if located else 0), len(misses) + max(len(hits) - 1, 0), located


def run_case(method: str, case: CrossFileCase, root: Path,
             settings: Settings) -> CaseOutcome:
    repo = materialise(case, root)
    files, diff_text = read_changed_files(repo, "main", "pr")
    changed = [ln for f in files if f.file_path == case.changed_file for ln in f.added_lines]

    started = time.perf_counter()
    trace = []
    rounds = 0
    prompt_tokens = completion_tokens = 0
    status = "ok"
    error = ""
    schema_valid = True

    if method == METHOD_V1:
        res = review_with_llm(diff_text, files, settings=settings)
        findings = list(res.findings)
        usage = res.usage or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        schema_valid = res.schema_valid
        if res.error:
            status, error = "error", res.error
    else:
        res = review_with_tools(diff_text, files, TrustedCodebase(repo), settings)
        findings = list(res.findings)
        usage = res.usage or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        trace = res.trace
        rounds = res.rounds
        schema_valid = res.schema_valid
        status = res.status
        error = res.error

    tp, fp, located = match(case, findings, changed)
    flagged = any(f.file_path == case.changed_file for f in findings)
    return CaseOutcome(
        case_id=case.case_id, split=case.split, category=case.category,
        has_bug=case.has_bug, flagged=flagged, findings=len(findings),
        tp=tp, fp=fp, located=(located if case.has_bug else None),
        tool_calls=len(trace),
        tool_calls_ok=sum(1 for t in trace if t.ok),
        tool_rounds=rounds,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        latency_s=round(time.perf_counter() - started, 3),
        status=status if schema_valid else "schema_invalid",
        error=error,
        tool_errors="; ".join(f"{t.name}:{t.error_code}" for t in trace if not t.ok)[:200],
        finding_notes=" | ".join(f"L{f.start_line} {f.summary[:100]}" for f in findings)[:500],
    )


def summarise(method: str, split: str, cases: list[CrossFileCase],
              outcomes: list[CaseOutcome]) -> MethodSummary:
    ok = [o for o in outcomes if o.status not in {"error", "schema_invalid"}]
    bug = [o for o in ok if o.has_bug]
    clean = [o for o in ok if not o.has_bug]
    tp = sum(1 for o in bug if o.flagged)
    fn = len(bug) - tp
    fp = sum(1 for o in clean if o.flagged)
    tn = len(clean) - fp
    p, r = _div(tp, tp + fp), _div(tp, tp + fn)
    calls = sum(o.tool_calls for o in outcomes)
    calls_ok = sum(o.tool_calls_ok for o in outcomes)
    schema_ok = sum(1 for o in outcomes if o.status != "schema_invalid")
    lat = [o.latency_s for o in ok]
    return MethodSummary(
        method=method, split=split, n_cases=len(outcomes),
        n_bug=len(bug), n_clean=len(clean),
        tp=tp, fp=fp, fn=fn, tn=tn,
        precision=p, recall=r, f1=_f1(p, r),
        clean_false_alarm_rate=_div(fp, len(clean)),
        location_accuracy=_div(sum(1 for o in bug if o.located), len(bug)),
        tool_calls=calls, tool_calls_ok=calls_ok,
        tool_success_rate=_div(calls_ok, calls),
        schema_valid_rate=_div(schema_ok, len(outcomes)),
        prompt_tokens=sum(o.prompt_tokens for o in outcomes),
        completion_tokens=sum(o.completion_tokens for o in outcomes),
        mean_latency_s=round(sum(lat) / len(lat), 3) if lat else None,
        cases=outcomes,
    )


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:.2%}"


def render(summaries: list[MethodSummary]) -> str:
    lines = [
        "# Cross-file benchmark: V1 (no tools) vs V2 (read-only tools)",
        "",
        "Each case changes ONE file; the contract that makes it wrong lives in another",
        "file, so it is only decidable by reading the repository.",
        "Matching rule is identical to the main benchmark and was frozen before measuring.",
        "Methods never see the hidden ground-truth tests.",
        "",
        "| Method | Split | Cases (bug/clean) | TP | FP | FN | Precision | Recall | F1 | Clean false-alarm | Location accuracy |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s.method} | {s.split} | {s.n_cases} ({s.n_bug}/{s.n_clean}) | {s.tp} | {s.fp} | "
            f"{s.fn} | {_pct(s.precision)} | {_pct(s.recall)} | {_pct(s.f1)} | "
            f"{_pct(s.clean_false_alarm_rate)} | {_pct(s.location_accuracy)} |"
        )
    lines += ["", "## Tool usage and cost", "",
              "| Method | Split | Tool calls | Succeeded | Tool success rate | Schema-valid | Prompt tokens | Completion tokens | Mean latency (s) |",
              "|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s.method} | {s.split} | {s.tool_calls} | {s.tool_calls_ok} | "
            f"{_pct(s.tool_success_rate)} | {_pct(s.schema_valid_rate)} | {s.prompt_tokens} | "
            f"{s.completion_tokens} | {s.mean_latency_s if s.mean_latency_s is not None else 'n/a'} |"
        )
    lines += ["", "## Per-case detail", "",
              "| Method | Case | Split | Bug? | Flagged | TP | FP | Tools | Tool errors | Findings |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        for o in s.cases:
            note = (o.finding_notes or "").replace("|", "/")
            lines.append(
                f"| {s.method} | {o.case_id} | {o.split} | {o.has_bug} | {o.flagged} | {o.tp} | "
                f"{o.fp} | {o.tool_calls} | {(o.tool_errors or '—').replace('|', '/')} | {note} |"
            )
    return "\n".join(lines) + "\n"


def write_outputs(summaries: list[MethodSummary], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(
        json.dumps([asdict(s) for s in summaries], indent=2, ensure_ascii=False), encoding="utf-8")
    cols = ["method", "split", "n_cases", "n_bug", "n_clean", "tp", "fp", "fn", "precision",
            "recall", "f1", "clean_false_alarm_rate", "location_accuracy", "tool_calls",
            "tool_calls_ok", "tool_success_rate", "schema_valid_rate", "prompt_tokens",
            "completion_tokens", "mean_latency_s"]
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for s in summaries:
            w.writerow(asdict(s))
    with (out / "cases.csv").open("w", newline="", encoding="utf-8") as fh:
        fields = list(CaseOutcome.__dataclass_fields__)
        w = csv.writer(fh)
        w.writerow(["method"] + fields)
        for s in summaries:
            for o in s.cases:
                w.writerow([s.method] + [getattr(o, f) for f in fields])
    (out / "REPORT.md").write_text(render(summaries), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    ap.add_argument("--methods", default=f"{METHOD_V1},{METHOD_V2}")
    ap.add_argument("--out", type=Path, default=Path("docs/benchmark-results/crossfile"))
    args = ap.parse_args(argv)

    settings = get_settings()
    if not settings.api_key:
        print("NOT EVALUATED: PR_GUARDIAN_API_KEY is not set.", file=sys.stderr)
        return 3

    cases = build_cases()
    if args.split != "all":
        cases = [c for c in cases if c.split == args.split]
    methods = [m.strip() for m in args.methods.split(",") if m]

    summaries = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for method in methods:
            print(f"running {method} on {len(cases)} cases ...", file=sys.stderr)
            outcomes = []
            for case in cases:
                try:
                    outcomes.append(run_case(method, case, root, settings))
                except Exception as exc:  # noqa: BLE001 - record and continue
                    outcomes.append(CaseOutcome(
                        case.case_id, case.split, case.category, case.has_bug, False, 0, 0, 0,
                        None, status="error", error=f"{type(exc).__name__}: {exc}"))
            summaries.append(summarise(method, args.split, cases, outcomes))

    write_outputs(summaries, args.out)
    print(render(summaries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
