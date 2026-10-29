# V4.1 evidence chain — real DeepSeek run

Development case, not the frozen benchmark. The question is provenance:
does every citation point at code the model actually received?

## bug (mcp)

* branch `dev/credit-cents` at `e75690a175edd068b3003cf9e380343da4a8b0e4`
* model `deepseek-flash`, llm_status `partial`, tool_status `tool_limit`
* model calls 5 (failures 0), tokens 19687/2153, wall 13.298s
* evidence: 1 verified, 0 unverified, registry `591386bff1582abf`

| round | tool_call_id | tool | outcome |
|---|---|---|---|
| 1 | `call_00_ET_R8YhtufvwH1ri4ztTnDC3034` | read_code_context | hit |
| 1 | `call_01_ET_96TgYlLUYtFXf1DafrNg1763` | find_symbol_definition | hit |
| 2 | `call_00_3ceL2eWoPaJAkzYb0DJH1796` | read_code_context | hit |
| 2 | `call_01_LMRQmaUdBcqnRdAqhhqo4188` | search_code | hit |
| 3 | `call_00_KWRfo4fOS3bGZNQ9ntrM5424` | read_code_context | hit |
| 3 | `call_01_djShJnlGyMxN2pGIFRRO2934` | search_code | hit |
| 4 | `call_00_v1xx95VH3lK9lkFMxisf3592` | read_code_context | budget_denied |
| 4 | `call_01_v58bNTFyTfk3cPzabots1845` | search_code | budget_denied |

| finding | evidence_status | claim_status | citations |
|---|---|---|---|
| finalise() now passes a minor-unit (cents) value into apply_credit, wh | source_verified | claim_unjudged | `src/shop/checkout.py:6-9` (source_verified, call_00_ET_R8YhtufvwH1ri4ztTnDC3034), `src/shop/invoices.py:23-26` (source_verified, call_01_ET_96TgYlLUYtFXf1DafrNg1763), `src/shop/orders.py:6-8` (source_verified, call_01_LMRQmaUdBcqnRdAqhhqo4188) |

## clean (mcp)

* branch `dev/credit-units` at `3ca5e0cb0088b37824c38bf6de71f8f9b503e8a3`
* model `deepseek-flash`, llm_status `partial`, tool_status `tool_limit`
* model calls 5 (failures 0), tokens 15867/1680, wall 11.37s
* evidence: 1 verified, 0 unverified, registry `5bbe43b1c521e643`

| round | tool_call_id | tool | outcome |
|---|---|---|---|
| 1 | `call_00_ET_yFwwLLaMEnNsmc86XbOe3744` | read_code_context | hit |
| 1 | `call_01_ET_NCGKKcuISyhmpX0Ir1he0551` | find_symbol_definition | hit |
| 2 | `call_00_uwpmEq6VdNC6FNJkzUrN7191` | read_code_context | error |
| 2 | `call_01_bonHOGHd0EabZxMowSqU8378` | search_code | hit |
| 3 | `call_00_88fM0wbPy9s6rKjsh8gy2795` | read_code_context | hit |
| 4 | `call_00_wp41yVsW9XOZJ83Gnyrz4271` | search_code | hit |
| 4 | `call_01_vrxkdU7XLfMFpEcG8Soo2870` | search_code | budget_denied |

| finding | evidence_status | claim_status | citations |
|---|---|---|---|
| The newly passed note="web" argument has no effect: apply_credit accep | source_verified | claim_unjudged | `src/shop/checkout.py:6-9` (source_verified, call_00_ET_yFwwLLaMEnNsmc86XbOe3744), `src/shop/invoices.py:23-26` (source_verified, call_01_ET_NCGKKcuISyhmpX0Ir1he0551) |

