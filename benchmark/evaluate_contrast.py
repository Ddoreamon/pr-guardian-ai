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
import platform
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from benchmark.contrast_cases import (
    UNCONTROVERSIAL,
    VERSION,
    ContrastPair,
    build_index,
    clean_class_of,
    files_for,
)
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


def _located(pair: ContrastPair, findings: list[ReviewFinding]) -> bool:
    """True only when a finding is in the changed file AND inside a touched region."""
    regions = touched_regions(pair)
    return any(f.file_path == pair.diff_file and _in_regions(f, regions) for f in findings)


def semantic_match(pair: ContrastPair, findings: list[ReviewFinding]) -> bool:
    """Keyword-assisted match: a located finding on the changed file whose text
    satisfies every signature group.

    This is NOT a full semantic judgement of correctness. It is a narrow screen
    that must be confirmed by a human reviewer; the raw text of every finding is
    stored so the adjudication can be checked.
    """
    regions = touched_regions(pair)
    for finding in findings:
        if finding.file_path != pair.diff_file:
            continue
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
    clean_class: str = ""
    # TP | FP | FN | TN | contested
    outcome_class: str = ""
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
    findings_detail: list = field(default_factory=list)
    tool_status: str = ""
    partial: bool = False
    tool_trace: list = field(default_factory=list)
    human_verdict: str = ""
    human_note: str = ""


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
    contested: int = 0
    decided: int = 0
    contested_rate: float | None = None
    accuracy: float | None = None
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    clean_false_alarm_rate: float | None = None
    # strict reading: a contested finding is treated as a false positive, so a
    # model cannot improve its score by flagging every context-dependent case
    precision_strict: float | None = None
    clean_false_alarm_rate_strict: float | None = None
    location_accuracy: float | None = None
    semantic_accuracy: float | None = None
    pairs: int = 0
    pairs_decided: int = 0
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


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for discordant counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) * 0.5**n for i in range(k + 1))
    return round(min(1.0, 2 * tail), 6)


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
    tool_status = "not_applicable"
    partial = False

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
        tool_status = res.status
        partial = res.status in {"tool_limit", "schema_invalid", "model_error"}

    latency = round(time.perf_counter() - started, 3)
    located = _located(pair, findings)
    flagged = any(f.file_path == pair.diff_file for f in findings)
    # computed for clean variants as well: it decides contested vs FP
    semantic = semantic_match(pair, findings)

    correct_location = (flagged == has_bug)
    correct_semantic = (semantic if has_bug else not flagged)

    cls = clean_class_of(pair.pair_id)
    if has_bug:
        outcome_class = "TP" if located else "FN"
    elif not located:
        outcome_class = "TN"
    elif cls == UNCONTROVERSIAL:
        outcome_class = "FP"
    elif semantic:
        # the finding accurately describes a real change in the changed
        # function; the repository makes it harmless, so it is not a defect,
        # but calling it an FP would overstate the model's error.
        outcome_class = "contested"
    else:
        outcome_class = "FP"

    return VariantOutcome(
        pair_id=pair.pair_id, variant=variant, split=pair.split, mechanism=pair.mechanism,
        label=label, flagged=flagged, located=located, semantic=semantic,
        correct_location=correct_location, correct_semantic=correct_semantic,
        clean_class=cls, outcome_class=outcome_class,
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
        findings_detail=[f.model_dump(mode="json") for f in findings],
        tool_status=tool_status,
        partial=partial,
        tool_trace=[t.as_dict() for t in trace],
    )


