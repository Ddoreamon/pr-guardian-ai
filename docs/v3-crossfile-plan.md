# V2.2 cross-file evaluation plan (design review only — not yet run)

Status: **proposal**. No case has been written to code, no model call has been
made, and no held-out result exists. This document is for review before any data
is frozen.

---

## 1. What this set is for

The V2.1 cross-file set (10 cases) showed that V1 already solved most cases, so
it could not separate "reads the repository" from "reads the diff". The reason is
structural: the cases were *hard for a diff-only reviewer by accident*, not *by
construction*.

This set is built around one property:

> **The same diff is a defect in one repository and correct in another.**

Only the external context differs. A model that never calls a tool sees exactly
the same input in both variants and therefore cannot distinguish them. A model
that reads the defining context can.

That also gives a clean falsification test: if a method scores the same on both
variants of a pair, it is not using context — whatever its aggregate score says.

---

## 2. Case inventory (22 cases, 11 contrast pairs)

Every pair shares one identical diff. `A` and `B` differ only in a **different
file** that the diff does not touch.

| Pair | Diff (identical in both variants) | Context that differs (another file) | A: bug | B: clean |
|---|---|---|---|---|
| P1 | `pricing.py`: `total >= MIN` → `total > MIN` | `contract.py`: `ALLOW_EXACT_THRESHOLD = True` vs `False` | inclusive contract violated at exactly MIN | contract says strictly-greater; change is correct |
| P2 | `pricing.py`: delete the `is_valid_quantity(qty)` guard | `validation.py`: `qty > 0` vs `qty >= 0` (+ docstring) | zero quantity now silently accepted | callers already reject 0; guard was redundant |
| P3 | `reporting.py`: `tax_for(total)` → `tax_for(net_total(total))` | `totals.py`: `net_total` returns total minus discount vs total times 100 | tax computed on a 100× value | tax on net is the documented rule |
| P4 | `pagination.py`: `items[start:start+size]` → `items[start:start+size+1]` | `contract.py`: `SIZE_IS_INCLUSIVE = False` vs `True` | one extra item per page | inclusive-size contract satisfied |
| P5 | `money.py`: `round(x, 2)` → `round(x, 3)` | `contract.py`: `DECIMAL_PLACES = 2` vs `3` | currency rounding contract broken | contract allows 3 decimals |
| P6 | `stock.py`: `raise ValueError` → `raise KeyError` | `api.py`: `except ValueError` vs `except KeyError` | caller's handler no longer catches it | caller expects KeyError; change is correct |
| P7 | `filters.py`: default `limit=None` → `limit=0` | `caller.py`: calls with no argument expecting unlimited vs expecting zero | unlimited path becomes "zero results" | zero default is the documented behaviour |
| P8 | `search.py`: empty result `return []` → `return None` | `caller.py`: `for hit in results` vs `if results is None` | iteration over None crashes | caller explicitly handles None |
| P9 | `cart.py`: `items = list(items)` → `items = items` | `caller.py`: reuses the original list and asserts it unchanged vs discards it | caller's list mutated | documented in-place mutation, caller does not reuse |
| P10 | `checkout.py`: swap the `qty` and `price` validations | `contract.py`: `PRICE_CHECK_REQUIRES_QTY` True vs False | price check now runs before qty, changing which error surfaces | checks are independent; order irrelevant |
| P11 | `inventory.py`: `if sku in seen: continue` → `if sku in seen: break` | `contract.py`: input may contain scattered duplicates vs "stop at first duplicate" | later rows silently dropped | stop-at-first-duplicate is the contract |

Coverage: boundary conditions (P1, P4, P5), null/empty handling (P7, P8),
exception contracts (P6, P10), state mutation (P9), filtering semantics (P11,
P2), unit/scaling errors (P3).

Bug / clean balance: **11 bug variants, 11 clean variants.** Each pair contributes
exactly one of each, so a method that always reports "bug" gets 50% precision and
a method that always reports "clean" gets 50% recall — both are visibly bad.

---

## 3. Why each variant is decidable only with context

The diff text is byte-identical inside a pair, so the following are excluded by
construction as ways to get the right answer from the diff alone:

- the changed identifier or constant name,
- the shape of the edit (`>=` → `>`, delete a guard, swap checks),
- "this smells like a bug" prior: it would fire equally in both variants.

Ground truth is therefore a function of **(diff, context)**. For every pair the
harness asserts `diff_a == diff_b` mechanically; a pair that fails this assertion
is rejected before any measurement.

---

## 4. Ground truth

Each of the 22 cases carries a hidden pytest file that encodes the specification
for that variant:

* the **base** revision must pass,
* the **bug** variant must fail,
* the **clean** variant must pass.

