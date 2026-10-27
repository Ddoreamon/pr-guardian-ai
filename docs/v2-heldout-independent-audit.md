# V2 held-out — independent audit record

Status: **frozen**. This document archives the independent review of the held-out
run and closes the Function Calling stage. No model call was made to produce it,
and the run's artefacts were not modified.

## 0. Provenance — read this before quoting the numbers

The adjudication recorded here was performed by an **independent AI reviewer**
with access to the frozen artefacts (`REPORT.md`, `results.json` including every
finding's full text, `experiment.json` and the per-variant tool traces). It was
**not** a third-party human review, and it must not be described as one. The
reviewer was not the agent that produced the run.

The producing agent's automated scores are kept as-is in
`contrast-heldout-v3/`; this document does not rewrite them.

## 1. Frozen run identity

| Field | Value |
|---|---|
| Artefacts | `docs/benchmark-results/contrast-heldout-v3/` (unmodified) |
| Code SHA at run time | `bcae59d796b071807b121e84c2ee7ebf56365d20` |
| Dataset version | v3 (dev P1–P5, held-out P6–P11) |
| Model | `deepseek-flash` via `https://api.deepseek.com`, temperature 0 |
| Window (UTC) | 2026-10-27T15:41:26Z → 15:44:17Z |
| Tool budget | 3 rounds / 4 calls per variant |
| Re-run since | **No** |
| Label or prompt change since | **No** |

An earlier attempt (`contrast-heldout-v3-aborted-0calls/`) is retained: it made
**zero** model requests because a shadowed git helper broke repository
materialisation. It is an incident record, not a result.

## 2. Final adjudication

Counts over the 12 variants per method (6 pairs × 2 variants).

| Method | TP | FN | TN | FP | Contested | Decided |
|---|---|---|---|---|---|---|
| V1 diff-only | **4** | **2** | **1** | 0 | **5** | 7 |
| V2 tools | **5** | **1** | **2** | 0 | **4** | 8 |

Readings:

* **Decided** excludes contested findings.
* **Strict** counts every contested finding as a false positive, so the model
  gains nothing by flagging context-dependent clean variants.

### Detection metrics

| Method | Precision (decided) | Recall | **F1 (decided)** | Precision (strict) | **F1 (strict)** | Clean FAR (strict) |
|---|---|---|---|---|---|---|
| V1 diff-only | 100.00% | 66.67% | **80.00%** | 44.44% | **53.33%** | 83.33% |
| V2 tools | 100.00% | 83.33% | **90.91%** | 55.56% | **66.67%** | 66.67% |

Strict F1 is derived from the recorded strict precision and recall:
`2PR/(P+R)` → V1 `2·0.4444·0.6667/1.1111 = 53.33%`, V2 `2·0.5556·0.8333/1.3889 = 66.67%`.
The frozen `REPORT.md` prints decided F1 only; the evaluator now also computes
`f1_strict` for future runs, without re-running this one.

### Pair-level

| Method | Pairs | Decided pairs | Consistent | Pair consistency | Binomial p (p0 = 0.25) |
|---|---|---|---|---|---|
| V1 | 6 | **1** | 0 | 0.00% | 1.0 |
| V2 | 6 | **2** | 1 | 50.00% | 0.4375 |

Paired comparison over the 7 jointly decided variants: V2-only correct 1,
V1-only correct 0, exact McNemar **p = 1.0**.

## 3. What actually changed

**P7 is the only variant where V2 clearly added a detection.** P7-a (retry count
honoured by the client) was missed by V1 and found by V2, which read
`src/client.py` and observed that `get()` only succeeds on its second attempt.

Everything else is unchanged between the two methods:

* both missed **P10-a** (the `http` → `https` narrowing in a mixed-scheme feed);
* both produced **zero decided false positives**;
* both flagged the same context-dependent clean variants as contested;
* V2 additionally stayed silent on P10-b, which V1 flagged — that is the whole of
  the contested-count difference (5 → 4).

So the honest summary is: **one extra detection out of six bug variants, at
≈9.9× the prompt tokens (45,226 vs 4,580), with no statistically significant
difference (McNemar p = 1.0, pair denominators 1 and 2).**

## 4. Partial variants

Three V2 variants ended in `tool_limit` (budget exhausted; `partial = true`):

| Variant | Tool calls | Note |
|---|---|---|
| P9-a | 5 | hit the 4-call budget while checking the config contract |
| P9-b | 6 | same case, clean variant |
| P10-b | 6 | exhausted the budget on the feed contract |

They are reported with their real status; none was silently treated as complete
or dropped. P9-a nonetheless produced the correct finding, and P10-b was correct
to stay silent — but their evidence is incomplete by construction, which is why
the partial flag is recorded per variant.

## 5. P11-a — the second finding does not count as a new defect

V2 produced two findings for P11-a:

1. `L6` — "Empty-list guard was removed, so `average([])` now raises
   ZeroDivisionError" → this is the defect the PR introduces.
2. `L4` — "The new None branch contradicts the `items: list[float]` type
   contract" → **not attributable to this PR**: the annotation `items: list[float]`
   exists unchanged in the base revision, and the PR only alters the guard
   condition. The condition the finding describes predates the change.

Adjudication: the variant stays **TP** on the strength of finding 1. Finding 2 is
recorded as an unverified observation about pre-existing code, not as an extra
detection and not as a false positive (it is located in the changed file and the
PR does touch that function).

## 6. What this audit did not do

* No label was changed; no pair was added, removed or reclassified.
* The run was not repeated, and no prompt or budget was altered afterwards.
* The automated scores in `contrast-heldout-v3/` were left untouched; the strict
  F1 values above are derived, and the derivation is shown.
* No claim of general V2 superiority is made or supported by these numbers.

## 7. Standing conclusion

Function Calling (V2) is **closed** on this evidence:

* the protocol works end to end against a real provider (32 requests, 0 failed,
  41 tool calls, effective hit rate 68.29%);
* it yields **one** additional true detection over the diff-only baseline on six
  held-out pairs, at roughly ten times the prompt cost;
* neither the detection difference nor the pair-level difference is significant
  at this sample size.

That is a legitimate negative-to-weak-positive result for an exploratory stage,
and it is recorded as final. Any further improvement would require a new,
pre-registered evaluation, which is out of scope for this stage.
