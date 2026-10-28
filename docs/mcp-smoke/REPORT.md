# DeepSeek -> MCP -> Finding smoke test (development cases)

Not part of the frozen benchmark. Case A is a cross-file bug (a keyword the
callee rejects); case B is a cross-file clean change (a keyword the callee
accepts). Both go through the real provider and the real MCP stdio session.

## Direct tool comparison (same snapshot, same arguments)

* pinned commit: `8e45fc1ddc12c4b2b8bf91f460404de0dbd33dfe`
* MCP session start: 0.9654s
* per-call median: MCP 0.053s vs in-process 0.0497s
* payloads identical: True (4/4)
* every payload ref == pinned SHA: True
* worktree edit ignored: True
* session closed: True; leftover threads: []

| tool | arguments | latency (s) | identical |
|---|---|---|---|
| read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 20}` | 0.1083 | True |
| read_code_context | `{"file_path": "src/shipping.py", "start_line": 1, "end_line": 8}` | 0.048 | True |
| find_symbol_definition | `{"symbol": "estimate"}` | 0.058 | True |
| find_symbol_definition | `{"symbol": "format_money"}` | 0.0015 | True |

## End-to-end runs

### bug / mcp

* branch `dev/express-shipping` at `8e45fc1ddc12c4b2b8bf91f460404de0dbd33dfe`
* llm_status: `ok`; tool_status: `ok`
* model calls: 3 (failures 0), rounds 3
* tokens: 4772 prompt / 932 completion
* tool calls: 3; leaked threads: []

| round | tool_call_id | tool | arguments | outcome | matched |
|---|---|---|---|---|---|
| 1 | `call_00_ET_dBh8gjVpBZv3pcENph1r7381` | read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 40}` | hit | 0 |
| 1 | `call_01_ET_OhUZwuqejfyvk7OOeVQQ1739` | find_symbol_definition | `{"symbol": "estimate"}` | hit | 1 |
| 2 | `call_00_bUOIIU7er0kkLwGBIL9t7764` | read_code_context | `{"file_path": "src/shipping.py", "start_line": 1, "end_line": 30}` | hit | 0 |

| finding | file:line | status | source | severity |
|---|---|---|---|---|
| total() passes an unsupported `express=True` keyword to shipping.estimate(), whi | src/checkout.py:10 | suspected | llm | high |

### bug / local

* branch `dev/express-shipping` at `8e45fc1ddc12c4b2b8bf91f460404de0dbd33dfe`
* llm_status: `ok`; tool_status: `ok`
* model calls: 3 (failures 0), rounds 3
* tokens: 4826 prompt / 881 completion
* tool calls: 3; leaked threads: []

| round | tool_call_id | tool | arguments | outcome | matched |
|---|---|---|---|---|---|
| 1 | `call_00_ET_TUTuMrMZIfoaU1g2WJbf3967` | read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 40}` | hit | 0 |
| 1 | `call_01_ET_IzJFixhmH98lpD9fTdki6355` | find_symbol_definition | `{"symbol": "estimate"}` | hit | 1 |
| 2 | `call_00_gyPxutMj86hwpEfnPsgS3749` | read_code_context | `{"file_path": "src/shipping.py", "start_line": 1, "end_line": 20}` | hit | 0 |

| finding | file:line | status | source | severity |
|---|---|---|---|---|
| total() now passes an unsupported keyword argument `express=True` to estimate(), | src/checkout.py:10 | suspected | llm | high |

### clean / mcp

* branch `dev/currency-arg` at `c71ac357d5ad82dedc69996f05aad711b575273e`
* llm_status: `ok`; tool_status: `ok`
* model calls: 2 (failures 0), rounds 2
* tokens: 2645 prompt / 231 completion
* tool calls: 2; leaked threads: []

| round | tool_call_id | tool | arguments | outcome | matched |
|---|---|---|---|---|---|
| 1 | `call_00_ET_JAKo2uvi7bHyesKcMRf45869` | read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 40}` | hit | 0 |
| 1 | `call_01_ET_wfeOecflOukhup0oYm638431` | find_symbol_definition | `{"symbol": "format_money"}` | hit | 1 |

| finding | file:line | status | source | severity |
|---|---|---|---|---|
| _no findings_ |  |  |  |  |

### clean / local

* branch `dev/currency-arg` at `c71ac357d5ad82dedc69996f05aad711b575273e`
* llm_status: `ok`; tool_status: `ok`
* model calls: 2 (failures 0), rounds 2
* tokens: 2645 prompt / 161 completion
* tool calls: 2; leaked threads: []

| round | tool_call_id | tool | arguments | outcome | matched |
|---|---|---|---|---|---|
| 1 | `call_00_ET_iMGLHDeCZWdUCjctwp1Z9370` | read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 40}` | hit | 0 |
| 1 | `call_01_ET_GXzl43MZIahxGUHiZoDY4342` | find_symbol_definition | `{"symbol": "format_money"}` | hit | 1 |

| finding | file:line | status | source | severity |
|---|---|---|---|---|
| _no findings_ |  |  |  |  |

