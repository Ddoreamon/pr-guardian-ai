# V4.5 evaluation statistics — correction and denominators

Applies to `docs/v4.5-eval/results.json` and `REPORT.md` as produced by the V4.5
round. The original files are **not** rewritten; this note records what their
numbers actually count.

## 1. Row composition

| Group | Rows | Scored? |
|---|---|---|
| Dev — recorded V4.4 traces (baseline 6 + planner 6) | 12 | **no** (`expected` is null) |
| Held-out — complete / callee-only / one-caller profiles | 12 | yes |
| Negative controls — wrong SHA, truncated read, search-only, per case | 21 | yes |
| **Total rows** | **45** | **33 scored** |
| Distinct cases | 13 (6 dev + 7 held-out) | — |

`status_accuracy`, `obligation_kind_precision` and `obligation_kind_recall` were
computed over the **33 scored** profiles. That part is consistent.

## 2. The unknown rate denominator was wrong

The report printed `unknown_rate = 2.2%`. That is `1 / 45` — one unknown row divided
by **all** rows, including the 12 dev rows that are not scored.

The correct figure over scored profiles is `1 / 33 = 3.03%`.

The single unknown row is `held-out/H3-dynamic/complete` (dynamic `getattr`
dispatch), which is the intended behaviour.

## 3. `H6-local/search-only` was scored twice

`PROFILES["H6-local"]` contains a `search-only` profile **and** the evaluator adds a
`search-only` negative control for every case. `H6-local` therefore produced two
identical rows (`held-out/H6-local/search-only` and
`negative/H6-local/search-only`), both counted in the 33.

Distinct scored profiles: **32** (33 − the duplicate). Corrected accuracy on the
deduplicated set is unchanged (100%), because both rows agree.

## 4. Obligation kind precision/recall over-counts the negative controls

Every negative control reuses that case's obligation set, so each case contributes
its kinds up to four times (one profile plus three controls). The reported
precision/recall of 1.00/1.00 is therefore weighted by controls, not by cases. Per
distinct case the figure is unchanged at 1.00/1.00, but the denominator should be
read as "profiles", not "cases".

## 5. What this does **not** change

* No status, obligation, missing/unknown id or explanation in `results.json`
  changes; the rows are intact.
* The conclusion stands: 0 false sufficient, 0 false insufficient on that set.
* The caveat stands, and is strengthened by the arithmetic above: 33 scored profiles
  from 7 authored cases is a small, same-author sample, so this is exploratory.

## 6. Corrected summary line

```
total rows 45 = dev 12 (unscored) + held-out 12 + negative 21
scored profiles 33 (distinct 32; one duplicate search-only profile on H6-local)
status accuracy 33/33 = 100%   (unchanged on the deduplicated 32)
unknown rate 1/33 = 3.03%      (previously reported as 1/45 = 2.22%)
false sufficient 0   false insufficient 0
```
