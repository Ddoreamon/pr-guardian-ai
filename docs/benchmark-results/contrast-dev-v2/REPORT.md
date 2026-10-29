# Contrast-pair benchmark (same diff, different external context)

Two layers are scored separately: **located** (finding in the changed file,
inside a touched definition) and **semantic (keyword-assisted)** (the text
satisfies the frozen signature). The keyword layer is a screen, NOT a full
semantic judgement: every finding is adjudicated in
`human-review-adjudicated.csv`. Pair consistency is not accuracy - see
`docs/v3-contrast-design.md` section 4.

## Detection

| Method | Split | Variants (bug/clean) | TP | FP | FN | Accuracy | Precision | Recall | F1 | Clean false alarm | Location acc. | Semantic acc. (keyword) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v1-diff-only | dev | 10 (5/5) | 5 | 3 | 0 | 70.00% | 62.50% | 100.00% | 76.92% | 60.00% | 100.00% | 70.00% |
| v2-tools | dev | 10 (5/5) | 5 | 2 | 0 | 80.00% | 71.43% | 100.00% | 83.33% | 40.00% | 100.00% | 80.00% |

## Pair consistency (both variants correct)

| Method | Split | Pairs | Consistent (located) | Pair consistency | Semantic consistent (keyword) | Semantic pair consistency | Binomial p (vs 50% coin flip) |
|---|---|---|---|---|---|---|---|
| v1-diff-only | dev | 5 | 2 | 40.00% | 2 | 40.00% | 0.8125 |
| v2-tools | dev | 5 | 3 | 60.00% | 3 | 60.00% | 0.5 |

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
| v1-diff-only | P3 | b | clean | True | True | False | False | 0 | L6 with_item now aliases and mutates the caller's list instead of returning a new list |
| v1-diff-only | P4 | a | bug | True | True | True | True | 0 | L8 Off-by-one: quantities equal to the limit are now rejected |
| v1-diff-only | P4 | b | clean | True | True | False | False | 0 | L8 Changing `<=` to `<` rejects a quantity exactly equal to the limit |
| v1-diff-only | P5 | a | bug | True | True | True | True | 0 | L7 reserve now raises LookupError instead of ValueError for insufficient stock |
| v1-diff-only | P5 | b | clean | True | True | False | False | 0 | L7 reserve() now raises LookupError instead of ValueError for insufficient stock, breaking th |
| v2-tools | P1 | a | bug | True | True | True | True | 3 | L7 calculate() now calls apply_rate with an unsupported keyword argument strict=True, causing |
| v2-tools | P1 | b | clean | False | False | False | True | 3 |  |
| v2-tools | P2 | a | bug | True | True | True | True | 5 | L7 tax_for now passes a value in cents into convert(), which expects dollars, inflating tax b |
| v2-tools | P2 | b | clean | False | False | False | True | 4 |  |
| v2-tools | P3 | a | bug | True | True | True | True | 2 | L6 with_item now mutates the caller's input list instead of operating on a copy |
| v2-tools | P3 | b | clean | True | True | False | False | 3 | L6 with_item no longer copies its input, so it mutates the caller's list in place instead of  |
| v2-tools | P4 | a | bug | True | True | True | True | 5 | L8 Changing the comparison to `qty < limit` makes `accept` return False when qty equals limit |
| v2-tools | P4 | b | clean | False | False | False | True | 6 |  |
| v2-tools | P5 | a | bug | True | True | True | True | 5 | L7 reserve() now raises LookupError instead of ValueError, breaking the established exception |
| v2-tools | P5 | b | clean | True | True | False | False | 6 | L6 reserve() now raises LookupError instead of ValueError, breaking the documented/establishe |
