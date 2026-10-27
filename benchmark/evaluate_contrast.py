"""Evaluate the contrast pairs: v1-diff-only vs v2-tools.

Scoring is deliberately split into two layers, because "the finding sits in the
right function" is not the same as "the finding describes the actual defect":

  located     the finding lands inside a definition the change touched
  semantic    additionally, the finding text matches the case's defect signature

A pair is counted as consistent only when BOTH variants are correct, which is a
different quantity from per-case accuracy (see docs/v3-contrast-design.md §4).

Run (dev only for now):
    python -m benchmark.evaluate_contrast --split dev
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from benchmark.contrast_cases import ContrastPair, build_index, files_for
from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.git_reader import read_changed_files
from pr_guardian.llm_reviewer import review_with_llm
from pr_guardian.models import ReviewFinding
from pr_guardian.settings import Settings, get_settings
from pr_guardian.tool_calling import review_with_tools

METHOD_V1 = "v1-diff-only"
METHOD_V2 = "v2-tools"


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=60)


def materialise(pair: ContrastPair, variant: str, root: Path) -> Path:
    repo = root / f"{pair.pair_id}-{variant}-{uuid.uuid4().hex[:6]}"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "c@example.com")
    _git(repo, "config", "user.name", "c")
    for rel, content in files_for(pair, variant, apply_pr=False).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "pr")
    (repo / pair.diff_file).write_text(pair.pr_code, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pr")
    return repo


def touched_regions(pair: ContrastPair) -> list[tuple[int, int]]:
    """Definitions the change touched, in the PR revision (AST based)."""
    tree = ast.parse(pair.pr_code)
    spans = [(n.name, n.lineno, n.end_lineno)
             for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
             and getattr(n, "end_lineno", None)]
    changed = _changed_new_lines(pair)
    inside = [(f, l) for _n, f, l in spans if any(f <= ln <= l for ln in changed)]
    if inside:
        return inside
    return [(1, max(len(pair.pr_code.splitlines()), 1))]


def _changed_new_lines(pair: ContrastPair) -> list[int]:
    """New-file line numbers whose content differs from base."""
    import difflib

    base = pair.base_code.splitlines()
    pr = pair.pr_code.splitlines()
    sm = difflib.SequenceMatcher(a=base, b=pr)
    out: list[int] = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag in ("insert", "replace"):
            out.extend(range(j1 + 1, j2 + 1))
    if not out:
        # pure deletion: anchor on the join point
        for tag, _i1, _i2, j1, _j2 in sm.get_opcodes():
            if tag == "delete":
                out.append(max(j1, 1))
    return out


def _in_regions(finding: ReviewFinding, regions: list[tuple[int, int]]) -> bool:
    return any(f <= line <= l
               for line in range(finding.start_line, finding.end_line + 1)
               for f, l in regions)


def semantic_match(pair: ContrastPair, findings: list[ReviewFinding]) -> bool:
    """True only when a located finding names the actual defect.

    Each signature group must be satisfied by at least one term; the groups are
    case-specific and written when the case is frozen, not after seeing output.
    """
    regions = touched_regions(pair)
    for finding in findings:
        if not _in_regions(finding, regions):
            continue
        text = f"{finding.summary} {finding.explanation} {finding.evidence}".lower()
        if all(any(term.lower() in text for term in group) for group in pair.defect_signature):
            return True
    return False


@dataclass
class VariantOutcome:
    pair_id: str
    variant: str
    split: str
    mechanism: str
    label: str            # bug | clean
    flagged: bool
    located: bool
    semantic: bool
    correct_location: bool
    correct_semantic: bool
    findings: int = 0
    tool_calls: int = 0
    tool_hits: int = 0
    tool_misses: int = 0
    tool_errors: int = 0
    tool_budget: int = 0
    model_attempts: int = 0
    model_failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    status: str = "ok"
    note: str = ""
    finding_text: str = ""


@dataclass
class FeedSummary:
    method: str
    split: str
    variants: int = 0
    bugs: int = 0
    cleans: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    accuracy: float | None = None
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    clean_false_alarm_rate: float | None = None
    location_accuracy: float | None = None
    semantic_accuracy: float | None = None
    pairs: int = 0
    pair_consistent: int = 0
    pair_consistency: float | None = None
    semantic_pair_consistent: int = 0
    semantic_pair_consistency: float | None = None
    pair_p_value: float | None = None
    tool_calls: int = 0
    tool_hits: int = 0
    tool_misses: int = 0
    tool_errors: int = 0
    tool_budget: int = 0
    tool_hit_rate: float | None = None
    model_attempts: int = 0
    model_failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    mean_latency_s: float | None = None
    cost_usd: float | None = None
    note: str = ""
    outcomes: list[VariantOutcome] = field(default_factory=list)


def _div(a: int, b: int) -> float | None:
    return None if b == 0 else round(a / b, 4)


def _f1(p, r):
    if p is None or r is None or (p + r) == 0:
        return None
    return round(2 * p * r / (p + r), 4)


def binomial_ge(k: int, n: int, p: float = 0.5) -> float:
    """One-sided P(X >= k) for X ~ Binomial(n, p)."""
    if n == 0:
        return 1.0
    return round(sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1)), 6)


def cost_estimate(prompt_tokens: int, completion_tokens: int) -> float | None:
    try:
        in_price = float(os.environ["PR_GUARDIAN_PRICE_INPUT_PER_M"])
        out_price = float(os.environ["PR_GUARDIAN_PRICE_OUTPUT_PER_M"])
    except (KeyError, ValueError):
        return None
    return round(prompt_tokens / 1_000_000 * in_price + completion_tokens / 1_000_000 * out_price, 6)


# ---------------------------------------------------------------------------
# Running one variant
# ---------------------------------------------------------------------------

def run_variant(method: str, pair: ContrastPair, variant: str, root: Path,
                settings: Settings) -> VariantOutcome:
    repo = materialise(pair, variant, root)
    files, diff_text = read_changed_files(repo, "main", "pr")
    label = "bug" if variant == "a" else "clean"
    has_bug = label == "bug"

    started = time.perf_counter()
    trace = []
    status = "ok"
    note = ""
    usage: dict[str, int] = {}
    attempts = failures = 0

    if method == METHOD_V1:
        res = review_with_llm(diff_text, files, settings=settings)
        findings = list(res.findings)
        usage = res.usage or {}
        attempts = 1
        failures = 0 if res.error == "" else 1
        if res.error:
            status, note = "error", res.error
    else:
        res = review_with_tools(diff_text, files, TrustedCodebase.from_git(repo, "pr"), settings)
        findings = list(res.findings)
        usage = res.usage or {}
        trace = res.trace
        attempts = res.model_attempts
        failures = res.model_failures
        status, note = res.status, res.error

    latency = round(time.perf_counter() - started, 3)
    regions = touched_regions(pair)
    located = any(_in_regions(f, regions) for f in findings)
    flagged = any(f.file_path == pair.diff_file for f in findings)
    semantic = semantic_match(pair, findings) if has_bug else False

    correct_location = (flagged == has_bug)
    correct_semantic = (semantic if has_bug else not flagged)

    return VariantOutcome(
        pair_id=pair.pair_id, variant=variant, split=pair.split, mechanism=pair.mechanism,
        label=label, flagged=flagged, located=located, semantic=semantic,
        correct_location=correct_location, correct_semantic=correct_semantic,
        findings=len(findings),
        tool_calls=len(trace),
        tool_hits=sum(1 for t in trace if t.outcome == "hit"),
        tool_misses=sum(1 for t in trace if t.outcome == "miss"),
        tool_errors=sum(1 for t in trace if t.outcome == "error"),
        tool_budget=sum(1 for t in trace if t.outcome == "budget_denied"),
        model_attempts=attempts,
        model_failures=failures,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        latency_s=latency,
        status=status,
        note=note,
        finding_text=" | ".join(f"L{f.start_line} {f.summary[:90]}" for f in findings)[:400],
    )


def summarise(method: str, split: str, outcomes: list[VariantOutcome],
              pair_count: int) -> FeedSummary:
    ok = [o for o in outcomes if o.status not in {"error"}]
    bugs = [o for o in ok if o.label == "bug"]
    cleans = [o for o in ok if o.label == "clean"]
    tp = sum(1 for o in bugs if o.flagged)
    fn = len(bugs) - tp
    fp = sum(1 for o in cleans if o.flagged)
    tn = len(cleans) - fp
    p, r = _div(tp, tp + fp), _div(tp, tp + fn)

    by_pair: dict[str, dict[str, VariantOutcome]] = {}
    for o in ok:
        by_pair.setdefault(o.pair_id, {})[o.variant] = o
    consistent = sum(1 for v in by_pair.values()
                     if len(v) == 2 and all(x.correct_location for x in v.values()))
    semantic_consistent = sum(1 for v in by_pair.values()
                              if len(v) == 2 and all(x.correct_semantic for x in v.values()))

    calls = sum(o.tool_calls for o in outcomes)
    lat = [o.latency_s for o in ok]
    prompt_tokens = sum(o.prompt_tokens for o in outcomes)
    completion_tokens = sum(o.completion_tokens for o in outcomes)
    return FeedSummary(
        method=method, split=split, variants=len(ok), bugs=len(bugs), cleans=len(cleans),
        tp=tp, fp=fp, fn=fn, tn=tn,
        accuracy=_div(sum(1 for o in ok if o.correct_location), len(ok)),
        precision=p, recall=r, f1=_f1(p, r),
        clean_false_alarm_rate=_div(fp, len(cleans)),
        location_accuracy=_div(sum(1 for o in bugs if o.located), len(bugs)),
        semantic_accuracy=_div(sum(1 for o in ok if o.correct_semantic), len(ok)),
        pairs=pair_count, pair_consistent=consistent,
        pair_consistency=_div(consistent, pair_count),
        semantic_pair_consistent=semantic_consistent,
        semantic_pair_consistency=_div(semantic_consistent, pair_count),
        pair_p_value=binomial_ge(consistent, pair_count),
        tool_calls=calls,
        tool_hits=sum(o.tool_hits for o in outcomes),
        tool_misses=sum(o.tool_misses for o in outcomes),
        tool_errors=sum(o.tool_errors for o in outcomes),
        tool_budget=sum(o.tool_budget for o in outcomes),
        tool_hit_rate=_div(sum(o.tool_hits for o in outcomes), calls),
        model_attempts=sum(o.model_attempts for o in outcomes),
        model_failures=sum(o.model_failures for o in outcomes),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        mean_latency_s=round(sum(lat) / len(lat), 3) if lat else None,
        cost_usd=cost_estimate(prompt_tokens, completion_tokens),
        outcomes=outcomes,
    )


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:.2%}"


def render(summaries: list[FeedSummary]) -> str:
    lines = [
        "# Contrast-pair benchmark (same diff, different external context)",
        "",
        "Two layers are scored separately: **located** (finding inside a touched",
        "definition) and **semantic** (finding text matches the case's defect",
        "signature). Pair consistency is not accuracy — see",
        "`docs/v3-contrast-design.md` §4.",
        "",
        "## Detection",
        "",
        "| Method | Split | Variants (bug/clean) | TP | FP | FN | Accuracy | Precision | Recall | F1 | Clean false alarm | Location acc. | Semantic acc. |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s.method} | {s.split} | {s.variants} ({s.bugs}/{s.cleans}) | {s.tp} | {s.fp} | "
            f"{s.fn} | {_pct(s.accuracy)} | {_pct(s.precision)} | {_pct(s.recall)} | {_pct(s.f1)} | "
            f"{_pct(s.clean_false_alarm_rate)} | {_pct(s.location_accuracy)} | {_pct(s.semantic_accuracy)} |"
        )
    lines += ["", "## Pair consistency (both variants correct)", "",
              "| Method | Split | Pairs | Consistent (located) | Pair consistency | Semantic consistent | Semantic pair consistency | Binomial p (vs 50% coin flip) |",
              "|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s.method} | {s.split} | {s.pairs} | {s.pair_consistent} | "
            f"{_pct(s.pair_consistency)} | {s.semantic_pair_consistent} | "
            f"{_pct(s.semantic_pair_consistency)} | {s.pair_p_value} |"
        )
    lines += ["", "## Tool use and cost", "",
              "| Method | Split | Model requests (failed) | Tool calls | Hits | Misses | Errors | Budget denied | Effective hit rate | Prompt tokens | Completion tokens | Mean latency (s) | Cost (USD) |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s.method} | {s.split} | {s.model_attempts} ({s.model_failures}) | {s.tool_calls} | "
            f"{s.tool_hits} | {s.tool_misses} | {s.tool_errors} | {s.tool_budget} | "
            f"{_pct(s.tool_hit_rate)} | {s.prompt_tokens} | {s.completion_tokens} | "
            f"{s.mean_latency_s if s.mean_latency_s is not None else 'n/a'} | "
            f"{s.cost_usd if s.cost_usd is not None else 'not computed'} |"
        )
    lines += ["", "## Per-variant detail", "",
              "| Method | Pair | Var | Label | Flagged | Located | Semantic | Correct | Tools | Findings |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        for o in s.outcomes:
            note = (o.finding_text or "").replace("|", "/")
            lines.append(
                f"| {s.method} | {o.pair_id} | {o.variant} | {o.label} | {o.flagged} | "
                f"{o.located} | {o.semantic} | {o.correct_semantic} | {o.tool_calls} | {note} |"
            )
    return "\n".join(lines) + "\n"


def write_outputs(summaries: list[FeedSummary], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(
        json.dumps([asdict(s) for s in summaries], indent=2, ensure_ascii=False), encoding="utf-8")
    summary_fields = [f for f in FeedSummary.__dataclass_fields__ if f != "outcomes"]
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=summary_fields, extrasaction="ignore")
        w.writeheader()
        for s in summaries:
            w.writerow(asdict(s))
    variant_fields = list(VariantOutcome.__dataclass_fields__)
    with (out / "variants.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["method"] + variant_fields)
        for s in summaries:
            for o in s.outcomes:
                w.writerow([s.method] + [getattr(o, f) for f in variant_fields])
    (out / "REPORT.md").write_text(render(summaries), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    ap.add_argument("--methods", default=f"{METHOD_V1},{METHOD_V2}")
    ap.add_argument("--out", type=Path, default=Path("docs/benchmark-results/contrast-dev"))
    args = ap.parse_args(argv)

    settings = get_settings()
    if not settings.api_key:
        print("NOT EVALUATED: PR_GUARDIAN_API_KEY is not set.", file=sys.stderr)
        return 3

    pairs = build_index()
    if args.split != "all":
        pairs = [p for p in pairs if p.split == args.split]
    methods = [m.strip() for m in args.methods.split(",") if m]

    summaries: list[FeedSummary] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for method in methods:
            print(f"running {method} on {len(pairs)} pairs ({len(pairs) * 2} variants) ...",
                  file=sys.stderr)
            outcomes: list[VariantOutcome] = []
            for pair in pairs:
                for variant in ("a", "b"):
                    try:
                        outcomes.append(run_variant(method, pair, variant, root, settings))
                    except Exception as exc:  # noqa: BLE001 - record, never crash the run
                        outcomes.append(VariantOutcome(
                            pair_id=pair.pair_id, variant=variant, split=pair.split,
                            mechanism=pair.mechanism,
                            label="bug" if variant == "a" else "clean",
                            flagged=False, located=False, semantic=False,
                            correct_location=False, correct_semantic=False,
                            status="error", note=f"{type(exc).__name__}: {exc}"))
            summaries.append(summarise(method, args.split, outcomes, len(pairs)))

    write_outputs(summaries, args.out)
    print(render(summaries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
