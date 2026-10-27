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
| ruff-only | dev | ok | 20 (17/3) | 0 | 0 | 17 | n/a | 0.00% | n/a | 0.00% | 0.416 |

## Finding-level issue matching

| Method | Split | Finding TP | Finding FP | Finding precision | Bug location accuracy |
|---|---|---|---|---|---|
| ruff-only | dev | 0 | 0 | n/a | 0.00% |

## Model calls and tokens

Provider-reported usage only. Cost is not inferred without a price table.

| Method | Split | LLM calls | Schema-valid rate | Prompt tokens | Completion tokens | Total tokens | LLM latency (s, mean) |
|---|---|---|---|---|---|---|---|
| ruff-only | dev | 0 | n/a | 0 | 0 | 0 | n/a |

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
