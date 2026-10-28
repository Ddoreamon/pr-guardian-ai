# V4.4 frozen dev cases

Six independent repositories, built deterministically (fixed commit dates).
Every label is executable: `tests/test_gold.py` passes on `main` and on the
clean branch, and fails on the bug branch. `tests/` is outside the tool
allowlist, so the agent cannot read the answer.

Budgets: `max_tool_calls=4`, `max_tool_rounds=3`, `search_top_k=5`.

| id | label | head sha | gold | required evidence |
|---|---|---|---|---|
| B1-cents | bug | `61c61107bad7` | `src/shipping.py:4` `estimate` | `src/shipping.py:4-6`, `src/checkout.py:8-10` |
| B2-kwarg | bug | `84c6d9792a62` | `src/shipping.py:4` `estimate` | `src/shipping.py:4-6`, `src/checkout.py:8-10` |
| B3-exception | bug | `52886370b7ee` | `src/stock.py:4` `reserve` | `src/stock.py:4-8`, `src/checkout.py:13-18` |
| C1-kwarg | clean | `82deae9e3348` | `src/checkout.py:19` `label` | `src/checkout.py:21-23` |
| C2-identity | clean | `0e8a8671c8d1` | `src/checkout.py:19` `dollars_as_cents` | `src/checkout.py:21-23` |
| C3-refactor | clean | `f2dc4c146988` | `src/stock.py:4` `reserve` | `src/stock.py:4-8`, `src/checkout.py:13-18` |

## Rationale

* **B1-cents** — a cents value reaches a dollars parameter; only the callee's docstring decides it
* **B2-kwarg** — estimate() takes no express keyword, so the call raises at runtime
* **B3-exception** — the callee raises a new exception type and the caller only catches ValueError; both sides are needed to see it
* **C1-kwarg** — a new caller is fine, but the reviewer must read it and the callee
* **C2-identity** — a conversion looks risky until the callee confirms it is exact
* **C3-refactor** — a rewrite that preserves the result for every input

## Metrics
`model_requests`, `model_failures`, `tool_calls_requested`, `tool_calls_executed`, `tool_calls_denied`, `tool_errors`, `tool_misses`, `required_evidence_obtained`, `evidence_required_total`, `review_status`, `findings_total`, `findings_source_verified`, `finding_correct`, `finding_false_positive`, `prompt_tokens`, `completion_tokens`, `latency_s`

