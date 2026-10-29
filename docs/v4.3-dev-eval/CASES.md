# V4.3 frozen dev evaluation

Six independent cases, fixed **before** any efficiency work: three cross-file
bugs and three clean PRs. No case overlaps the frozen held-out sets or the
historical smoke repositories.

Budgets: `max_tool_calls=4`, `max_tool_rounds=3`, `search_top_k=5`.

## Cases

| id | label | branch | head file | gold | required evidence |
|---|---|---|---|---|---|
| B1-cents | bug | `case/b1` | `src/checkout.py` | `src/shipping.py:4` `estimate` | `src/shipping.py:1-8`, `src/checkout.py:1-10` |
| B2-kwarg | bug | `case/b2` | `src/checkout.py` | `src/shipping.py:4` `estimate` | `src/shipping.py:1-8` |
| B3-exception | bug | `case/b3` | `src/checkout.py` | `src/stock.py:4` `reserve` | `src/stock.py:1-8`, `src/checkout.py:1-12` |
| C1-kwarg | clean | `case/c1` | `src/checkout.py` | `src/checkout.py:12` `label` | `src/checkout.py:1-14`, `src/shipping.py:1-8` |
| C2-identity | clean | `case/c2` | `src/checkout.py` | `src/money.py:4` `to_cents` | `src/money.py:1-6`, `src/checkout.py:9-12` |
| C3-guard | clean | `case/c3` | `src/stock.py` | `src/stock.py:4` `reserve` | `src/stock.py:1-10` |

## Rationale

* **B1-cents** — a cents value reaches a dollars parameter; only the callee's docstring decides it
* **B2-kwarg** — estimate() takes no express keyword, so the call raises at runtime
* **B3-exception** — a raised exception escapes the caller's except clause; the callee body is required to see it
* **C1-kwarg** — the new caller is fine; a reviewer must still read it to confirm
* **C2-identity** — a conversion looks risky until the callee confirms it is applied once
* **C3-guard** — an added guard is harmless; the reviewer must read it to prove that

## Metrics

| metric | meaning |
|---|---|
| `tool_calls_requested` | calls the model asked for |
| `tool_calls_executed` | calls that really ran |
| `tool_calls_denied` | refused by the budget |
| `invalid_argument_calls` | validation failures (category C) |
| `empty_searches` | searches returning nothing (D) |
| `exact_repeat_calls` | identical executed calls (A) |
| `overlapping_read_calls` | different arguments, overlapping lines (B) |
| `required_evidence_obtained` | how many required ranges were delivered |
| `review_status` / `partial_rate` | process outcome |
| `prompt_tokens`, `completion_tokens`, `latency_s` | cost |
| `findings_*`, `findings_correct`, `findings_false_positive` | quality |

## Acceptance rule

A change is kept only if it **keeps `required_evidence_obtained` and
`findings_correct` unchanged** while reducing executed calls or denials.
Fewer calls with missing evidence is a regression, not a win.

## Status

The cases are frozen; no model has been run against them yet. Measuring
`findings_correct` requires a live run, which this round does not do.
