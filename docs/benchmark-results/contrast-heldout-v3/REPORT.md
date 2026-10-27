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
| v1-diff-only | heldout | 12 (6/6) | 4 | 0 | 2 | 1 | 5 | 7 | 71.43% | 100.00% | 66.67% | 80.00% | 0.00% | 44.44% | 83.33% | 66.67% | 71.43% |
| v2-tools | heldout | 12 (6/6) | 5 | 0 | 1 | 2 | 4 | 8 | 87.50% | 100.00% | 83.33% | 90.91% | 0.00% | 55.56% | 66.67% | 83.33% | 87.50% |

## Pair consistency (both variants correct)

| Method | Split | Pairs | Pairs decided | Consistent | Pair consistency | Semantic consistent | Semantic pair consistency | Binomial p (null p0 = 0.25) |
|---|---|---|---|---|---|---|---|---|
| v1-diff-only | heldout | 6 | 1 | 0 | 0.00% | 0 | 0.00% | 1.0 |
| v2-tools | heldout | 6 | 2 | 1 | 50.00% | 1 | 50.00% | 0.4375 |

## Tool use and cost

| Method | Split | Model requests (failed) | Tool calls | Hits | Misses | Errors | Budget denied | Effective hit rate | Prompt tokens | Completion tokens | Mean latency (s) | Cost (USD) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v1-diff-only | heldout | 12 (0) | 0 | 0 | 0 | 0 | 0 | n/a | 4580 | 13063 | 6.945 | not computed |
| v2-tools | heldout | 32 (0) | 41 | 28 | 3 | 5 | 5 | 68.29% | 45226 | 11300 | 6.526 | not computed |

## Per-variant detail

| Method | Pair | Var | Label | Flagged | Located | Semantic (keyword) | Correct | Tools | Findings |
|---|---|---|---|---|---|---|---|---|---|
| v1-diff-only | P6 | a | bug | True | True | True | True | 0 | L4 Changing `active`'s default limit from `None` to `0` alters no-argument calls from unlimit |
| v1-diff-only | P6 | b | clean | True | True | True | False | 0 | L4 Changing `limit`'s default from `None` to `0` changes the no-argument API behavior and lik |
| v1-diff-only | P7 | a | bug | False | False | False | False | 0 |  |
| v1-diff-only | P7 | b | clean | False | False | False | True | 0 |  |
| v1-diff-only | P8 | a | bug | True | True | True | True | 0 | L7 `billable()` now rounds fractional quantities down, undercounting billable units |
| v1-diff-only | P8 | b | clean | True | True | True | False | 0 | L7 billable() now rounds fractional quantities down instead of up |
| v1-diff-only | P9 | a | bug | True | True | True | True | 0 | L6 Missing required `rate` now silently defaults to 0.0 instead of raising a configuration er |
| v1-diff-only | P9 | b | clean | True | True | True | False | 0 | L6 Missing `rate` now silently defaults to 0.0 instead of raising. |
| v1-diff-only | P10 | a | bug | False | False | False | False | 0 |  |
| v1-diff-only | P10 | b | clean | True | True | True | False | 0 | L6 The HTTPS-only check now rejects valid HTTP URLs that were previously fetchable. |
| v1-diff-only | P11 | a | bug | True | True | True | True | 0 | L6 Empty-list guard removed; only None is now rejected, so an empty list falls through to the |
| v1-diff-only | P11 | b | clean | True | True | True | False | 0 | L6 Guard changed from empty-list check to None check, so average([]) now divides by zero |
| v2-tools | P6 | a | bug | True | True | True | True | 4 | L4 Changing the default of `limit` from `None` to `0` makes `active()` return an empty list b |
| v2-tools | P6 | b | clean | True | True | True | False | 2 | L4 Changing the default of `limit` from `None` to `0` makes `active()` with no arguments retu |
| v2-tools | P7 | a | bug | True | True | True | True | 3 | L7 fetch() now passes retries=1, but get() only succeeds on its second attempt, so the first  |
| v2-tools | P7 | b | clean | False | False | False | True | 3 |  |
| v2-tools | P8 | a | bug | True | True | True | True | 4 | L6 billable() now rounds quantities down, so fractional quantities are under-billed instead o |
| v2-tools | P8 | b | clean | True | True | True | False | 2 | L5 billable() now rounds down, so fractional quantities are billed as zero/partial units, inv |
| v2-tools | P9 | a | bug | True | True | True | True | 5 | L6 rate_for now silently defaults a missing "rate" key to 0.0, contradicting the documented c |
| v2-tools | P9 | b | clean | True | True | True | False | 6 | L6 Missing 'rate' key now silently yields 0.0 instead of erroring, masking broken configurati |
| v2-tools | P10 | a | bug | False | False | False | False | 2 |  |
| v2-tools | P10 | b | clean | False | False | False | True | 6 |  |
| v2-tools | P11 | a | bug | True | True | True | True | 2 | L6 Empty-list guard was removed, so `average([])` now raises ZeroDivisionError instead of ret / L4 The new None branch contradicts the `items: list[float]` type contract |
| v2-tools | P11 | b | clean | True | True | True | False | 2 | L6 Empty list now raises ZeroDivisionError instead of returning 0.0 |
