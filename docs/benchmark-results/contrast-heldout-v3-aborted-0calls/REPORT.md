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
| v1-diff-only | heldout | 0 (0/0) | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| v2-tools | heldout | 0 (0/0) | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

## Pair consistency (both variants correct)

| Method | Split | Pairs | Pairs decided | Consistent | Pair consistency | Semantic consistent | Semantic pair consistency | Binomial p (null p0 = 0.25) |
|---|---|---|---|---|---|---|---|---|
| v1-diff-only | heldout | 6 | 0 | 0 | n/a | 0 | n/a | 1.0 |
| v2-tools | heldout | 6 | 0 | 0 | n/a | 0 | n/a | 1.0 |

## Tool use and cost

| Method | Split | Model requests (failed) | Tool calls | Hits | Misses | Errors | Budget denied | Effective hit rate | Prompt tokens | Completion tokens | Mean latency (s) | Cost (USD) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v1-diff-only | heldout | 0 (0) | 0 | 0 | 0 | 0 | 0 | n/a | 0 | 0 | n/a | not computed |
| v2-tools | heldout | 0 (0) | 0 | 0 | 0 | 0 | 0 | n/a | 0 | 0 | n/a | not computed |

## Per-variant detail

| Method | Pair | Var | Label | Flagged | Located | Semantic (keyword) | Correct | Tools | Findings |
|---|---|---|---|---|---|---|---|---|---|
| v1-diff-only | P6 | a | bug | False | False | False | False | 0 |  |
| v1-diff-only | P6 | b | clean | False | False | False | False | 0 |  |
| v1-diff-only | P7 | a | bug | False | False | False | False | 0 |  |
| v1-diff-only | P7 | b | clean | False | False | False | False | 0 |  |
| v1-diff-only | P8 | a | bug | False | False | False | False | 0 |  |
| v1-diff-only | P8 | b | clean | False | False | False | False | 0 |  |
| v1-diff-only | P9 | a | bug | False | False | False | False | 0 |  |
| v1-diff-only | P9 | b | clean | False | False | False | False | 0 |  |
| v1-diff-only | P10 | a | bug | False | False | False | False | 0 |  |
| v1-diff-only | P10 | b | clean | False | False | False | False | 0 |  |
| v1-diff-only | P11 | a | bug | False | False | False | False | 0 |  |
| v1-diff-only | P11 | b | clean | False | False | False | False | 0 |  |
| v2-tools | P6 | a | bug | False | False | False | False | 0 |  |
| v2-tools | P6 | b | clean | False | False | False | False | 0 |  |
| v2-tools | P7 | a | bug | False | False | False | False | 0 |  |
| v2-tools | P7 | b | clean | False | False | False | False | 0 |  |
| v2-tools | P8 | a | bug | False | False | False | False | 0 |  |
| v2-tools | P8 | b | clean | False | False | False | False | 0 |  |
| v2-tools | P9 | a | bug | False | False | False | False | 0 |  |
| v2-tools | P9 | b | clean | False | False | False | False | 0 |  |
| v2-tools | P10 | a | bug | False | False | False | False | 0 |  |
| v2-tools | P10 | b | clean | False | False | False | False | 0 |  |
| v2-tools | P11 | a | bug | False | False | False | False | 0 |  |
| v2-tools | P11 | b | clean | False | False | False | False | 0 |  |
