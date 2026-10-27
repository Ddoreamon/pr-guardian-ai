# Benchmark corrections log

Committed benchmark artefacts are treated as historical records: they are **not
rewritten**. When something in them turns out to be wrong, the correction is
recorded here and the affected code or data is fixed forward.

---

## C-1 · Matching rule described as "identical to the main benchmark"

**Where:** `docs/benchmark-results/crossfile-dev/REPORT.md` and
`docs/benchmark-results/crossfile-heldout/REPORT.md` (generated before this fix),
and the generator in `benchmark/evaluate_crossfile.py`.

**What was wrong:** the reports state *"Matching rule is identical to the main
benchmark"*. It is not.

| Benchmark | Matching rule |
|---|---|
| Main 37-case set | a finding matches if it lands on a changed **line** of the changed file |
| Cross-file set | a finding matches if it lands inside a **definition the change touched** (resolved with the AST, so pure deletions are covered) |

**Why it changed:** the line-only rule mislabelled a correct detection. Case
XF-03 removes two lines, so it has no added lines at all; the model correctly
reported "quantity validation removed" and the rule scored it as a false
positive.

**Status:** generator text fixed; the committed reports were left as-is and this
note supersedes that one sentence. Numbers in the two benchmarks were never
directly comparable and should not be summed.

---

## C-2 · Dev case XF-04 was mislabelled as clean

**Where:** `benchmark/crossfile_cases.py`, dev case XF-04.

**What was wrong:** the PR changed the report format string from `{amount:.2f}`
to `{amount:,.2f}` and was labelled *clean*. For totals of 10,000 or more the
thousands separator changes the user-visible string

```
before: "Discount: 2000.00 on 20000.00"
after:  "Discount: 2,000.00 on 20,000.00"
```

so it is not behaviour-preserving and the label was wrong.

**Fix:** XF-04 was replaced (dev revision 2) with a local variable rename that is
identical at every amount, and the case now asserts exact strings at both a
small and a large amount. The old behaviour is recorded as a counterexample in
`tests/test_crossfile.py::test_previous_formatting_change_was_not_clean`.

**Scope:** dev only. The held-out cases XF-05 … XF-10 have never changed, and no
held-out result has been re-run or re-scored.

---

## C-3 · Tool reads came from the working tree

**Where:** `src/pr_guardian/agent_tools.py`, used by `tool_calling.py`.

**What was wrong:** tools resolved paths against the checked-out working tree, so
a tool could read code that differs from the commit being reviewed (a dirty tree
had already been observed during a real run).

**Fix:** tools now read blobs from the specific commit through
`git ls-tree` / `git show` (`GitSnapshot`), and are restricted to `src/**/*.py`
tracked at that commit. Regression tests cover a dirty worktree, a branch switch
during the review, and a committed symlink.

**Status:** fixed. This changed no previously published number: the cross-file
results committed earlier were produced when the worktree happened to equal the
PR commit, but they are not re-run here — see the note in the plan for the new
evaluation.

## C-4 · The random baseline for pair consistency is p(1-p), not 2p(1-p)

**Where:** `docs/v3-contrast-design.md` section 4.2, the printed `REPORT.md`
header of `docs/benchmark-results/contrast-dev-v2/`, and every binomial p-value
computed with a null of 0.5.

**What was wrong:** the design document claimed that an independent coin-flip
reviewer, flagging each variant with probability p, has pair-consistency
probability `2p(1-p)` - 50% at p = 0.5. That is the probability that the two
predictions *differ*, which includes the case where both are wrong.

A pair is (A = bug, B = clean), so both are correct only when A is flagged and B
is not:

    P(both correct) = P(flag A) * P(not flag B) = p (1 - p)

At p = 0.5 that is **25%**, not 50%. Per-case accuracy remains 50% for any p,
because exactly one of the two answers must be right.

| Observation | Old null (0.5) | Correct null (0.25) |
|---|---|---|
| 2 of 5 pairs correct | p ~ 0.8125 | **p ~ 0.3672** |
| 3 of 5 pairs correct | p ~ 0.5000 | **p ~ 0.1035** |

**Fix applied:** pair consistency is tested against p0 = 0.25; the design
document, report header and tests state the correct formula, and a test derives
p(1-p) from the definition.

**Method comparison:** comparing each method to a random baseline is not a
comparison between the methods. An exact McNemar test over the same variants (and
the pair-level counterpart) is now reported with the discordant counts, computed
only over variants that were actually scored.

**History:** `contrast-dev/` and `contrast-dev-v2/` are not rewritten. Their
p-values used the old null and are superseded by `contrast-dev-v2-rescored/`.


## C-5 · "clean" now has two classes

A clean variant is **unambiguous** only when the diff is a no-op for the changed
function itself, on every input. When the diff does change that function's own
observable behaviour (input mutation, exception type, default value, rounding
rule, accepted scheme, empty-input handling) and the repository merely makes it
harmless, the variant is **context-dependent**: a finding that accurately
describes the change is scored **contested**, never false positive, and the pair
is excluded from pair consistency.

Rationale: the review rule adopted in `docs/v2.2-audit.md` treats documented
contracts *including behaviour established by existing code* as the target. It
would be inconsistent to call an accurate description of a real behaviour change
a hallucination.

| Class | Pairs |
|---|---|
| unambiguous | P1, P2, P4, P7 |
| context-dependent | P3, P5, P6, P8, P9, P10, P11 |

Consequence: the unambiguous subset is 3 dev pairs and **1 held-out pair**, so
held-out pair consistency cannot carry a claim on its own. This is stated in the
readiness report rather than papered over.
