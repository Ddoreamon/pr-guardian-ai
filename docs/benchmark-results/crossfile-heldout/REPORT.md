# Cross-file benchmark: V1 (no tools) vs V2 (read-only tools)

Each case changes ONE file; the contract that makes it wrong lives in another
file, so it is only decidable by reading the repository.
Matching rule is identical to the main benchmark and was frozen before measuring.
Methods never see the hidden ground-truth tests.

| Method | Split | Cases (bug/clean) | TP | FP | FN | Precision | Recall | F1 | Clean false-alarm | Location accuracy |
|---|---|---|---|---|---|---|---|---|---|---|
| v1-no-tools | heldout | 6 (4/2) | 4 | 0 | 0 | 100.00% | 100.00% | 100.00% | 0.00% | 100.00% |
| v2-tools | heldout | 6 (4/2) | 4 | 0 | 0 | 100.00% | 100.00% | 100.00% | 0.00% | 100.00% |

## Tool usage and cost

| Method | Split | Tool calls | Succeeded | Tool success rate | Schema-valid | Prompt tokens | Completion tokens | Mean latency (s) |
|---|---|---|---|---|---|---|---|---|
| v1-no-tools | heldout | 0 | 0 | n/a | 100.00% | 1864 | 2770 | 3.411 |
| v2-tools | heldout | 20 | 17 | 85.00% | 100.00% | 21173 | 3825 | 5.132 |

## Per-case detail

| Method | Case | Split | Bug? | Flagged | TP | FP | Tools | Tool errors | Findings |
|---|---|---|---|---|---|---|---|---|---|
| v1-no-tools | XF-05 | heldout | True | True | 1 | 0 | 0 | — | L5 TAX_RATE reduced by 10x from 0.20 to 0.02, likely a decimal typo |
| v1-no-tools | XF-06 | heldout | True | True | 1 | 0 | 0 | — | L11 Discount threshold comparison changed from inclusive to exclusive |
| v1-no-tools | XF-07 | heldout | True | True | 1 | 0 | 0 | — | L7 Discount calculation uses total divided by 100 instead of the original total |
| v1-no-tools | XF-08 | heldout | True | True | 1 | 0 | 0 | — | L6 is_valid_quantity now accepts zero, changing the lower bound of valid quantities |
| v1-no-tools | XF-09 | heldout | False | False | 0 | 0 | 0 | — |  |
| v1-no-tools | XF-10 | heldout | False | False | 0 | 0 | 0 | — |  |
| v2-tools | XF-05 | heldout | True | True | 1 | 0 | 6 | read_code_context:not_found; read_code_context:budget_exceeded; read_code_context:budget_exceeded | L5 TAX_RATE reduced tenfold from 0.20 to 0.02, changing all tax computations globally |
| v2-tools | XF-06 | heldout | True | True | 1 | 0 | 4 | — | L11 Changing `>=` to `>` excludes orders exactly at DISCOUNT_THRESHOLD from receiving the discount, cont |
| v2-tools | XF-07 | heldout | True | True | 1 | 0 | 4 | — | L7 Order total is divided by 100 before being passed to discount_for, computing the discount on the wro |
| v2-tools | XF-08 | heldout | True | True | 1 | 0 | 2 | — | L5 The relaxation from `qty > 0` to `qty >= 0` now accepts zero, which contradicts the function's docum |
| v2-tools | XF-09 | heldout | False | False | 0 | 0 | 2 | — |  |
| v2-tools | XF-10 | heldout | False | False | 0 | 0 | 2 | — |  |
