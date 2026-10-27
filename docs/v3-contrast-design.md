# V2.2 contrast-pair benchmark — corrected design and statistics

Supersedes the case table in `v3-crossfile-plan.md` (that file remains as the
earlier proposal). Nothing here has been run: this is the design that the
verifier enforces before any model call.

---

## 1. Design review of the first proposal — what was wrong

| Pair | Problem found | Verdict |
|---|---|---|
| P1 | "`ALLOW_EXACT_THRESHOLD = True/False`" was a **flag nothing read**. Correctness would have rested on a textual assertion, which the task forbids. | redesigned |
| P2 | A pure deletion with a "the guard was redundant" claim: the claim was not executable — no code proved the guard redundant. | redesigned |
| P3 | "`net_total` returns total×100" made the bug trivially visible in the diff's own unit, and the clean variant relied on a comment. | redesigned |
| P5 | "Contract says 3 decimals" was a docstring, not behaviour. | redesigned |
| P6 | "Caller catches KeyError" — but the **base** raises `ValueError`, so the base test would have failed. A clean variant must be satisfied by base *and* PR. | redesigned |
| P4, P7–P11 | Several variants were only clean because an input "never occurs" with no executable proof, and some made base fail. | redesigned |
| all | None asserted that the two variants produce an identical model input. | now enforced |

Two constraints were adopted after this review:

1. **Base must pass in both variants.** A clean variant cannot be created by
   changing the specification; base and PR must both satisfy the same tests.
2. **The diff must be a no-op (or compensated) in B.** "Clean" therefore means
   *observable behaviour of the service is unchanged*, not "a comment says it is
   fine". Every neutralising mechanism is named per pair below and is exercised
   by the variant's own tests.

---

## 2. Neutralising mechanisms

Each pair uses exactly one, and the mechanism is executable code in a file the
diff does not touch:

| Code | Mechanism | Why B is neutral |
|---|---|---|
| M1 | callee accepts the new argument and ignores it (`**options`, unused parameter) | same return value for every input |
| M2 | conversion used by the diff is an identity in B | same return value for every input |
| M3 | consumer of the changed return value already handles the new shape | same observable result |
| M4 | an earlier guard in B already enforces what the diff tightens | changed branch unreachable given the caller |
| M5 | caller catches the new exception type as well, mapping it identically | same observable result |
| M6 | every caller passes the argument explicitly, equal to the new default | default never used |
| M7 | the caller guarantees an input domain in which the change is invisible | same result on that domain |

---

## 3. The 22 cases (11 pairs)

`diff` is byte-identical inside a pair; only the *context* file differs.

| Pair | Split | Diff (same in A and B) | Context difference | A (bug) | B (clean) | Mechanism |
|---|---|---|---|---|---|---|
| P1 | dev | `pricing.py`: `apply_rate(total)` → `apply_rate(total, strict=True)` | `rates.py`: `def apply_rate(total)` vs `def apply_rate(total, **options)` | `TypeError` at call time | extra kwarg accepted and ignored | M1 |
| P2 | dev | `tax.py`: `convert(total)` → `convert(to_cents(total))` | `money.py`: `to_cents` = ×100 vs identity | tax is 100× too large | identical value | M2 |
| P3 | dev | `search.py`: `return []` → `return None` (empty branch) | `api.py`: iterates result vs guards `None` | `TypeError` on empty query | same empty response | M3 |
| P4 | dev | `limits.py`: `if qty <= limit:` → `if qty < limit:` | `validate.py`: no earlier check vs an earlier `qty <= limit` guard | boundary quantity rejected | boundary already rejected earlier | M4 |
| P5 | dev | `stock.py`: `raise ValueError(...)` → `raise LookupError(...)` | `api.py`: catches `ValueError` vs `(ValueError, LookupError)` with the same mapping | uncaught exception escapes | same error response | M5 |
| P6 | held-out | `filters.py`: `limit=None` → `limit=0` | `service.py`: calls `active()` with no argument vs `active(limit=SERVICE_LIMIT)` | "unlimited" becomes "zero results" | argument always explicit | M6 |
| P7 | held-out | `retry.py`: `fetch(url)` → `fetch(url, retries=1)` | `client.py`: honours `retries` vs single-shot client that ignores it | a flaky call stops being retried | unchanged (client retries internally) | M1 |
| P8 | held-out | `units.py`: `ceil(qty)` → `floor(qty)` | `orders.py`: passes fractional quantities vs integer-only quantities | fractional billing unit lost | integers: ceil == floor | M7 |
| P9 | held-out | `pricing.py`: `cfg.get("rate")` → `cfg.get("rate", 0.0)` | `config.py`: key always present vs key optional with documented 0.0 default | a missing key silently becomes free | key always present | M6 |
| P10 | held-out | `urls.py`: `startswith("http://")` → `startswith("https://")` | `ingest.py`: feeds `http://` URLs vs only `https://` URLs | plain-http feed rejected | all inputs already https | M7 |
| P11 | held-out | `stats.py`: `if not items:` → `if items is None:` | `report.py`: may pass an empty list vs guarantees non-empty | empty list divides by zero | never empty | M7 |