Verification runs before freezing and is committed as
`benchmark/verify_contrast.py`, mirroring the existing `verify_crossfile.py`
(base passes → PR outcome matches the label). A case whose tests do not separate
the two variants is removed or rewritten, never relabelled.

Boundary inputs are explicitly required in every clean variant's tests (e.g. P1
tests exactly `MIN` and `MIN + epsilon`), so "clean" cannot mean "untested".

---

## 5. Splits (frozen before any run)

| Split | Pairs | Cases |
|---|---|---|
| dev | P1, P2, P3, P4, P5 | 10 (5 bug, 5 clean) |
| held-out | P6, P7, P8, P9, P10, P11 | 12 (6 bug, 6 clean) |

Freeze rules:

* split assignment is fixed in this document and in code before the first run;
* the held-out set is run **once** for the final number and never used to edit a
  prompt, a tool description, or a case;
* dev may be used freely for plumbing and sanity checks;
* any later change to a held-out case is recorded in `docs/BENCHMARK_CORRECTIONS.md`
  and forces a fresh freeze with a new version number.

---

## 6. Methods to compare

| Method | Definition |
|---|---|
| `v1-diff-only` | current V1 reviewer: diff + file list, no tools |
| `v2-tools` | tool-calling loop, tools reading the **PR commit snapshot** |
| `v2-tools-no-context` (optional ablation) | tools enabled but `read_code_context` limited to the changed file only — isolates "reading the same file again" from "reading another file" |

The ablation is worth one extra pass: it distinguishes a real context effect from
a generic "more tokens helped" effect.

---

## 7. Metrics (all reported per method, per split)

Detection quality (the matching rule stays the AST "touched definition" rule
introduced in V2.1; it is documented and frozen):

* Precision, Recall, F1 (PR-level: a variant is flagged if any finding lands in
  the touched definition of the changed file)
* Clean-PR false-alarm rate over the clean variants
* Location accuracy over the bug variants

Tool effectiveness — reported separately, because a call can succeed without
retrieving anything:

* effective hit rate = calls with `outcome = hit` ÷ all tool calls
* miss rate, error rate, budget-denial rate
* calls per case, and the share of cases where at least one hit preceded the
  finding
* **contrast consistency**: for each pair, did the method get both variants right?
  (9/11 or better is the bar this set is designed to detect; a diff-only method
  cannot exceed ~50% by construction)

Cost:

* prompt / completion tokens (provider-reported, summed over every request,
  including the final JSON repair call)
* wall-clock latency per case
* cost in USD, computed only from an explicitly configured price table
  (`PR_GUARDIAN_PRICE_INPUT_PER_M`, `PR_GUARDIAN_PRICE_OUTPUT_PER_M`); if no
  prices are configured the report says *not computed*, never a guess

---

## 8. Pre-registered expectations (to avoid post-hoc storytelling)

Written before any run:

1. `v1-diff-only` should score near chance on **contrast consistency**, because
   both variants of a pair present an identical diff. If V1 scores well above
   chance, the pairs are leaking context into the diff and the set is invalid.
2. `v2-tools` should exceed V1 on contrast consistency if and only if it actually
   reads the differing file. Its effective hit rate is the leading indicator.
3. The ablation should sit between the two. If the ablation matches `v2-tools`,
   the gain came from re-reading the changed file, not from cross-file context.
4. Any result where V2 recall rises while its effective hit rate stays near zero
   is a measurement error, not a capability.

These are predictions, not results. Nothing here may be quoted as a finding until
the frozen run exists.

---

## 9. Not in scope

- No new tools, no shell, no test execution by the model.
- No prompt tuning against held-out data.
- No re-running or re-scoring the V2.1 cross-file set: those artefacts are
  historical records (see `docs/BENCHMARK_CORRECTIONS.md`).
- No change to the public Render deployment; it keeps live model calls and tool
  calling disabled.

---

## 10. Open questions for review

1. **Pair count.** 11 pairs gives 22 cases; is that enough to separate a 50%
   baseline from a 75% contrast score? (Power is the weak point of every set built
   so far; a 6-pair dev / 11-pair held-out split would be the minimum I'd trust.)
2. **Clean variants.** Should some clean variants be "correct but ugly" (a real
   false-alarm trap for the model) rather than "correct and tidy"? I lean yes for
   2–3 pairs.
3. **Optional ablation.** Worth 12 extra model calls, or skip to keep cost down?
   I lean keep, because it is the only way to tell "context" from "more tokens".
4. **Location accuracy definition** for the clean variants is undefined (no defect
   to locate); the plan reports it on bug variants only. Confirm that is acceptable.
