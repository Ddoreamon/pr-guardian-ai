# Cross-file benchmark: V1 (no tools) vs V2 (read-only tools)

Each case changes ONE file; the contract that makes it wrong lives in another
file, so it is only decidable by reading the repository.
Matching rule is identical to the main benchmark and was frozen before measuring.
Methods never see the hidden ground-truth tests.

| Method | Split | Cases (bug/clean) | TP | FP | FN | Precision | Recall | F1 | Clean false-alarm | Location accuracy |
|---|---|---|---|---|---|---|---|---|---|---|
| v1-no-tools | dev | 4 (3/1) | 2 | 0 | 1 | 100.00% | 66.67% | 80.00% | 0.00% | 66.67% |
| v2-tools | dev | 4 (3/1) | 2 | 0 | 1 | 100.00% | 66.67% | 80.00% | 0.00% | 66.67% |

## Tool usage and cost

| Method | Split | Tool calls | Succeeded | Tool success rate | Schema-valid | Prompt tokens | Completion tokens | Mean latency (s) |
|---|---|---|---|---|---|---|---|---|
| v1-no-tools | dev | 0 | 0 | n/a | 100.00% | 1285 | 982 | 2.076 |
| v2-tools | dev | 13 | 13 | 100.00% | 100.00% | 16714 | 2545 | 5.646 |

## Per-case detail

| Method | Case | Split | Bug? | Flagged | TP | FP | Tools | Tool errors | Findings |
|---|---|---|---|---|---|---|---|---|---|
| v1-no-tools | XF-01 | dev | True | False | 0 | 0 | 0 | — |  |
| v1-no-tools | XF-02 | dev | True | True | 1 | 0 | 0 | — | L25 tax_for now computes tax using DISCOUNT_RATE instead of TAX_RATE |
| v1-no-tools | XF-03 | dev | True | True | 1 | 0 | 0 | — | L17 line_total no longer validates qty, allowing zero/negative/non-integer quantities |
| v1-no-tools | XF-04 | dev | False | False | 0 | 0 | 0 | — |  |
| v2-tools | XF-01 | dev | True | False | 0 | 0 | 4 | — |  |
| v2-tools | XF-02 | dev | True | True | 1 | 0 | 3 | — | L25 tax_for uses DISCOUNT_RATE instead of TAX_RATE, halving the sales tax returned |
| v2-tools | XF-03 | dev | True | True | 1 | 0 | 3 | — | L16 Removal of the quantity guard makes line_total silently accept zero, negative, float, or bool quanti |
| v2-tools | XF-04 | dev | False | False | 0 | 0 | 3 | — |  |
