# V4.4 evaluation — evidence-aware planner

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
| required evidence coverage | 90.00% |
| tool calls requested / executed / denied | 23 / 20 / 3 |
| partial rate | 50.00% |
| source-verified findings | 3 |
| prompt / completion tokens | 43899 / 4760 |
| mean latency | 6.33s |

## Per case

| case | label | status | calls req/exec/den | evidence | findings | correct | verified | latency |
|---|---|---|---|---|---|---|---|---|
| B1-cents | bug | partial | 5/4/1 | 2/2 | 1 | yes | 1 | 8.72s |
| B2-kwarg | bug | completed | 4/4/0 | 2/2 | 1 | yes | 1 | 7.17s |
| B3-exception | bug | partial | 4/4/0 | 2/2 | 1 | yes | 1 | 9.2s |
| C1-kwarg | clean | partial | 6/4/2 | 1/1 | 0 | yes | 0 | 5.3s |
| C2-identity | clean | completed | 3/3/0 | 1/1 | 0 | yes | 0 | 5.22s |
| C3-refactor | clean | completed | 1/1/0 | 1/2 | 0 | yes | 0 | 2.4s |

## Tool calls

### B1-cents

| round | tool | arguments | outcome |
|---|---|---|---|
| 1 | read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 40}` | hit |
| 1 | find_symbol_definition | `{"symbol": "to_cents"}` | hit |
| 2 | read_code_context | `{"file_path": "src/shipping.py", "start_line": 1, "end_line": 30}` | hit |
| 2 | read_code_context | `{"file_path": "src/money.py", "start_line": 1, "end_line": 30}` | hit |
| 3 | search_code | `{"query": "total(weight_kg", "top_k": 5}` | budget_denied |

### B2-kwarg

| round | tool | arguments | outcome |
|---|---|---|---|
| 1 | read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 25}` | hit |
| 1 | find_symbol_definition | `{"symbol": "estimate"}` | hit |
| 2 | read_code_context | `{"end_line": 10, "file_path": "src/shipping.py", "start_line": 1}` | hit |
| 2 | search_code | `{"query": "express shipping surcharge estimate", "top_k": 5}` | hit |

### B3-exception

| round | tool | arguments | outcome |
|---|---|---|---|
| 1 | read_code_context | `{"file_path": "src/stock.py", "start_line": 1, "end_line": 20}` | hit |
| 1 | search_code | `{"query": "insufficient stock ValueError", "top_k": 10}` | hit |
| 2 | read_code_context | `{"end_line": 30, "file_path": "src/checkout.py", "start_line": 1}` | hit |
| 3 | search_code | `{"query": "except ValueError reserve", "top_k": 10}` | hit |

### C1-kwarg

| round | tool | arguments | outcome |
|---|---|---|---|
| 1 | read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 30}` | hit |
| 1 | find_symbol_definition | `{"symbol": "total"}` | hit |
| 2 | search_code | `{"query": "label(", "top_k": 10}` | hit |
| 2 | search_code | `{"query": "checkout label order", "top_k": 10}` | hit |
| 3 | search_code | `{"query": "def estimate weight_kg", "top_k": 5}` | budget_denied |
| 3 | search_code | `{"query": "pub 展开 public API checkout functions documentation", "top_k": 5}` | budget_denied |

### C2-identity

| round | tool | arguments | outcome |
|---|---|---|---|
| 1 | read_code_context | `{"file_path": "src/checkout.py", "start_line": 1, "end_line": 30}` | hit |
| 1 | find_symbol_definition | `{"symbol": "to_cents"}` | hit |
| 2 | read_code_context | `{"file_path": "src/money.py", "start_line": 1, "end_line": 30}` | hit |

### C3-refactor

| round | tool | arguments | outcome |
|---|---|---|---|
| 1 | read_code_context | `{"file_path": "src/stock.py", "start_line": 1, "end_line": 30}` | hit |

