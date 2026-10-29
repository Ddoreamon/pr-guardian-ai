# Dev v2 — human adjudication of every finding

Run: `docs/benchmark-results/contrast-dev-v2/`, model `deepseek-flash`, dev split
(5 pairs / 10 variants), one run. Rule applied: the one adopted in
`docs/v2.2-audit.md` — **a defect is a violation of a documented contract**
(annotations, docstrings, behaviour the surrounding code establishes). Passing
tests is necessary, not sufficient.

Verdicts: TP = reported a real contract violation · FP = reported something that
does not violate a documented contract · TN = correctly reported nothing ·
FN = missed a real violation.

## V1 · diff only

| Variant | Label | Auto | Human | Why |
|---|---|---|---|---|
| P1-a | bug | TP | **TP** | names the unsupported `strict` argument; the callee signature is in another file and really does reject it |
| P1-b | clean | TN | **TN** | said nothing |
| P2-a | bug | TP | **TP** | "passes a cents amount to convert" — the 100× unit error |
| P2-b | clean | TN | **TN** | said nothing |
| P3-a | bug | TP | **TP** | the removed copy is observable: the caller reads the mutated list |
| P3-b | clean | FP | **FP** (debatable) | the caller snapshots the count first, so behaviour is unchanged; nothing documents "returns a new list". A reviewer following house style could still raise it — recorded as a convention-level concern, not a contract violation |
| P4-a | bug | TP | **TP** | `qty == limit` is now rejected; the only guard in A allows it |
| P4-b | clean | FP | **FP** (debatable) | `precheck` already rejects `qty >= limit` in **both** base and PR, so `accept`'s observable behaviour never changes; the docstring "within the limit" was already loose |
| P5-a | bug | TP | **TP** | `LookupError` escapes the caller's `except ValueError` |
| P5-b | clean | FP | **FP** (weakest label) | `reserve`'s docstring has no `raises` clause and B's caller catches both types. The prior exception type is *observable* history, not documentation — this is the case where Rule F and review convention are furthest apart |

V1 totals: TP 5 · FP 3 · FN 0 · TN 2 → accuracy 70%, precision 62.5%, recall 100%.

## V2 · tools

| Variant | Label | Auto | Human | Why |
|---|---|---|---|---|
| P1-a | bug | TP | **TP** | cites `src/rates.py:4` and the missing parameter; the trace shows it read that file |
| P1-b | clean | TN | **TN** | said nothing after 3 calls |
| P2-a | bug | TP | **TP** | 100× unit error, quoting both files |
| P2-b | clean | TN | **TN** | correctly recognised the identity conversion this time (the v1 run did not) |
| P3-a | bug | TP | **TP** | mutation is observable in A |
| P3-b | clean | FP | **FP** (debatable) | same judgement as V1, i.e. reading the caller did not change the verdict |
| P4-a | bug | TP | **TP** | anchors on `accept`'s docstring, a genuine documented contract |
| P4-b | clean | TN | **TN** | correctly silent — `precheck` already rejects the boundary |
| P5-a | bug | TP | **TP** | uncaught `LookupError` |
| P5-b | clean | FP | **FP** (weakest label) | same judgement as V1 |

V2 totals: TP 5 · FP 2 · FN 0 · TN 3 → accuracy 80%, precision 71.43%, recall 100%.

## Auto vs human agreement

Automated scoring and this adjudication agree on **20/20 variants** for this run.
That agreement is weaker evidence than it looks:

* the same person designed the cases, wrote the signatures and adjudicated;
* P3-b, P4-b and P5-b are genuine judgement calls, and a reviewer applying
  "input mutation is always a defect" or "changing an exception type is always
  breaking" would turn three FPs into FNs and change both methods' scores;
* the keyword screen cannot express that nuance — which is why the verdicts are
  recorded here and in `human-review-adjudicated.csv`, not just in the summary
  table.

## What changed versus the v1 Dev run

| | v1 dataset | v2 dataset |
|---|---|---|
| V1 accuracy / pair consistency | 50.00% / 0% | 70.00% / 40% |
| V2 accuracy / pair consistency | 70.00% / 40% | 80.00% / 60% |
| V2 clean false-alarm rate | 60% | 40% |
| V2 effective tool hit rate | 96.43% | 71.43% |

Four changes, mixed together — which is why no causal claim is made:

1. **Dataset redesigns** (P2, P3) removed the self-contradictions. V1's pair
   consistency rising from 0% to 40% is the clearest signal that the *pairs* got
   fairer, not that the model got better: a diff-only reviewer can now be right on
   both variants wherever the diff itself is unambiguous.
2. **Prompt** now states the documented-contract rule for both methods; V1's
   completion tokens jumped (6,690 → 26,148 over 10 calls) and latency rose
   (4.70 s → 16.96 s), consistent with longer, more explicit answers.
3. **V2's tool budget bit harder**: 42 calls, 7 budget denials, 4 errors. Its
   effective hit rate fell to 71.43% while its prompt tokens rose to 48,296.
   `read_code_context` is limited to `src/**/*.py` in the reviewed commit, so
   attempts outside that scope are refused — the denials are that guard working.
4. **Run-to-run variance**: V1 flagged P1-a this run and P2-a again, neither of
   which it flagged in the v1 run. Same model, different samples.

Net: V2 still has not demonstrated a capability advantage. Its pair consistency
(3/5) has a one-sided binomial p = 0.5 against a coin flip, and its remaining two
errors are *judgement* errors after reading the context, not retrieval failures.
