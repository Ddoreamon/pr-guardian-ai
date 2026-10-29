# Contrast-pair benchmark (same diff, different external context)

Two layers are scored separately: **located** (finding inside a touched
definition) and **semantic** (finding text matches the case's defect
signature). Pair consistency is not accuracy — see
`docs/v3-contrast-design.md` §4.

## Detection

| Method | Split | Variants (bug/clean) | TP | FP | FN | Accuracy | Precision | Recall | F1 | Clean false alarm | Location acc. | Semantic acc. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v1-diff-only | dev | 10 (5/5) | 3 | 3 | 2 | 50.00% | 50.00% | 60.00% | 54.55% | 60.00% | 60.00% | 50.00% |
| v2-tools | dev | 10 (5/5) | 5 | 3 | 0 | 70.00% | 62.50% | 100.00% | 76.92% | 60.00% | 100.00% | 70.00% |

## Pair consistency (both variants correct)

| Method | Split | Pairs | Consistent (located) | Pair consistency | Semantic consistent | Semantic pair consistency | Binomial p (vs 50% coin flip) |
|---|---|---|---|---|---|---|---|
| v1-diff-only | dev | 5 | 0 | 0.00% | 0 | 0.00% | 1.0 |
| v2-tools | dev | 5 | 2 | 40.00% | 2 | 40.00% | 0.8125 |

## Tool use and cost

| Method | Split | Model requests (failed) | Tool calls | Hits | Misses | Errors | Budget denied | Effective hit rate | Prompt tokens | Completion tokens | Mean latency (s) | Cost (USD) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v1-diff-only | dev | 10 (0) | 0 | 0 | 0 | 0 | 0 | n/a | 3010 | 6690 | 4.699 | not computed |
| v2-tools | dev | 27 (0) | 28 | 27 | 0 | 1 | 0 | 96.43% | 34728 | 6789 | 5.587 | not computed |

## Per-variant detail

| Method | Pair | Var | Label | Flagged | Located | Semantic | Correct | Tools | Findings |
|---|---|---|---|---|---|---|---|---|---|
| v1-diff-only | P1 | a | bug | False | False | False | False | 0 |  |
| v1-diff-only | P1 | b | clean | False | False | False | True | 0 |  |
| v1-diff-only | P2 | a | bug | False | False | False | False | 0 |  |
| v1-diff-only | P2 | b | clean | False | False | False | True | 0 |  |
| v1-diff-only | P3 | a | bug | True | True | True | True | 0 | L7 Empty-query branch now returns None, violating the declared list[dict] return contract |
| v1-diff-only | P3 | b | clean | True | True | False | False | 0 | L7 search() now returns None instead of an empty list, violating its declared `list[dict]` re |
| v1-diff-only | P4 | a | bug | True | True | True | True | 0 | L8 Off-by-one: qty equal to limit is now rejected |
| v1-diff-only | P4 | b | clean | True | True | False | False | 0 | L8 Inclusive boundary changed to exclusive: qty == limit is now rejected |
| v1-diff-only | P5 | a | bug | True | True | True | True | 0 | L7 Exception type changed from ValueError to LookupError, breaking callers that catch ValueEr |
| v1-diff-only | P5 | b | clean | True | True | False | False | 0 | L7 Insufficient-stock error now raises LookupError, changing the exception contract and miscl |
| v2-tools | P1 | a | bug | True | True | True | True | 3 | L7 calculate() passes an unsupported keyword argument `strict` to apply_rate, which takes onl |
| v2-tools | P1 | b | clean | False | False | False | True | 3 |  |
| v2-tools | P2 | a | bug | True | True | True | True | 4 | L7 tax_for now passes a cents value into convert(), which expects dollars, inflating tax by 1 |
| v2-tools | P2 | b | clean | True | True | False | False | 3 | L7 to_cents is an identity function, so the change is a no-op and tax is still computed as if |
| v2-tools | P3 | a | bug | True | True | True | True | 2 | L7 Empty-query branch now returns None instead of an empty list, violating the declared retur |
| v2-tools | P3 | b | clean | True | True | False | False | 2 | L4 search() now returns None for an empty query instead of an empty list, violating its decla |
| v2-tools | P4 | a | bug | True | True | True | True | 4 | L8 Inclusive boundary dropped: qty == limit is now rejected, contradicting the documented "wi |
| v2-tools | P4 | b | clean | False | False | False | True | 3 |  |
| v2-tools | P5 | a | bug | True | True | True | True | 2 | L7 Exception type for insufficient stock changed from ValueError to LookupError, altering the |
| v2-tools | P5 | b | clean | True | True | False | False | 2 | L7 Insufficient-stock condition now raises LookupError instead of ValueError, breaking caller |
