# Re-scored run (current label rules, no new model calls)

Source: `docs/benchmark-results/contrast-dev-v2/results.json`

# Contrast-pair benchmark (same diff, different external context)

Two layers are scored separately: **located** (finding in the changed file,
inside a touched definition) and **semantic (keyword-assisted)** (the text
satisfies the frozen signature). The keyword layer is a screen, NOT a full
semantic judgement: every finding is adjudicated in
`human-review-adjudicated.csv`. Pair consistency is not accuracy - see
`docs/v3-contrast-design.md` section 4.

Random baseline: for a pair (A=bug, B=clean), independent Bernoulli(p)
predictions get BOTH variants right with probability p(1-p) - 25% at p=0.5,
not 50%. Pair consistency is therefore tested against p0 = 0.25, and only
over pairs where neither variant was contested.

Contested: a clean variant whose diff does change the changed function's own
behaviour (side effect, exception type, default, rounding, accepted scheme,
empty input). The repository makes it harmless, but a finding that describes
that change is not scored as FP.

## Detection

| Method | Split | Variants (bug/clean) | TP | FP | FN | TN | Contested | Decided | Accuracy (decided) | Precision | Recall | F1 | Clean false alarm (decided) | Precision (strict) | Clean false alarm (strict) | Location acc. | Semantic acc. (keyword) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v1-diff-only | dev | 10 (5/5) | 5 | 1 | 0 | 2 | 2 | 8 | 87.50% | 83.33% | 100.00% | 90.91% | 33.33% | 62.50% | 60.00% | 100.00% | 87.50% |
| v2-tools | dev | 10 (5/5) | 5 | 0 | 0 | 3 | 2 | 8 | 100.00% | 100.00% | 100.00% | 100.00% | 0.00% | 71.43% | 40.00% | 100.00% | 100.00% |

## Pair consistency (both variants correct)

| Method | Split | Pairs | Pairs decided | Consistent | Pair consistency | Semantic consistent | Semantic pair consistency | Binomial p (null p0 = 0.25) |
|---|---|---|---|---|---|---|---|---|
| v1-diff-only | dev | 5 | 3 | 2 | 66.67% | 2 | 66.67% | 0.15625 |
| v2-tools | dev | 5 | 3 | 3 | 100.00% | 3 | 100.00% | 0.015625 |

## Tool use and cost

| Method | Split | Model requests (failed) | Tool calls | Hits | Misses | Errors | Budget denied | Effective hit rate | Prompt tokens | Completion tokens | Mean latency (s) | Cost (USD) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v1-diff-only | dev | 10 (0) | 0 | 0 | 0 | 0 | 0 | n/a | 3798 | 26148 | 16.955 | not computed |
| v2-tools | dev | 32 (0) | 42 | 30 | 1 | 4 | 7 | 71.43% | 48296 | 10789 | 8.191 | not computed |

## Per-variant detail

| Method | Pair | Var | Label | Flagged | Located | Semantic (keyword) | Correct | Tools | Findings |
|---|---|---|---|---|---|---|---|---|---|
| v1-diff-only | P1 | a | bug | True | True | True | True | 0 | L7 calculate now passes an unsupported `strict` keyword to `apply_rate`. |
| v1-diff-only | P1 | b | clean | False | False | False | True | 0 |  |
| v1-diff-only | P2 | a | bug | True | True | True | True | 0 | L7 The new call passes a cents amount to convert, so tax_for returns tax in the wrong unit. |
| v1-diff-only | P2 | b | clean | False | False | False | True | 0 |  |
| v1-diff-only | P3 | a | bug | True | True | True | True | 0 | L6 with_item now aliases the caller's input list instead of copying it |
| v1-diff-only | P3 | b | clean | True | True | True | False | 0 | L6 with_item now aliases and mutates the caller's list instead of returning a new list |
| v1-diff-only | P4 | a | bug | True | True | True | True | 0 | L8 Off-by-one: quantities equal to the limit are now rejected |
| v1-diff-only | P4 | b | clean | True | True | True | False | 0 | L8 Changing `<=` to `<` rejects a quantity exactly equal to the limit |
| v1-diff-only | P5 | a | bug | True | True | True | True | 0 | L7 reserve now raises LookupError instead of ValueError for insufficient stock |
| v1-diff-only | P5 | b | clean | True | True | True | False | 0 | L7 reserve() now raises LookupError instead of ValueError for insufficient stock, breaking th |
| v2-tools | P1 | a | bug | True | True | True | True | 3 | L7 calculate() now calls apply_rate with an unsupported keyword argument strict=True, causing |
| v2-tools | P1 | b | clean | False | False | False | True | 3 |  |
| v2-tools | P2 | a | bug | True | True | True | True | 5 | L7 tax_for now passes a value in cents into convert(), which expects dollars, inflating tax b |
| v2-tools | P2 | b | clean | False | False | False | True | 4 |  |
| v2-tools | P3 | a | bug | True | True | True | True | 2 | L6 with_item now mutates the caller's input list instead of operating on a copy |
| v2-tools | P3 | b | clean | True | True | True | False | 3 | L6 with_item no longer copies its input, so it mutates the caller's list in place instead of  |
| v2-tools | P4 | a | bug | True | True | True | True | 5 | L8 Changing the comparison to `qty < limit` makes `accept` return False when qty equals limit |
| v2-tools | P4 | b | clean | False | False | False | True | 6 |  |
| v2-tools | P5 | a | bug | True | True | True | True | 5 | L7 reserve() now raises LookupError instead of ValueError, breaking the established exception |
| v2-tools | P5 | b | clean | True | True | True | False | 6 | L6 reserve() now raises LookupError instead of ValueError, breaking the documented/establishe |
