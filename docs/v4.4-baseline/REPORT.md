# V4.4 evaluation — baseline

Real `deepseek-flash`, six frozen dev cases, budgets 4 calls / 3 rounds.
Bug labels are proven by a hidden test the agent cannot read.

## Summary

| metric | value |
|---|---|
| cases | 6 |
| TP / FP / FN / TN | 3 / 0 / 0 / 3 |
| accuracy | 100.00% |
| precision | 100.00% |
| recall | 100.00% |
| F1 | 100.00% |
| clean false-alarm rate | 0.00% |
| required evidence coverage | 100.00% |
| tool calls requested / executed / denied | 28 / 23 / 5 |
| partial rate | 50.00% |
| source-verified findings | 3 |
| prompt / completion tokens | 41642 / 4923 |
| mean latency | 7.06s |

## Per case

| case | label | status | calls req/exec/den | evidence | findings | correct | verified | latency |
|---|---|---|---|---|---|---|---|---|
| B1-cents | bug | partial | 5/4/1 | 2/2 | 1 | yes | 1 | 7.27s |
| B2-kwarg | bug | completed | 4/4/0 | 2/2 | 1 | yes | 1 | 5.66s |
| B3-exception | bug | partial | 6/4/2 | 2/2 | 1 | yes | 1 | 12.23s |
| C1-kwarg | clean | partial | 6/4/2 | 1/1 | 0 | yes | 0 | 8.33s |
| C2-identity | clean | completed | 4/4/0 | 1/1 | 0 | yes | 0 | 4.32s |
| C3-refactor | clean | completed | 3/3/0 | 2/2 | 0 | yes | 0 | 4.54s |

## Tool calls

### B1-cents

| round | tool | arguments | outcome |
|---|---|---|---|
| 1 | read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 30}` | hit |
| 1 | find_symbol_definition | `{"symbol": "estimate"}` | hit |
| 1 | find_symbol_definition | `{"symbol": "to_cents"}` | hit |
| 2 | read_code_context | `{"file_path": "src/shipping.py", "start_line": 1, "end_line": 10}` | hit |
| 2 | read_code_context | `{"file_path": "src/money.py", "start_line": 1, "end_line": 10}` | budget_denied |

### B2-kwarg

| round | tool | arguments | outcome |
|---|---|---|---|
| 1 | read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 30}` | hit |
| 1 | find_symbol_definition | `{"symbol": "estimate"}` | hit |
| 2 | read_code_context | `{"file_path": "src/shipping.py", "start_line": 1, "end_line": 20}` | hit |
| 2 | search_code | `{"query": "total(weight_kg"}` | hit |

### B3-exception

| round | tool | arguments | outcome |
|---|---|---|---|
| 1 | read_code_context | `{"file_path": "src/stock.py", "start_line": 1, "end_line": 40}` | hit |
| 1 | search_code | `{"query": "reserve(available, requested) ValueError insufficient stock", "top_k": 10}` | hit |
| 2 | read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 30}` | hit |
| 2 | search_code | `{"query": "except ValueError reserver stock shortage", "top_k": 10}` | hit |
| 3 | search_code | `{"query": "reserve", "top_k": 10}` | budget_denied |
| 3 | search_code | `{"query": "insufficient stock ValueError validation failure", "top_k": 10}` | budget_denied |

### C1-kwarg

| round | tool | arguments | outcome |
|---|---|---|---|
| 1 | read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 40}` | hit |
| 1 | find_symbol_definition | `{"symbol": "total"}` | hit |
| 2 | read_code_context | `{"file_path": "src/shipping.py", "start_line": 1, "end_line": 40}` | hit |
| 2 | read_code_context | `{"file_path": "src/money.py", "start_line": 1, "end_line": 40}` | hit |
| 3 | search_code | `{"query": "label", "top_k": 10}` | budget_denied |
| 3 | search_code | `{"query": "checkout.total place label order", "top_k": 10}` | budget_denied |

### C2-identity

| round | tool | arguments | outcome |
|---|---|---|---|
| 1 | read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 30}` | hit |
| 1 | find_symbol_definition | `{"symbol": "to_cents"}` | hit |
| 2 | read_code_context | `{"file_path": "src/money.py", "start_line": 1, "end_line": 40}` | hit |
| 2 | search_code | `{"query": "dollars_as_cents", "top_k": 10}` | hit |

### C3-refactor

| round | tool | arguments | outcome |
|---|---|---|---|
| 1 | read_code_context | `{"file_path": "src/stock.py", "start_line": 1, "end_line": 20}` | hit |
| 1 | search_code | `{"query": "reserve available requested"}` | hit |
| 2 | read_code_context | `{"file_path": "src/checkout.py", "end_line": 20, "start_line": 1}` | hit |

