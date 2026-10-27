# PR Guardian review

- **Case:** Boundary defect in discount rule
- **Repository:** demo-repo
- **Base / head:** `main` → `feature/boundary-bug`
- **Review id:** `fc9714204597`
- **Created:** 2026-10-27T11:35:53.976946+00:00
- **LLM status:** ok (LLM model: deepseek-flash)

## Test evidence

- pytest: 1 passed, 1 failed, 0 errors (0.69s)
- Failing tests: `tests/test_discount.py::test_threshold_order_gets_discount`

## Changed files

- `src/discount.py` (modified)

## Findings

### 1 test(s) failing on head
- **Location:** `(test suite):1`
- **Source:** pytest · **Status:** confirmed · **Severity:** high
- **Human decision:** ACCEPTED
- **Evidence:** pytest reported 1 failed, 0 errors
- **Explanation:** Failing tests: tests/test_discount.py::test_threshold_order_gets_discount
- **Suggested fix:** —

### Changing `>= 100` to `> 100` silently removes the discount for orders exactly at 100
- **Location:** `src/discount.py:6`
- **Source:** llm · **Status:** suspected · **Severity:** medium
- **Human decision:** —
- **Evidence:** if order_total > 100:  (previously: if order_total >= 100:)
- **Explanation:** The comparison operator was changed from inclusive to exclusive. As a result, an order_total of exactly 100.0 (and any value that compares equal to 100, including 100.0 arrived at via floating-point arithmetic) now falls into the no-discount branch, whereas it previously received the discount. This is an off-by-one boundary change whose correctness depends on the business rule for the threshold; if the requirement is 'orders of 100 or more qualify', this is a logic regression. Note that the type is float, so values like 99.99999999999999 will also behave differently at the edge.
- **Suggested fix:** If the threshold is meant to be inclusive, restore `if order_total >= 100:`. If excluding exactly 100 is intentional, document the threshold semantics and add a boundary test at 100.0 (and just below/above) to lock in the intended behavior.


## Notes

- pytest on head: 1 passed, 1 failed, 0 errors, timed_out=False
- LLM model: deepseek-flash
