# V1.1 real-world acceptance

Three self-authored projects (MIT), uploaded through the real HTTP API; case A
also through the real browser page. Nothing was mocked, no model was called.

## Summary

| case | files | LOC | findings | ruff | heuristic | status | elapsed |
|---|---|---|---|---|---|---|---|
| A_csv_report | 1 | 35 | 4 | 3 | 1 | completed | 0.22s |
| B_fastapi_service | 1 | 36 | 5 | 4 | 1 | completed | 0.2s |
| C_inventory_pkg | 4 | 55 | 5 | 3 | 2 | completed | 0.24s |

## Human review per case

### A_csv_report — CSV → JSON sales summary (single file)

Upload mode: `files`; license: self-authored, MIT; analysis: AST `ok`, Ruff `ok`, worker `ok`, AI `not_run`.

| rule | file | line | detected | verdict | actionable | note |
|---|---|---|---|---|---|---|
| F401 | csv_report.py | 6 | yes | valid | yes | unused OrderedDict import |
| F541 | csv_report.py | 28 | yes | valid | yes | f-string without placeholders |
| UP006 | csv_report.py | 9 | yes | valid | yes | mutable default argument |
| B006 | csv_report.py | 9 | yes | duplicate | yes | same defect as UP006, different rule |

Problems the checks did **not** find:

- next(reader) skips the first data row: DictReader already consumed the header
- float() on a malformed amount raises with no handler

After the fix: disappeared = F401, F541, UP006, B006; still present = none; review status = `completed`.

### B_fastapi_service — FastAPI inventory service (single file)

Upload mode: `files`; license: self-authored, MIT; analysis: AST `ok`, Ruff `ok`, worker `ok`, AI `not_run`.

| rule | file | line | detected | verdict | actionable | note |
|---|---|---|---|---|---|---|
| F401 | api.py | 3 | yes | valid | yes | unused os import |
| UP005 | api.py | 26 | yes | valid | yes | bare except hides real errors |
| I001 | api.py | 3 | yes | valid | yes | import order (style, low value) |
| E722 | api.py | 26 | yes | duplicate | yes | Ruff's own bare-except rule; same defect as UP005 |
| BLE001 | api.py | 35 | yes | valid | yes | delete handler catches every exception and leaks its text |

Problems the checks did **not** find:

- quantity accepts negative values; no validation
- delete handler returns the exception text to the client
- path parameter is untyped, so every name is a string id

After the fix: disappeared = F401, UP005; still present = none; review status = `completed`.

### C_inventory_pkg — Inventory package (multi-file, ZIP)

Upload mode: `zip`; license: self-authored, MIT; analysis: AST `ok`, Ruff `ok`, worker `ok`, AI `not_run`.

| rule | file | line | detected | verdict | actionable | note |
|---|---|---|---|---|---|---|
| UP002 | inventory/service.py | 20 | yes | valid | yes | shell=True with a caller-controlled path |
| UP006 | inventory/models.py | 12 | yes | valid | yes | mutable default accumulates across calls |
| B006 | inventory/models.py | 12 | yes | duplicate | yes | Ruff's version of UP006, same defect |
| RUF100 | inventory/service.py | 5 | yes | false_positive | no | flags the deliberate noqa; an artefact of isolated mode |
| PLW1510 | inventory/service.py | 20 | yes | valid | yes | subprocess.run without check=True silences failures |

Problems the checks did **not** find:

- cli ignores the report subprocess return code and exit status
- discount_all reuses the shared out list between calls (same root cause)

After the fix: disappeared = UP002, UP006; still present = none; review status = `completed`.

## Metrics (denominator: every finding the checks returned)

| case | findings | valid | duplicate | false positive | distinct valid |
|---|---|---|---|---|---|
| A_csv_report | 4 | 3 | 1 | 0 | 3 |
| B_fastapi_service | 5 | 4 | 1 | 0 | 4 |
| C_inventory_pkg | 5 | 3 | 1 | 1 | 3 |

## Security

- temp analyser dirs left behind: **0**
- source fragments found in the server log: **0** (log checked: True)

## Public instance

- reachable: True
- `/api/uploads/config` status: 404
- `/upload` status: 404
- uploads exposed: **False**

## Browser flow (case A)

- rendered findings: 4
- status line: `Analysis complete: 1 file(s), 4 finding(s). Tests were not run: uploaded code is never executed. (AST ok · Ruff ok · worker ok · AI not_run)`

