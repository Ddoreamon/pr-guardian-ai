# DeepSeek -> search_code -> read_code_context -> Finding (dev case)

Not the frozen benchmark. The bug is a unit mismatch that can only be judged
by reading a callee in another file.

* gold: `apply_credit` in `src/shop/invoices.py`

## Local vs MCP parity (same snapshot, same query)

* query: `credit invoice units`, top_k 5
* payloads identical: True
* ref: `5c00507cd65fdb812e785d95f73a7a032dbdf6ab`
* gold in top-k: True
* top files: ['src/shop/invoices.py', 'src/shop/checkout.py', 'src/shop/invoices.py', 'src/shop/invoices.py', 'src/shop/invoices.py']
* latency: local 0.932s, mcp 0.9526s (rpc 0.9525s)

## Runs

### bug

* branch `dev/credit-cents` at `5c00507cd65fdb812e785d95f73a7a032dbdf6ab`
* llm_status `partial`, tool_status `tool_limit`
* model calls 4 (failures 0), rounds 3
* tokens 9455 / 1071, wall 9.823s
* used search_code: True (1 calls); search hit the gold file: False
* session closed: True; leaked threads: []

| round | tool_call_id | tool | arguments | outcome | matched |
|---|---|---|---|---|---|
| 1 | `call_00_H2iNYn7wjnb9hMoouz9I7907` | read_code_context | `{"file_path": "src/shop/checkout.py", "start_line": 1, "end_line": 40}` | hit | 0 |
| 1 | `call_01_s01AafohRMgsG7UJzKpb2955` | find_symbol_definition | `{"symbol": "apply_credit"}` | hit | 1 |
| 2 | `call_00_C0w6tE0wKO6xdxB6dYQh9894` | search_code | `{"query": "finalise(order)", "top_k": 5}` | hit | 5 |
| 2 | `call_01_d5YiCwzGT2DfSaaTH4Hk0900` | read_code_context | `{"file_path": "src/shop/invoices.py", "start_line": 1, "end_line": 40}` | hit | 0 |
| 3 | `call_00_Q2zrdoUGn2Z4Ab8X1UCw7597` | search_code | `{"query": "order[\\\"amount\\\"] order total cents", "top_k": 10}` | budget_denied | 0 |
| 3 | `call_01_HowEWwld1nt5sUBVCwhg1534` | read_code_context | `{"file_path": "src/shop/orders.py", "start_line": 1, "end_line": 25}` | budget_denied | 0 |

| tool result | ref | count | first files |
|---|---|---|---|
| read_code_context | `5c00507cd65f` | None | - |
| find_symbol_definition | `5c00507cd65f` | 1 | src/shop/invoices.py |
| search_code | `5c00507cd65f` | 5 | src/shop/checkout.py, src/shop/orders.py, src/shop/orders.py, src/shop/orders.py, src/shop/pricing.py |
| read_code_context | `5c00507cd65f` | None | - |

| finding | file:line | status | severity |
|---|---|---|---|
| finalise now passes a cents value to apply_credit, which expects whole currency units, so  | src/shop/checkout.py:8 | suspected | high |

### clean

* branch `dev/credit-units` at `3525f3ed29df223bb710b8a3aaf25b0f28327d6f`
* llm_status `partial`, tool_status `tool_limit`
* model calls 4 (failures 0), rounds 3
* tokens 8871 / 963, wall 9.776s
* used search_code: True (1 calls); search hit the gold file: True
* session closed: True; leaked threads: []

| round | tool_call_id | tool | arguments | outcome | matched |
|---|---|---|---|---|---|
| 1 | `call_00_ET_HLa9HzxPFShJjovkGWq72958` | read_code_context | `{"file_path": "src/shop/checkout.py", "start_line": 1, "end_line": 40}` | hit | 0 |
| 1 | `call_01_ET_OUV27nTx5MM2ssWHpKJ54205` | find_symbol_definition | `{"symbol": "apply_credit"}` | hit | 1 |
| 2 | `call_00_48Op136OodLDCOuBI7vv7558` | read_code_context | `{"file_path": "src/shop/invoices.py", "start_line": 1, "end_line": 40}` | hit | 0 |
| 2 | `call_01_28DSDnrgqvNIZVhe4YDp6484` | search_code | `{"query": "apply_credit note"}` | hit | 3 |
| 3 | `call_00_msTvBdPj5POtWAzpgZax4181` | search_code | `{"query": "note audit trail credit note", "top_k": 10}` | budget_denied | 0 |
| 3 | `call_01_dX5R7BFvMmMUq5bREqmT0414` | search_code | `{"query": "finalise order checkout", "top_k": 10}` | budget_denied | 0 |

| tool result | ref | count | first files |
|---|---|---|---|
| read_code_context | `3525f3ed29df` | None | - |
| find_symbol_definition | `3525f3ed29df` | 1 | src/shop/invoices.py |
| read_code_context | `3525f3ed29df` | None | - |
| search_code | `3525f3ed29df` | 3 | src/shop/invoices.py, src/shop/checkout.py, src/shop/pricing.py |

| finding | file:line | status | severity |
|---|---|---|---|
| _no findings_ |  |  |  |

