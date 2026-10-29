# PR Guardian Release 1.0 — Integration Plan

Companion to `RELEASE_AUDIT.md`. Nothing here is executed in this round; this is the
procedure to follow once the integration is authorised.

Facts this plan relies on:

* the V2→V4.5 stack is **linear** — every branch except `feature/v3-hybrid-rag` is an
  ancestor of the next one (checked with `git merge-base --is-ancestor`);
* `feature/v3-hybrid-rag` is a **sibling** of the chain and is not an ancestor of the
  V4.5 tip, so skipping it removes nothing the later branches import;
* the V4.3 layer `feature/v4-budget-efficiency` (`8fbb75f`) has **no PR** and is the
  base of PR #6.

## 0. Preconditions

| # | Precondition | How to check |
|---|---|---|
| 0.1 | the budget layer is reviewed | a PR exists with head `feature/v4-budget-efficiency`, or #6's base is retargeted to `feature/v4-agent-reliability` |
| 0.2 | no branch moved since the audit | `python scripts/release_inventory.py` still prints the SHAs in §1 of the audit |
| 0.3 | working tree clean | `git status --short` |
| 0.4 | full suite green on the tip | `pytest -q` (396 passed at audit time) |

If §0.2 fails, stop and re-audit: the plan's commit-by-commit expectations no longer
hold.

## 1. Create the release branch

```bash
git fetch --all
git switch --create release/1.0 6689645a47d1c067a1b8847ba725474007be3b90
```

**Why from the V4.5 tip and not from `main`:** the stack is linear, so the tip already
contains every reviewed commit. Branching from `main` would force seven sequential
merges where only the last one changes the tree, and every intermediate step would be
a partially integrated product.

## 2. Integration order

Two acceptable strategies. A is recommended; B keeps the PR history in `main`.

### Strategy A — single integrated merge (recommended)

```bash
git switch main
git merge --no-ff release/1.0 -m "release: PR Guardian 1.0 (V1 -> V4.5, audited at 6689645)"
```

The merge commit records the audit SHA. PRs #1–#7 can then be closed as "merged via
release/1.0" or merged in order for history; either way the tree is identical.

### Strategy B — merge PRs in stack order

Merge in this order, waiting for CI on each: #1 → #2 → #3 → #5 → (#6 after P0-2) → #7.
Do **not** merge #4. After each merge:

```bash
pytest -q && ruff check src tests demo benchmark app scripts
```

Expected result: the tree after #7 equals `git diff release/1.0` (empty).

### Verifying #4 was not needed

```bash
git merge-base --is-ancestor feature/v3-hybrid-rag 6689645  # exit 1 = not contained
git grep -n -i -E "onnx|sentence_transformers|dense|rrf" -- src app  # only comments
```

At audit time the only matches were two comments in `repo_index.py` explaining what
the index is **not**. No import, no dependency, no configuration key.

## 3. Commits to exclude from the release tree

Nothing needs deleting — the experiments are already behind defaults. Excluded from
the **default path**, kept in the tree for reproducibility:

| Item | Where | Default |
|---|---|---|
| Dense / Hybrid retrieval | `feature/v3-hybrid-rag` (not merged) | absent |
| Evidence planner prompt | `tool_calling.PLANNER_GUIDANCE` | `PR_GUARDIAN_EVIDENCE_PLANNER=0` |
| Sufficiency gate | `src/pr_guardian/sufficiency.py` | not imported by `app`/`pipeline` |
| Shadow replay | `src/pr_guardian/shadow.py` | offline script only |
| Cache experiment | not implemented (No-Go) | absent |

**Decision for 1.0:** keep the code and keep it off by default; do not move it to a
research branch. Reason: the offline evaluators and their tests are the evidence trail
for the negative results, and removing them would make the audit unreproducible.
Guard it with a test that asserts the defaults (see §5.3).

## 4. Release-branch changes (P0-3, P0-4)

On `release/1.0`, three small commits:

1. **Version**
   ```bash
   # pyproject.toml: version = "1.0.0"
   # app/main.py: FastAPI(..., version="1.0.0")
   # surface it in /health and the page footer
   ```
   Add a test that `/health` reports the same string as `pyproject.toml`.
2. **README scope** — sections: *What ships in 1.0*, *What is experimental and off by
   default*, *What the numbers measure* (link `docs/release-1.0/RELEASE_AUDIT.md` §6),
   *Architecture* (V1 → V4.5 one line each), *Reproducing the research* (links to the
   negative results).
3. **CHANGELOG.md** — 1.0.0 entry listing the shipped capabilities and explicitly
   naming the No-Go experiments that are **not** part of 1.0.

## 5. Verification gates

Run at every step; all four must pass before tagging.

```bash
# 5.1 static + unit
ruff check src tests demo benchmark app scripts
pytest -q

# 5.2 API and MCP parity
pytest tests/test_api.py tests/test_mcp_protocol.py tests/test_mcp_integration.py -q

# 5.3 defaults are still the release defaults
pytest tests/test_budget_efficiency.py -q      # planner off, budgets unchanged
python - <<'PY'
from pr_guardian.settings import Settings
import inspect
print("tool_calls default:", inspect.signature(Settings).parameters["tool_calls_enabled"].default)
print("planner default:", inspect.signature(Settings).parameters["evidence_planner"].default)
PY

# 5.4 browser UI
python -m uvicorn app.main:app --port 8765 &
python scripts/browser_test.py --base-url http://127.0.0.1:8765
```

Expected at audit time: `396 passed`, `76` browser checks, `66` API/MCP tests.

## 6. Demo and evidence for reviewers

1. Review one **bug** case and one **clean** case from the preset demo repository, with
   tool calling on (`PR_GUARDIAN_TOOL_CALLS_ENABLED=1`) so evidence badges appear.
2. Produce one **partial** review deliberately (lower `PR_GUARDIAN_MAX_TOOL_CALLS` to 1)
   to show that partial is labelled incomplete, not clean.
3. Export both as JSON and Markdown; confirm no host path or key appears.
4. Record the screen or store the artefacts under `docs/release-1.0/demo/` (do not
   overwrite the existing `docs/screenshots/`).

## 7. Rollback

| Situation | Action |
|---|---|
| integration merge is wrong | on `main`: `git revert -m 1 <merge-sha>`; on `release/1.0`: `git reset --hard 6689645` |
| one PR caused a regression | `git revert <merge-sha>` on `release/1.0`, keep the branch for diagnosis |
| CI fails after the merge | do not tag; fix on `release/1.0`, re-run §5, re-tag `-rc2` |
| bad deploy | Render → redeploy the previous successful commit; `PR_GUARDIAN_ALLOW_LIVE_LLM=0` as the immediate kill switch |
| leaked key suspicion | rotate in the Render dashboard, then re-check `python scripts/release_security_audit.py` |

No business branch (`feature/*`) is ever rewritten by these operations.

## 8. When an RC may be created

Tag `v1.0.0-rc1` on `release/1.0` when:

1. P0-1 … P0-5 are closed, with evidence linked in the tag message;
2. §5 gates pass on the exact tagged commit;
3. `main` and `release/1.0` differ by exactly the audited stack;
4. the demo artefacts exist and were produced from that commit;
5. the rollback was rehearsed once on a scratch branch.

Only then: deploy Render with live model calls still **off**, verify the public page
shows Ruff + pytest evidence and "Not Evaluated" for AI, and announce.

## 9. Explicitly out of scope

No new agent features, no V4.6, no retrieval/embedding work, no re-running the frozen
held-out sets, no merging PR #4, no history rewrite, no closed PRs reopened without
review.
