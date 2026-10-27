# Trace evidence — why "read the context" is not the same as "got it right"

Captured from a real run (`deepseek-flash`, tools enabled) on the **clean**
variant of pair P2. Nothing here is synthesised: the calls, their arguments and
the outcomes come from `ReviewReport.tool_calls`.

## What the model did

| round | tool | arguments | outcome | result |
|---|---|---|---|---|
| 1 | `read_code_context` | `src/tax.py` 1–20 | hit | 478 chars |
| 1 | `find_symbol_definition` | `convert` | hit | 1 match |
| 1 | `find_symbol_definition` | `to_cents` | hit | 1 match |
| 2 | `read_code_context` | `src/money.py` 1–12 | hit | 593 chars |

4 tool calls, 4 effective hits, 3 model requests, 4,735 prompt tokens.

So the model did open the **context file** — the file that differs between the
two variants — and it even quoted it correctly.

## What it concluded

> "The added call to `to_cents()` is an identity function, so it does not convert
> dollars to cents and the unit mismatch (and 100x tax error) remains."
>
> evidence: `src/tax.py:5-7` → `return convert(to_cents(total))`;
> `src/money.py:4-6` → …

The premise is right (`to_cents` is the identity in this service) but the
conclusion is wrong: the model kept the assumption from `tax.py`'s docstring
("expressed in dollars") instead of adopting `money.py`'s convention ("amounts
are already integer cents"). The variant is labelled clean because the tests pass
in that service, and the change is a genuine no-op there.

## Why this matters for the write-up

* It is direct evidence that the tool layer works end to end on a real provider.
* It is equally direct evidence that **retrieval is not reasoning**. A high
  effective hit rate (96.4% over the dev run) coexisted with a 60% clean
  false-alarm rate.
* Therefore no claim of the form "V2 reads context, so V2 reviews better" is
  supported by this run. The retrieval claim and the correctness claim have to be
  reported, and judged, separately.