Bug/clean balance is 11/11, and each pair contributes exactly one of each.

**Stated limitation (not hidden):** P8, P10, P11 are neutral because of the
callers' input domain (M7), not because the callee ignores the change. If a future
caller violated that domain the change would become a defect. The verifier asserts
the domain by running the variant's tests, and the variant's test file exercises
the boundary of that domain.

---

## 4. Statistics — corrected

Let each variant be a binary prediction ("this PR has a defect" / "not"), and let
the ground truth be balanced (11 bug, 11 clean).

Definitions used from here on, and never mixed:

* **Accuracy** — correct variants ÷ 22. Per-case, chance = 0.5.
* **Precision / Recall / F1** — the usual definitions over *variants*:
  Recall = flagged bugs ÷ 11, Precision = flagged bugs ÷ flagged variants.
* **Clean false-alarm rate** — flagged clean variants ÷ 11.
* **Pair consistency** — pairs where *both* variants are correct ÷ 11. This is a
  different quantity from accuracy and cannot be derived from it.

### 4.1 A deterministic diff-only reviewer

The two variants of a pair present a byte-identical diff, so such a model emits
the same verdict twice. Whatever that verdict is:

* it is right on exactly one variant and wrong on the other → **accuracy = 50%**;
* it therefore never gets both → **pair consistency = 0%**, not 50%.

If it always answers "bug": Recall = 100%, Precision = 50%, accuracy = 50%,
pair consistency = 0%.
If it always answers "clean": Recall = 0%, Precision = 0/0 (undefined → reported
as `n/a`), accuracy = 50%, pair consistency = 0%.

So **50% accuracy is the diff-only baseline, and 0% pair consistency is its
signature.** Any method with pair consistency above 0% has done something other
than reading the diff.

### 4.2 A random reviewer

For a reviewer that flags each variant independently with probability *p*:

* expected accuracy = 50% for any *p* (one of the two answers must be right);
* expected pair consistency = P(flag A) · P(not flag B) + P(not flag A) · P(flag B)
  = 2p(1−p), which is **maximal at 50% when p = 0.5**, and 0 at p = 0 or 1.

Random pair success is therefore **50%, not 0%** — the opposite of the
deterministic case. The two must not be conflated: 0% is evidence of determinism,
not of weakness.

### 4.3 Reading a real result

With 11 pairs, pair consistency under the null "no context used" is 0%, and under
a coin-flip model 50%. A method reporting *k*/11 pairs correct is compared against
the 50% coin-flip null with a one-sided binomial test (n = 11). At 9/11 the
one-sided p-value is about 0.033; at 8/11 about 0.113. **We will report exact
counts and this p-value rather than a bare percentage**, and we will not describe
a difference as an improvement unless it clears that bar and the tool hit rate
supports the mechanism.

---

## 5. What the verifier must assert before any run

1. `diff(A) == diff(B)` byte-for-byte, and the changed-file list is identical.
2. The rendered V1 user prompt is identical for A and B (no case id, label, split,
   variant letter, or path difference leaks in).
3. Base A and base B both pass their tests.
4. PR A fails; PR B passes.
5. The hidden test file never appears in the diff, the prompt, or the tool scope.
6. The label matches business behaviour: the failing assertion in A is on a value
   or exception the service exposes, not on an internal detail.

Any pair failing 1–6 is redesigned and re-frozen **before** a model call. The
frozen set is then versioned; later changes get a new version number and are
logged in `docs/BENCHMARK_CORRECTIONS.md`.
