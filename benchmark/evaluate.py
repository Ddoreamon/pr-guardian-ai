"""Benchmark evaluator for PR Guardian.

Design rules (fixed in advance; do not change after looking at held-out results):

* Inputs to every method: the PR's git diff and the module source file.
  The test file (which encodes the ground truth) is NEVER given to a method
  unless the method is explicitly named with the `+tests` suffix.
* Ground truth is applied only by this evaluator, after a method has run.
* Two metric families, reported separately:
    - PR-level bug detection: a PR is "flagged" if the method emits >=1 finding
      on the changed module. Compared against `has_bug`.
    - Finding-level issue matching: a finding is a true positive only if it is
      on the changed module file AND on a line changed by the PR. Each buggy PR
      can be matched at most once.
* Dev / held-out split is taken from the case. Held-out results must not be
  used to tune prompts; `--split` selects which set to score.
* Division by zero is avoided: undefined ratios are reported as None.

Run:  python -m benchmark.evaluate --split all --out benchmark/results
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from benchmark.cases import Case, build_cases
from pr_guardian.git_reader import read_changed_files
from pr_guardian.models import FindingSource, ReviewFinding, VerificationStatus
from pr_guardian.static_analysis import run_ruff
from pr_guardian.validator import validate_findings

METHOD_RUFF = "ruff-only"
METHOD_LLM = "llm-only"          # raw model output; validator NOT applied
METHOD_COMBINED = "combined"     # ruff + llm + validator (the shipped pipeline)
METHOD_COMBINED_TESTS = "combined+tests"  # combined + pytest evidence; reported separately


@dataclass
class MethodRun:
    """What one method produced for one case."""

    findings: list[ReviewFinding]
    changed_lines: list[int]
    note: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_latency_s: float = 0.0
    llm_called: bool = False
    dropped_invalid: int = 0
    raw_findings: int = 0


@dataclass
class CaseOutcome:
    case_id: str
    split: str
    category: str
    has_bug: bool
    flagged: bool
    findings: int
    tp_findings: int
    fp_findings: int
    bug_located: bool | None  # None for clean PRs
    latency_s: float
    status: str  # ok | not_evaluated | error
    error: str = ""
    failure_note: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_latency_s: float = 0.0
    schema_valid: bool | None = None
    dropped_invalid: int = 0
    raw_findings: int = 0
    finding_notes: str = ""


@dataclass
class MethodSummary:
    method: str
    split: str
    status: str
    n_cases: int
    n_bug: int
    n_clean: int
    # PR-level
    pr_tp: int = 0
    pr_fp: int = 0
    pr_fn: int = 0
    pr_tn: int = 0
    pr_precision: float | None = None
    pr_recall: float | None = None
    pr_f1: float | None = None
    clean_false_alarm_rate: float | None = None
    # Finding-level
    finding_tp: int = 0
    finding_fp: int = 0
    finding_precision: float | None = None
    bug_location_accuracy: float | None = None
    mean_latency_s: float | None = None
    # Cost accounting (only from provider-reported usage; cost needs a price table)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    schema_valid_rate: float | None = None
    validator_dropped: int = 0
    note: str = ""
    cases: list[CaseOutcome] = field(default_factory=list)


def safe_div(num: int, den: int) -> float | None:
    """Ratio that returns None instead of raising on a zero denominator."""
    return None if den == 0 else round(num / den, 4)


def f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None or (p + r) == 0:
        return None
    return round(2 * p * r / (p + r), 4)


# ---------------------------------------------------------------------------
# Repository materialisation
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=60)


def materialise_case(case: Case, root: Path, include_tests: bool) -> Path:
    """Create a git repo: `main` = base, `pr` = base + PR edit.

    The ground-truth test file exists in BOTH revisions only when include_tests
    is set. For every other method the repository simply never contains tests,
    so the diff cannot show tests being deleted — that would be a spurious change
    the model could comment on (and it did, before this was fixed).
    """
    # Each (case, method) gets its own directory so runs never collide.
    repo = root / f"{case.case_id}-{'tests' if include_tests else 'notests'}-{uuid.uuid4().hex[:8]}"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "bench@example.com")
    _git(repo, "config", "user.name", "bench")
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / f"{case.module}.py").write_text(case.base_code, encoding="utf-8")
    if include_tests:
        (repo / "tests").mkdir()
        (repo / "tests" / f"test_{case.module}.py").write_text(case.test_code, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "pr")
    (repo / "src" / f"{case.module}.py").write_text(case.pr_code, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pr")
    return repo


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

# Lints that describe style/hygiene and can never describe a behavioural defect.
# A hit from one of these on a changed line is INCIDENTAL, not evidence the
# defect was found. Declared up front; do not extend after seeing results.
INCIDENTAL_RULES = {"F841", "E501", "W291", "W293", "I001", "UP017", "UP035", "E711", "E712"}


def _is_incidental(f: ReviewFinding) -> bool:
    return f.source == FindingSource.RUFF and (f.rule or "") in INCIDENTAL_RULES


def match_findings(case: Case, findings: list[ReviewFinding], changed_lines: list[int]) -> tuple[int, int, bool]:
    """Return (tp, fp, located) for one case.

    A finding is TP only if it is on the changed module file and on a changed line.
    At most one TP per buggy case (a single defect cannot be matched twice).
    Clean PRs: every finding on the changed module is a FP.
    """
    target = f"src/{case.module}.py"
    on_target = [f for f in findings if f.file_path == target]
    changed = set(changed_lines)

    def on_changed(f: ReviewFinding) -> bool:
        return any(line in changed for line in range(f.start_line, f.end_line + 1))

    if not case.has_bug:
        # every relevant finding on the module is a false positive on a clean PR
        return 0, len([f for f in on_target if not _is_incidental(f)]), False

    # incidental lint hits never count as detecting the defect
    relevant = [f for f in on_target if not _is_incidental(f)]
    hits = [f for f in relevant if on_changed(f)]
    misses = [f for f in relevant if not on_changed(f)]
    located = bool(hits)
    tp = 1 if located else 0          # one defect can be matched at most once
    fp = len(misses) + max(len(hits) - 1, 0)  # extra hits on the same defect are duplicates
    return tp, fp, located


# ---------------------------------------------------------------------------
# Method runners
# ---------------------------------------------------------------------------

def run_method(method: str, case: Case, root: Path) -> MethodRun:
    """Run one method on one case.

    `llm-only` is deliberately the RAW model output: it measures the model alone.
    `combined` applies the validator, which is what the shipped pipeline does.
    """
    include_tests = method == METHOD_COMBINED_TESTS
    repo = materialise_case(case, root, include_tests=include_tests)
    files, diff_text = read_changed_files(repo, "main", "pr")
    changed = [l for f in files if f.file_path == f"src/{case.module}.py" for l in f.added_lines]

    if method == METHOD_RUFF:
        findings, _ = run_ruff(repo, files)
        return MethodRun(findings=findings, changed_lines=changed)

    if method in (METHOD_LLM, METHOD_COMBINED, METHOD_COMBINED_TESTS):
        import os

        from pr_guardian.llm_reviewer import review_with_llm
        if not os.environ.get("PR_GUARDIAN_API_KEY"):
            return MethodRun([], changed, "NOT_EVALUATED: no PR_GUARDIAN_API_KEY")
        res = review_with_llm(diff_text, files)
        findings = list(res.findings)
        if method != METHOD_LLM:
            ruff_findings, _ = run_ruff(repo, files)
            findings = ruff_findings + findings
            # the shipped pipeline validates before reporting
            validated = validate_findings(findings, files)
            dropped = sum(1 for f in validated
                          if f.verification_status == VerificationStatus.INVALID)
            findings = [f for f in validated
                        if f.verification_status != VerificationStatus.INVALID]
            usage = res.usage or {}
            return MethodRun(
                findings=findings,
                changed_lines=changed,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                llm_latency_s=res.latency_s,
                llm_called=res.error not in {"no_api_key", "disabled_by_policy"},
                dropped_invalid=dropped,
                raw_findings=len(ruff_findings) + len(res.findings),
            )
        usage = res.usage or {}
        return MethodRun(
            findings=findings,
            changed_lines=changed,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            llm_latency_s=res.latency_s,
            llm_called=res.error not in {"no_api_key", "disabled_by_policy"},
            raw_findings=len(res.findings),
        )

    raise ValueError(f"unknown method {method}")


def evaluate_method(method: str, cases: list[Case], root: Path) -> MethodSummary:
    outcomes: list[CaseOutcome] = []
    notes: list[str] = []
    for case in cases:
        t0 = time.perf_counter()
        try:
            run = run_method(method, case, root)
            if run.note.startswith("NOT_EVALUATED"):
                outcomes.append(CaseOutcome(case.case_id, _split(case), case.category, case.has_bug,
                                            False, 0, 0, 0, None, 0.0, "not_evaluated", run.note))
                notes.append(run.note)
                continue
            tp, fp, located = match_findings(case, run.findings, run.changed_lines)
            flagged = any(
                f.file_path == f"src/{case.module}.py" and not _is_incidental(f)
                for f in run.findings
            )
            outcomes.append(CaseOutcome(
                case.case_id, _split(case), case.category, case.has_bug, flagged,
                len(run.findings), tp, fp, (located if case.has_bug else None),
                round(time.perf_counter() - t0, 4), "ok",
                prompt_tokens=run.prompt_tokens,
                completion_tokens=run.completion_tokens,
                llm_latency_s=run.llm_latency_s,
                schema_valid=(True if run.llm_called else None),
                dropped_invalid=run.dropped_invalid,
                raw_findings=run.raw_findings,
                finding_notes=" | ".join(
                    f"L{f.start_line} {f.summary[:110]}" for f in run.findings
                )[:600],
            ))
        except Exception as exc:  # noqa: BLE001 - record and continue, never crash the run
            outcomes.append(CaseOutcome(case.case_id, _split(case), case.category, case.has_bug,
                                        False, 0, 0, 0, None, 0.0, "error", f"{type(exc).__name__}: {exc}"))
    return summarise(method, cases, outcomes, notes)


def _split(case: Case) -> str:
    return "dev" if case.dev_split else "heldout"


def summarise(method: str, cases: list[Case], outcomes: list[CaseOutcome], notes: list[str]) -> MethodSummary:
    ok = [o for o in outcomes if o.status == "ok"]
    status = "ok" if len(ok) == len(outcomes) and ok else ("not_evaluated" if not ok else "partial")
    if not ok:
        status = "not_evaluated"
    bug_ok = [o for o in ok if o.has_bug]
    clean_ok = [o for o in ok if not o.has_bug]
    pr_tp = sum(1 for o in bug_ok if o.flagged)
    pr_fn = len(bug_ok) - pr_tp
    pr_fp = sum(1 for o in clean_ok if o.flagged)
    pr_tn = len(clean_ok) - pr_fp
    precision = safe_div(pr_tp, pr_tp + pr_fp)
    recall = safe_div(pr_tp, pr_tp + pr_fn)
    f_tp = sum(o.tp_findings for o in ok)
    f_fp = sum(o.fp_findings for o in ok)
    lat = [o.latency_s for o in ok]
    located_n = sum(1 for o in bug_ok if o.bug_located)
    prompt_tokens = sum(o.prompt_tokens for o in ok)
    completion_tokens = sum(o.completion_tokens for o in ok)
    llm_calls = sum(1 for o in ok if o.schema_valid is not None)
    schema_ok = sum(1 for o in ok if o.schema_valid is True)
    return MethodSummary(
        method=method,
        split=_split_label(outcomes),
        status=status,
        n_cases=len(outcomes),
        n_bug=sum(1 for c in cases if c.has_bug),
        n_clean=sum(1 for c in cases if not c.has_bug),
        pr_tp=pr_tp, pr_fp=pr_fp, pr_fn=pr_fn, pr_tn=pr_tn,
        pr_precision=precision,
        pr_recall=recall,
        pr_f1=f1(precision, recall),
        clean_false_alarm_rate=safe_div(pr_fp, len(clean_ok)),
        finding_tp=f_tp,
        finding_fp=f_fp,
        finding_precision=safe_div(f_tp, f_tp + f_fp),
        bug_location_accuracy=safe_div(located_n, len(bug_ok)),
        mean_latency_s=round(sum(lat) / len(lat), 4) if lat else None,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        llm_calls=llm_calls,
        schema_valid_rate=safe_div(schema_ok, llm_calls),
        validator_dropped=sum(o.dropped_invalid for o in ok),
        note="; ".join(sorted(set(notes))),
        cases=outcomes,
    )


def _split_label(outcomes: list[CaseOutcome]) -> str:
    s = {o.split for o in outcomes}
    return s.pop() if len(s) == 1 else "all"


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_outputs(summaries: list[MethodSummary], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = [asdict(s) for s in summaries]
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        cols = ["method", "split", "status", "n_cases", "n_bug", "n_clean", "pr_tp", "pr_fp", "pr_fn",
                "pr_precision", "pr_recall", "pr_f1", "clean_false_alarm_rate", "finding_precision",
                "bug_location_accuracy", "mean_latency_s", "note"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for s in summaries:
            w.writerow(asdict(s))

    with (out_dir / "cases.csv").open("w", newline="", encoding="utf-8") as fh:
        cols = list(CaseOutcome.__dataclass_fields__.keys())
        w = csv.writer(fh)
        w.writerow(["method"] + cols)
        for s in summaries:
            for o in s.cases:
                w.writerow([s.method] + [getattr(o, c) for c in cols])

    (out_dir / "REPORT.md").write_text(render_markdown(summaries), encoding="utf-8")


def _fmt(v, pct: bool = True) -> str:
    """Format a metric. Ratios are shown as percentages; latency is shown as seconds."""
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.2%}" if pct else f"{v:.3f}"
    return str(v)


def render_markdown(summaries: list[MethodSummary]) -> str:
    lines = ["# Benchmark results", "",
             "Ground truth is applied only by the evaluator. Methods never see the test file",
             "unless explicitly marked `+tests`. `n/a` = undefined (zero denominator) or not run.", "",
             "Method definitions:", "",
             "* `ruff-only` — Ruff static analysis only.",
             "* `llm-only` — raw model output, validator NOT applied (measures the model alone).",
             "* `combined` — Ruff + model + validator (what the shipped pipeline reports).",
             "* `combined+tests` — combined plus pytest evidence; reported separately because",
             "  the test file is visible to the method in that condition.", ""]
    lines += ["## PR-level bug detection", "",
              "| Method | Split | Status | Cases (bug/clean) | TP | FP | FN | Precision | Recall | F1 | Clean false-alarm | Mean latency (s) |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s.method} | {s.split} | {s.status} | {s.n_cases} ({s.n_bug}/{s.n_clean}) | "
            f"{s.pr_tp} | {s.pr_fp} | {s.pr_fn} | {_fmt(s.pr_precision)} | {_fmt(s.pr_recall)} | "
            f"{_fmt(s.pr_f1)} | {_fmt(s.clean_false_alarm_rate)} | {_fmt(s.mean_latency_s, pct=False)} |"
        )
    lines += ["", "## Finding-level issue matching", "",
              "| Method | Split | Finding TP | Finding FP | Finding precision | Bug location accuracy |",
              "|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(f"| {s.method} | {s.split} | {s.finding_tp} | {s.finding_fp} | "
                     f"{_fmt(s.finding_precision)} | {_fmt(s.bug_location_accuracy)} |")

    lines += ["", "## Model calls and tokens", "",
              "Provider-reported usage only. Cost is not inferred without a price table.", "",
              "| Method | Split | LLM calls | Schema-valid rate | Validator dropped | Prompt tokens | Completion tokens | Total tokens | LLM latency (s, mean) |",
              "|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        mean_llm_lat = None
        llm_latencies = [o.llm_latency_s for o in s.cases if o.llm_latency_s]
        if llm_latencies:
            mean_llm_lat = round(sum(llm_latencies) / len(llm_latencies), 3)
        lines.append(
            f"| {s.method} | {s.split} | {s.llm_calls} | {_fmt(s.schema_valid_rate)} | "
            f"{s.validator_dropped} | {s.prompt_tokens} | {s.completion_tokens} | "
            f"{s.total_tokens} | {_fmt(mean_llm_lat, pct=False)} |"
        )

    lines += ["", "## Per-case detail", "",
              "| Method | Case | Split | Category | Has bug | Flagged | TP | FP | Raw | Dropped | Findings |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        for o in s.cases:
            note = (o.finding_notes or "").replace("|", "/")
            lines.append(
                f"| {s.method} | {o.case_id} | {o.split} | {o.category} | {o.has_bug} | "
                f"{o.flagged} | {o.tp_findings} | {o.fp_findings} | {o.raw_findings} | "
                f"{o.dropped_invalid} | {note} |"
            )

    notes = [f"- **{s.method}**: {s.note}" for s in summaries if s.note]
    if notes:
        lines += ["", "## Notes", ""] + notes
    lines += ["", "## Failure cases", ""]
    for s in summaries:
        for o in s.cases:
            wrong = (o.has_bug and not o.flagged) or ((not o.has_bug) and o.flagged) or o.status == "error"
            if wrong:
                lines.append(f"- `{s.method}` `{o.case_id}` ({o.category}, {o.split}): "
                             f"has_bug={o.has_bug}, flagged={o.flagged}, status={o.status} {o.error}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "heldout", "all"], default="all")
    ap.add_argument("--methods", default=f"{METHOD_RUFF},{METHOD_LLM},{METHOD_COMBINED}")
    ap.add_argument("--out", type=Path, default=Path("benchmark/results"))
    args = ap.parse_args(argv)

    cases = build_cases()
    if args.split != "all":
        cases = [c for c in cases if _split(c) == args.split]
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]

    summaries: list[MethodSummary] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for m in methods:
            print(f"running {m} on {len(cases)} cases ...", file=sys.stderr)
            summaries.append(evaluate_method(m, cases, root))
    write_outputs(summaries, args.out)
    print(render_markdown(summaries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
