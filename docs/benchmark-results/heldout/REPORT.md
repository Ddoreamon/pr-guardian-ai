# Benchmark results

Ground truth is applied only by the evaluator. Methods never see the test file
unless explicitly marked `+tests`. `n/a` = undefined (zero denominator) or not run.

## PR-level bug detection

| Method | Split | Status | Cases (bug/clean) | TP | FP | FN | Precision | Recall | F1 | Clean false-alarm | Mean latency (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ruff-only | heldout | ok | 17 (8/9) | 0 | 0 | 8 | n/a | 0.00% | n/a | 0.00% | 0.459 |

## Finding-level issue matching

| Method | Split | Finding TP | Finding FP | Finding precision | Bug location accuracy |
|---|---|---|---|---|---|
| ruff-only | heldout | 0 | 0 | n/a | 0.00% |

## Failure cases

- `ruff-only` `DISCOUNT-04` (boundary, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `USERS-04` (null_handling, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `CART-03` (data_filter, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `INVENTORY-03` (api_validation, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `PAGINATION-03` (api_validation, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `COUPON-03` (boundary, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `PARSING-03` (boundary, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `RETRY-03` (exception_handling, heldout): has_bug=True, flagged=False, status=ok 
