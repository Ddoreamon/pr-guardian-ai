"""Re-score a stored contrast run under the current label rules — no model calls.

Used to correct a historical artefact without overwriting it: the previous
results.json is read, each variant is reclassified with the current
clean_class rules, and the corrected summary is written to a new directory.

Run:
    python -m benchmark.rescore_contrast \
        --results docs/benchmark-results/contrast-dev-v2/results.json \
        --out docs/benchmark-results/contrast-dev-v2-rescored
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
from pathlib import Path

from benchmark.contrast_cases import UNCONTROVERSIAL, build_index, clean_class_of
from benchmark.evaluate_contrast import (
    FeedSummary,
    VariantOutcome,
    _located,
    compare_methods,
    render,
    semantic_match,
    summarise,
)
from pr_guardian.models import ReviewFinding

PAIRS = {p.pair_id: p for p in build_index()}


def reclassify(o: VariantOutcome) -> VariantOutcome:
    """Recompute located/semantic from the stored raw findings, then label.

    Nothing is taken on trust from the previous scoring code, which did not
    evaluate the keyword screen for clean variants at all.
    """
    pair = PAIRS[o.pair_id]
    findings = [ReviewFinding(**f) for f in (o.findings_detail or [])]
    located = _located(pair, findings)
    semantic = semantic_match(pair, findings)
    cls = clean_class_of(o.pair_id)
    has_bug = o.label == "bug"
    if has_bug:
        outcome_class = "TP" if located else "FN"
    elif not located:
        outcome_class = "TN"
    elif cls == UNCONTROVERSIAL:
        outcome_class = "FP"
    elif semantic:
        outcome_class = "contested"
    else:
        outcome_class = "FP"
    return dataclasses.replace(
        o, located=located, semantic=semantic, clean_class=cls,
        outcome_class=outcome_class,
        correct_location=(located == has_bug),
        correct_semantic=(semantic if has_bug else not located),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    data = json.loads(args.results.read_text(encoding="utf-8"))
    onames = {f.name for f in dataclasses.fields(VariantOutcome)}
    summaries: list[FeedSummary] = []
    for entry in data:
        outcomes = [reclassify(VariantOutcome(**{k: v for k, v in o.items() if k in onames}))
                    for o in entry["outcomes"]]
        pairs = len({o.pair_id for o in outcomes})
        summaries.append(summarise(entry["method"], entry["split"], outcomes, pairs))

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(
        json.dumps([dataclasses.asdict(s) for s in summaries], indent=2, ensure_ascii=False),
        encoding="utf-8")
    (args.out / "REPORT.md").write_text(
        "# Re-scored run (current label rules, no new model calls)\n\n"
        f"Source: `{args.results.as_posix()}`\n\n" + render(summaries),
        encoding="utf-8")
    with (args.out / "variants.csv").open("w", newline="", encoding="utf-8") as fh:
        fields = list(VariantOutcome.__dataclass_fields__)
        w = csv.writer(fh)
        w.writerow(["method"] + fields)
        for s in summaries:
            for o in s.outcomes:
                w.writerow([s.method] + [getattr(o, f) for f in fields])

    print(render(summaries))
    if len(summaries) >= 2:
        print(compare_methods(summaries[0], summaries[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
