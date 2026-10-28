# V4.5 robustness — where dependency analysis can fail

Separate from the V4.5 held-out set. Profiles deliver whole files (or nothing)
through `TrustedCodebase`; no model call, no gold path in the derivation.

## Summary

| metric | value |
|---|---|
| cases / profiles | 10 / 16 |
| status accuracy | 100.00% |
| **false sufficient** | **0** [] |
| false insufficient | 0 [] |
| unknown rate | 12.50% |
| obligation kind precision / recall | 0.6154 / 1.0 |
| derivation / assessment cost | 356.0 ms / 0.1 ms |

## Per profile

| case | profile | delivered | status | expected | kinds | required | missing | FS | FI |
|---|---|---|---|---|---|---|---|---|---|
| R1-delete-line | changed-file | src/fmt.py | sufficient | sufficient | modified_definition | 1 | 0 |  |  |
| R1-delete-line | nothing | — | insufficient | insufficient | modified_definition | 1 | 1 |  |  |
| R2-delete-function | changed-file | src/fmt.py | insufficient | insufficient | caller_usage | 1 | 1 |  |  |
| R2-delete-function | plus-consumer | src/fmt.py, src/report.py | sufficient | sufficient | caller_usage | 1 | 0 |  |  |
| R3-delete-file | importer | src/uses_other.py | sufficient | sufficient | caller_usage | 1 | 0 |  |  |
| R3-delete-file | nothing | — | insufficient | insufficient | caller_usage | 1 | 1 |  |  |
| R4-alias | changed-file | src/fmt.py | insufficient | insufficient | caller_usage, modified_definition | 3 | 2 |  |  |
| R4-alias | plus-callers | src/fmt.py, src/report.py, src/alias_report.py | sufficient | sufficient | caller_usage, modified_definition | 3 | 0 |  |  |
| R5-same-name | changed-file | src/fmt.py | insufficient | insufficient | caller_usage, modified_definition | 3 | 2 |  |  |
| R5-same-name | plus-callers | src/fmt.py, src/report.py, src/alias_report.py | sufficient | sufficient | caller_usage, modified_definition | 3 | 0 |  |  |
| R6-method | changed-file | src/cart.py | sufficient | sufficient | caller_usage, modified_definition | 5 | 0 |  |  |
| R7-indirect-raise | changed-file | src/parser.py | unknown | unknown | caller_usage, modified_definition, unknown_dependency | 2 | 0 |  |  |
| R8-magnitude | changed-file | src/cart.py | sufficient | sufficient | modified_definition | 2 | 0 |  |  |
| R9-default | changed-file | src/fmt.py | insufficient | insufficient | caller_usage, modified_definition | 2 | 1 |  |  |
| R9-default | plus-callers | src/fmt.py, src/report.py, src/alias_report.py | sufficient | sufficient | caller_usage, modified_definition | 2 | 0 |  |  |
| R10-registry | changed-file | src/registry.py | unknown | unknown | modified_definition, unknown_dependency | 1 | 0 |  |  |

## Non-sufficient explanations

* **R1-delete-line/nothing** — required evidence was not delivered for: ob-e2d42ff93e
* **R2-delete-function/changed-file** — required evidence was not delivered for: ob-31e347bc44
* **R3-delete-file/nothing** — required evidence was not delivered for: ob-f58d55adc5
* **R4-alias/changed-file** — required evidence was not delivered for: ob-ae824bd3cf, ob-8122ce7f87
* **R5-same-name/changed-file** — required evidence was not delivered for: ob-ae824bd3cf, ob-8122ce7f87
* **R7-indirect-raise/changed-file** — required evidence is covered, but dynamic constructs mean the obligation set cannot be proven complete: ob-078e38c46f
* **R9-default/changed-file** — required evidence was not delivered for: ob-74dde708f6
* **R10-registry/changed-file** — required evidence is covered, but dynamic constructs mean the obligation set cannot be proven complete: ob-65cc18af5f