def summarise(method: str, split: str, outcomes: list[VariantOutcome],
              pair_count: int) -> FeedSummary:
    ok = [o for o in outcomes if o.status not in {"error"}]
    bugs = [o for o in ok if o.label == "bug"]
    cleans = [o for o in ok if o.label == "clean"]
    tp = sum(1 for o in ok if o.outcome_class == "TP")
    fp = sum(1 for o in ok if o.outcome_class == "FP")
    fn = sum(1 for o in ok if o.outcome_class == "FN")
    tn = sum(1 for o in ok if o.outcome_class == "TN")
    contested = sum(1 for o in ok if o.outcome_class == "contested")
    decided = tp + fp + fn + tn
    p, r = _div(tp, tp + fp), _div(tp, tp + fn)

    by_pair: dict[str, dict[str, VariantOutcome]] = {}
    for o in ok:
        by_pair.setdefault(o.pair_id, {})[o.variant] = o
    # pair consistency only over pairs where neither variant was contested
    decided_pairs = {k: v for k, v in by_pair.items()
                     if len(v) == 2
                     and all(x.outcome_class != "contested" for x in v.values())}
    consistent = sum(1 for v in decided_pairs.values()
                     if all(x.outcome_class in {"TP", "TN"} for x in v.values()))
    semantic_consistent = sum(1 for v in decided_pairs.values()
                              if all(x.correct_semantic for x in v.values()))

    calls = sum(o.tool_calls for o in outcomes)
    lat = [o.latency_s for o in ok]
    prompt_tokens = sum(o.prompt_tokens for o in outcomes)
    completion_tokens = sum(o.completion_tokens for o in outcomes)
    return FeedSummary(
        method=method, split=split, variants=len(ok), bugs=len(bugs), cleans=len(cleans),
        tp=tp, fp=fp, fn=fn, tn=tn, contested=contested, decided=decided,
        contested_rate=_div(contested, len(ok)),
        accuracy=_div(tp + tn, decided),
        precision=p, recall=r, f1=_f1(p, r),
        clean_false_alarm_rate=_div(fp, fp + tn),
        precision_strict=_div(tp, tp + fp + contested),
        clean_false_alarm_rate_strict=_div(fp + contested, len(cleans)),
        location_accuracy=_div(sum(1 for o in bugs if o.located), len(bugs)),
        semantic_accuracy=_div(sum(1 for o in ok if o.outcome_class in {"TP", "TN"}), decided),
        pairs=pair_count, pairs_decided=len(decided_pairs),
        pair_consistent=consistent,
        pair_consistency=_div(consistent, len(decided_pairs)),
        semantic_pair_consistent=semantic_consistent,
        semantic_pair_consistency=_div(semantic_consistent, len(decided_pairs)),
        # null p0 = 0.25: an independent coin flip gets BOTH variants of a
        # (bug, clean) pair right with probability p(1-p), i.e. 0.25 at p=0.5
        pair_p_value=binomial_ge(consistent, len(decided_pairs), 0.25),
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
        "Two layers are scored separately: **located** (finding in the changed file,",
        "inside a touched definition) and **semantic (keyword-assisted)** (the text",
        "satisfies the frozen signature). The keyword layer is a screen, NOT a full",
        "semantic judgement: every finding is adjudicated in",
        "`human-review-adjudicated.csv`. Pair consistency is not accuracy - see",
        "`docs/v3-contrast-design.md` section 4.",
        "",
        "Random baseline: for a pair (A=bug, B=clean), independent Bernoulli(p)",
        "predictions get BOTH variants right with probability p(1-p) - 25% at p=0.5,",
        "not 50%. Pair consistency is therefore tested against p0 = 0.25, and only",
        "over pairs where neither variant was contested.",
        "",
        "Contested: a clean variant whose diff does change the changed function's own",
        "behaviour (side effect, exception type, default, rounding, accepted scheme,",
        "empty input). The repository makes it harmless, but a finding that describes",
        "that change is not scored as FP.",
        "",
        "## Detection",
        "",
        "| Method | Split | Variants (bug/clean) | TP | FP | FN | TN | Contested | Decided | Accuracy (decided) | Precision | Recall | F1 | Clean false alarm (decided) | Precision (strict) | Clean false alarm (strict) | Location acc. | Semantic acc. (keyword) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s.method} | {s.split} | {s.variants} ({s.bugs}/{s.cleans}) | {s.tp} | {s.fp} | "
            f"{s.fn} | {s.tn} | {s.contested} | {s.decided} | {_pct(s.accuracy)} | "
            f"{_pct(s.precision)} | {_pct(s.recall)} | {_pct(s.f1)} | "
            f"{_pct(s.clean_false_alarm_rate)} | {_pct(s.precision_strict)} | "
            f"{_pct(s.clean_false_alarm_rate_strict)} | {_pct(s.location_accuracy)} | "
            f"{_pct(s.semantic_accuracy)} |"
        )
    lines += ["", "## Pair consistency (both variants correct)", "",
              "| Method | Split | Pairs | Pairs decided | Consistent | Pair consistency | Semantic consistent | Semantic pair consistency | Binomial p (null p0 = 0.25) |",
              "|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s.method} | {s.split} | {s.pairs} | {s.pairs_decided} | {s.pair_consistent} | "
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
              "| Method | Pair | Var | Label | Flagged | Located | Semantic (keyword) | Correct | Tools | Findings |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        for o in s.outcomes:
            note = (o.finding_text or "").replace("|", "/")
            lines.append(
                f"| {s.method} | {o.pair_id} | {o.variant} | {o.label} | {o.flagged} | "
                f"{o.located} | {o.semantic} | {o.correct_semantic} | {o.tool_calls} | {note} |"
            )
    return "\n".join(lines) + "\n"


def _git(*args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], capture_output=True, text=True,
                              timeout=20, check=False)
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def experiment_record(args, settings: Settings, pairs: list[ContrastPair],
                      methods: list[str], started: str, finished: str) -> dict:
    """Everything needed to reproduce this run. The API key is never recorded."""
    return {
        "started_at": started,
        "finished_at": finished,
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_sha": _git("rev-parse", "HEAD"),
        "git_tree_clean": _git("status", "--porcelain") == "",
        "dataset_version": VERSION,
        "split": args.split,
        "methods": methods,
        "pairs": [
            {"pair_id": p.pair_id, "split": p.split, "mechanism": p.mechanism,
             "clean_class": clean_class_of(p.pair_id)}
            for p in pairs
        ],
        "model": settings.model,
        "base_url": settings.base_url,
        "public_mode": settings.public_mode,
        "allow_test_execution": settings.allow_test_execution,
        "max_live_llm_calls": settings.max_live_llm_calls,
        "tool_budget": {"max_rounds": settings.max_tool_rounds,
                        "max_calls": settings.max_tool_calls},
        "evaluator": ("benchmark/evaluate_contrast.py — label classes v3, "
                      "strict and decided readings, exact McNemar"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "api_key_recorded": False,
    }


def write_experiment(out: Path, record: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "experiment.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Experiment record", "",
             "| Field | Value |", "|---|---|"]
    for key in ("started_at", "finished_at", "git_branch", "git_sha",
                "git_tree_clean", "dataset_version", "split", "methods",
                "model", "base_url", "public_mode", "allow_test_execution",
                "max_live_llm_calls", "tool_budget", "evaluator", "python",
                "platform", "api_key_recorded"):
        value = record.get(key)
        lines.append(f"| `{key}` | `{json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value}` |")
    lines += ["", "## Pairs", "", "| Pair | Split | Mechanism | Clean class |", "|---|---|---|---|"]
    for p in record["pairs"]:
        lines.append(f"| {p['pair_id']} | {p['split']} | {p['mechanism']} | {p['clean_class']} |")
    (out / "EXPERIMENT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def compare_methods(first: FeedSummary, second: FeedSummary) -> str:
    """Paired comparison of two methods on the same variants (exact McNemar)."""
    key = lambda o: (o.pair_id, o.variant)
    a = {key(o): o for o in first.outcomes if o.outcome_class != "contested"}
    b = {key(o): o for o in second.outcomes if o.outcome_class != "contested"}
    shared = sorted(set(a) & set(b))
    only_a = sum(1 for k in shared
                 if a[k].outcome_class in {"TP", "TN"}
                 and b[k].outcome_class not in {"TP", "TN"})
    only_b = sum(1 for k in shared
                 if b[k].outcome_class in {"TP", "TN"}
                 and a[k].outcome_class not in {"TP", "TN"})
    p_value = mcnemar_exact(only_b, only_a)
    return (
        "\n## Paired comparison (same variants, decided only)\n\n"
        f"| Comparison | Shared variants | Only {first.method} correct | "
        f"Only {second.method} correct | Exact McNemar p |\n"
        "|---|---|---|---|---|\n"
        f"| {first.method} vs {second.method} | {len(shared)} | {only_a} | {only_b} | {p_value} |\n"
    )


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

    # human adjudication sheet: automated flags are suggestions, not verdicts
    with (out / "human-review.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["method", "pair_id", "variant", "label", "flagged", "located",
                    "semantic_keyword", "auto_correct", "findings",
                    "human_verdict (TP/FP/FN/TN/?)", "human_note"])
        for s in summaries:
            for o in s.outcomes:
                w.writerow([
                    s.method, o.pair_id, o.variant, o.label, o.flagged, o.located,
                    o.semantic, o.correct_semantic,
                    " || ".join(f"{f.get('file_path')}:{f.get('start_line')} {f.get('summary', '')}"
                                for f in o.findings_detail),
                    o.human_verdict, o.human_note,
                ])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    ap.add_argument("--methods", default=f"{METHOD_V1},{METHOD_V2}")
    ap.add_argument("--out", type=Path, default=Path("docs/benchmark-results/contrast-dev-v2"))
    args = ap.parse_args(argv)

    settings = get_settings()
    if not settings.api_key:
        print("NOT EVALUATED: PR_GUARDIAN_API_KEY is not set.", file=sys.stderr)
        return 3

    pairs = build_index()
    if args.split != "all":
        pairs = [p for p in pairs if p.split == args.split]
    methods = [m.strip() for m in args.methods.split(",") if m]

    started_at = datetime.now(UTC).isoformat(timespec="seconds")
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

    finished_at = datetime.now(UTC).isoformat(timespec="seconds")
    write_experiment(args.out, experiment_record(args, settings, pairs, methods,
                                                 started_at, finished_at))
    write_outputs(summaries, args.out)
    print(render(summaries))
    if len(summaries) >= 2:
        pairwise = compare_methods(summaries[0], summaries[1])
        print(pairwise)
        (args.out / "PAIRWISE.md").write_text(pairwise, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
