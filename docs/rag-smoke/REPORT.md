# DeepSeek -> search_code -> read_code_context -> Finding (dev case)

Not the frozen benchmark. The bug is a unit mismatch that can only be judged
by reading a callee in another file.

* gold: `apply_credit` in `src/shop/invoices.py`

## Local vs MCP parity (same snapshot, same query)

* query: `credit invoice units`, top_k 5
* payloads identical: True
* ref: `9a79ddc5d541050601b3a64449bfeea6cc4e65e2`
* gold in top-k: True
* top files: ['src/shop/invoices.py', 'src/shop/checkout.py', 'src/shop/invoices.py', 'src/shop/invoices.py', 'src/shop/invoices.py']
* latency: local 0.8109s, mcp 0.769s (rpc 0.7689s)

## Runs

### bug

* branch `dev/credit-cents` at `9a79ddc5d541050601b3a64449bfeea6cc4e65e2`
* llm_status `partial`, tool_status `tool_limit`
* model calls 4 (failures 0), rounds 3
* tokens 10470 / 1031, wall 9.463s
* used search_code: True (1 calls); search hit the gold file: False
* session closed: True; leaked threads: []

| round | tool_call_id | tool | arguments | outcome | matched |
|---|---|---|---|---|---|
| 1 | `call_00_ET_SpBF3GHVQjUzlYHCvIc38044` | read_code_context | `{"file_path": "src/shop/checkout.py", "start_line": 1, "end_line": 40}` | hit | 0 |
| 1 | `call_01_ET_6ll76AflJsIoYEm476i37696` | find_symbol_definition | `{"symbol": "apply_credit"}` | hit | 1 |
| 2 | `call_00_fI9XuQxb2GVJCksHO8Jf4081` | read_code_context | `{"file_path": "src/shop/invoices.py", "start_line": 1, "end_line": 40}` | hit | 0 |
| 2 | `call_01_LL3qToYA6gSMRUvusNU46199` | search_code | `{"query": "order[\"cents\"] order[\"amount\"] finalise", "top_k": 10}` | hit | 10 |
| 3 | `` | read_code_context | `{"file_path": "src/shop/orders.py", "start_line": 1, "end_line": 25}` | budget_denied | 0 |
| 3 | `` | search_code | `{"query": "cents key order dictionary", "top_k": 10}` | budget_denied | 0 |

| tool result | ref | count | first files |
|---|---|---|---|
| read_code_context | `9a79ddc5d541` | None | - |
| find_symbol_definition | `9a79ddc5d541` | 1 | src/shop/invoices.py |
| read_code_context | `9a79ddc5d541` | None | - |
| search_code | `9a79ddc5d541` | 10 | src/shop/checkout.py, src/shop/orders.py, src/shop/money.py, src/shop/orders.py, src/shop/pricing.py |

| finding | file:line | status | severity |
|---|---|---|---|
| finalise now passes a minor-unit (cents) value to apply_credit, which documents its amount | src/shop/checkout.py:8 | suspected | high |

### clean

* branch `dev/credit-units` at `b0d5a48d45467edbf73ca004a1516b4cf55be4b3`
* llm_status `partial`, tool_status `tool_limit`
* model calls 4 (failures 0), rounds 3
* tokens 8411 / 1417, wall 11.788s
* used search_code: True (1 calls); search hit the gold file: True
* session closed: True; leaked threads: []

| round | tool_call_id | tool | arguments | outcome | matched |
|---|---|---|---|---|---|
| 1 | `call_00_ET_INwBMmN1AD3VF9vpeiMH9086` | read_code_context | `{"file_path": "src/shop/checkout.py", "start_line": 1, "end_line": 30}` | hit | 0 |
| 1 | `call_01_ET_xsG3ddknxpv603ozoUAt9878` | find_symbol_definition | `{"symbol": "apply_credit"}` | hit | 1 |
| 2 | `call_00_BLd2wj4BRa6tsmRYFSRv5960` | read_code_context | `{"file_path": "src/shop/invoices.py", "start_line": 1, "end_line": 40}` | hit | 0 |
| 2 | `call_01_KPpDk3cU12bhg5oa5abd7645` | search_code | `{"query": "apply_credit note", "top_k": 10}` | hit | 3 |
| 3 | `` | search_code | `{"query": "finalise order checkout", "top_k": 10}` | budget_denied | 0 |
| 3 | `` | search_code | `{"query": "note", "top_k": 10}` | budget_denied | 0 |

| tool result | ref | count | first files |
|---|---|---|---|
| read_code_context | `b0d5a48d4546` | None | - |
| find_symbol_definition | `b0d5a48d4546` | 1 | src/shop/invoices.py |
| read_code_context | `b0d5a48d4546` | None | - |
| search_code | `b0d5a48d4546` | 3 | src/shop/invoices.py, src/shop/checkout.py, src/shop/pricing.py |

| finding | file:line | status | severity |
|---|---|---|---|
| Passing note="web" to apply_credit has no effect: the callee ignores its note parameter, s | src/shop/checkout.py:8 | suspected | low |

