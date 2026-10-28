# PR Guardian review

- **Case:** Boundary defect in discount rule
- **Repository:** demo-repo
- **Base / head:** `main` → `feature/boundary-bug`
- **Review id:** `b8fa63b3ddf1`
- **Created:** 2026-10-28T07:25:00.380235+00:00
- **LLM status:** not_configured (LLM step skipped: PR_GUARDIAN_API_KEY is not set.)

## Test evidence

- pytest: 1 passed, 1 failed, 0 errors (0.61s)
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


## Notes

- pytest on head: 1 passed, 1 failed, 0 errors, timed_out=False
- LLM step skipped: PR_GUARDIAN_API_KEY is not set.
