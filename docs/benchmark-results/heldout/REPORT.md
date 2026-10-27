# Benchmark results

Ground truth is applied only by the evaluator. Methods never see the test file
unless explicitly marked `+tests`. `n/a` = undefined (zero denominator) or not run.

Method definitions:

* `ruff-only` — Ruff static analysis only.
* `llm-only` — raw model output, validator NOT applied (measures the model alone).
* `combined` — Ruff + model + validator (what the shipped pipeline reports).
* `combined+tests` — combined plus pytest evidence; reported separately because
  the test file is visible to the method in that condition.

## PR-level bug detection

| Method | Split | Status | Cases (bug/clean) | TP | FP | FN | Precision | Recall | F1 | Clean false-alarm | Mean latency (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ruff-only | heldout | ok | 17 (8/9) | 0 | 0 | 8 | n/a | 0.00% | n/a | 0.00% | 0.470 |

## Finding-level issue matching

| Method | Split | Finding TP | Finding FP | Finding precision | Bug location accuracy |
|---|---|---|---|---|---|
| ruff-only | heldout | 0 | 0 | n/a | 0.00% |

## Model calls and tokens

Provider-reported usage only. Cost is not inferred without a price table.

| Method | Split | LLM calls | Schema-valid rate | Prompt tokens | Completion tokens | Total tokens | LLM latency (s, mean) |
|---|---|---|---|---|---|---|---|
| ruff-only | heldout | 0 | n/a | 0 | 0 | 0 | n/a |

## Failure cases

- `ruff-only` `DISCOUNT-04` (boundary, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `USERS-04` (null_handling, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `CART-03` (data_filter, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `INVENTORY-03` (api_validation, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `PAGINATION-03` (api_validation, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `COUPON-03` (boundary, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `PARSING-03` (boundary, heldout): has_bug=True, flagged=False, status=ok 
- `ruff-only` `RETRY-03` (exception_handling, heldout): has_bug=True, flagged=False, status=ok 
