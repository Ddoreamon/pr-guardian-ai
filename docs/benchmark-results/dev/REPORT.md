# Benchmark results

Ground truth is applied only by the evaluator. Methods never see the test file
unless explicitly marked `+tests`. `n/a` = undefined (zero denominator) or not run.

## PR-level bug detection

| Method | Split | Status | Cases (bug/clean) | TP | FP | FN | Precision | Recall | F1 | Clean false-alarm | Mean latency (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ruff-only | dev | ok | 20 (17/3) | 0 | 0 | 17 | n/a | 0.00% | n/a | 0.00% | 0.465 |

## Finding-level issue matching

| Method | Split | Finding TP | Finding FP | Finding precision | Bug location accuracy |
|---|---|---|---|---|---|
| ruff-only | dev | 0 | 0 | n/a | 0.00% |

## Failure cases

- `ruff-only` `DISCOUNT-01` (boundary, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `DISCOUNT-02` (boundary, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `USERS-01` (null_handling, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `USERS-02` (null_handling, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `CART-02` (logic_branch, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `INVENTORY-01` (state_update, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `INVENTORY-02` (exception_handling, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `PAGINATION-01` (api_validation, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `PAGINATION-02` (logic_branch, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `COUPON-01` (boundary, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `COUPON-02` (state_update, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `PARSING-01` (exception_handling, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `PARSING-02` (boundary, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `RETRY-01` (exception_handling, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `RETRY-02` (boundary, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `RANKING-01` (logic_branch, dev): has_bug=True, flagged=False, status=ok 
- `ruff-only` `RANKING-02` (logic_branch, dev): has_bug=True, flagged=False, status=ok 
