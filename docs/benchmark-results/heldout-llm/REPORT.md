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
| llm-only | heldout | ok | 17 (8/9) | 7 | 1 | 1 | 87.50% | 87.50% | 87.50% | 11.11% | 2.997 |
| combined | heldout | ok | 17 (8/9) | 8 | 1 | 0 | 88.89% | 100.00% | 94.12% | 11.11% | 3.397 |

## Finding-level issue matching

| Method | Split | Finding TP | Finding FP | Finding precision | Bug location accuracy |
|---|---|---|---|---|---|
| llm-only | heldout | 7 | 1 | 87.50% | 87.50% |
| combined | heldout | 8 | 1 | 88.89% | 100.00% |

## Model calls and tokens

Provider-reported usage only. Cost is not inferred without a price table.

| Method | Split | LLM calls | Schema-valid rate | Validator dropped | Prompt tokens | Completion tokens | Total tokens | LLM latency (s, mean) |
|---|---|---|---|---|---|---|---|---|
| llm-only | heldout | 17 | 100.00% | 0 | 5423 | 6916 | 12339 | 2.660 |
| combined | heldout | 17 | 100.00% | 0 | 5423 | 8405 | 13828 | 2.986 |

## Per-case detail

| Method | Case | Split | Category | Has bug | Flagged | TP | FP | Raw | Dropped | Findings |
|---|---|---|---|---|---|---|---|---|---|---|
| llm-only | DISCOUNT-04 | heldout | boundary | True | False | 0 | 0 | 0 | 0 |  |
| llm-only | DISCOUNT-05 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| llm-only | USERS-04 | heldout | null_handling | True | True | 1 | 0 | 1 | 0 | L6 Inverted None-guard breaks the None path of find_email |
| llm-only | CART-03 | heldout | data_filter | True | True | 1 | 0 | 1 | 0 | L9 Negative quantities are no longer skipped, allowing invalid cart items to affect totals. |
| llm-only | CART-04 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| llm-only | INVENTORY-03 | heldout | api_validation | True | True | 1 | 0 | 1 | 0 | L10 Zero-quantity reservations are now accepted because the guard changed from `qty <= 0` to `qty < 0`. |
| llm-only | INVENTORY-04 | heldout | clean | False | True | 0 | 1 | 1 | 0 | L12 Availability check and reservation now perform two separate stock.get reads instead of reusing a single value |
| llm-only | PAGINATION-03 | heldout | api_validation | True | True | 1 | 0 | 1 | 0 | L6 Removed validation for non-positive `size`, allowing invalid page sizes through |
| llm-only | PAGINATION-04 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| llm-only | COUPON-03 | heldout | boundary | True | True | 1 | 0 | 1 | 0 | L9 Expiration comparison is inverted, so expired coupons are treated as valid |
| llm-only | COUPON-04 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| llm-only | PARSING-03 | heldout | boundary | True | True | 1 | 0 | 1 | 0 | L10 parse_port now accepts port 0 as valid |
| llm-only | PARSING-04 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| llm-only | RETRY-03 | heldout | exception_handling | True | True | 1 | 0 | 1 | 0 | L9 Retry loop hard-coded to a single iteration, ignoring the attempts parameter |
| llm-only | RETRY-04 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| llm-only | RANKING-03 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| llm-only | RANKING-04 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| combined | DISCOUNT-04 | heldout | boundary | True | True | 1 | 0 | 1 | 0 | L6 Discount threshold changed to >= 101 on a float value, excluding totals between 100 and 101 |
| combined | DISCOUNT-05 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| combined | USERS-04 | heldout | null_handling | True | True | 1 | 0 | 1 | 0 | L6 The None guard is inverted, so a missing user no longer takes the None-handling path. |
| combined | CART-03 | heldout | data_filter | True | True | 1 | 0 | 1 | 0 | L9 Negative quantities are no longer filtered out in cart_total. |
| combined | CART-04 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| combined | INVENTORY-03 | heldout | api_validation | True | True | 1 | 0 | 1 | 0 | L10 Zero-quantity reservations are now accepted |
| combined | INVENTORY-04 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| combined | PAGINATION-03 | heldout | api_validation | True | True | 1 | 0 | 1 | 0 | L6 Page size validation was removed, allowing non-positive size values into pagination calculations. |
| combined | PAGINATION-04 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| combined | COUPON-03 | heldout | boundary | True | True | 1 | 0 | 1 | 0 | L9 Expiry comparison inverted: coupon_valid now accepts expired coupons and rejects still-valid ones. |
| combined | COUPON-04 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| combined | PARSING-03 | heldout | boundary | True | True | 1 | 0 | 1 | 0 | L10 Port 0 is now accepted as valid, though it is not a usable port |
| combined | PARSING-04 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| combined | RETRY-03 | heldout | exception_handling | True | True | 1 | 0 | 1 | 0 | L9 Retry loop hardcoded to a single iteration, ignoring the `attempts` parameter |
| combined | RETRY-04 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |
| combined | RANKING-03 | heldout | clean | False | True | 0 | 1 | 1 | 0 | L6 Zero request (n == 0) no longer hits the early-return guard in top_n |
| combined | RANKING-04 | heldout | clean | False | False | 0 | 0 | 0 | 0 |  |

## Failure cases

- `llm-only` `DISCOUNT-04` (boundary, heldout): has_bug=True, flagged=False, status=ok 
- `llm-only` `INVENTORY-04` (clean, heldout): has_bug=False, flagged=True, status=ok 
- `combined` `RANKING-03` (clean, heldout): has_bug=False, flagged=True, status=ok 
