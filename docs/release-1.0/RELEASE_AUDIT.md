# PR Guardian Release 1.0 — Release Audit

Read-only audit. No PR was merged, `main` was not touched, Render was not deployed,
no Git history was rewritten, and no paid model call was made.

Audited at `feature/v4-evidence-sufficiency` = `6689645a47d1c067a1b8847ba725474007be3b90`
on 2026-10-28. Machine-readable evidence: `git-inventory.json`,
`pr-inventory.json`, `security-inventory.json` (same directory).

---

## 1. Real Git state

| Ref | SHA | Meaning |
|---|---|---|
| `main` | `8a8a84cc57d351c282d727c68d09705d619cc47f` | V1 only: Ruff + pytest + one-shot LLM review |
| `feature/v2-function-calling` | `14c61082f38a` | PR #1 |
| `feature/v2-mcp-server` | `dd03e0fdc314` | PR #2 |
| `feature/v3-repo-rag` | `9659536aaae4` | PR #3 |
| `feature/v3-hybrid-rag` | `54eb047b0938` | PR #4, **not** in the V4 chain |
| `feature/v4-agent-reliability` | `59f354538b5d` | PR #5 |
| `feature/v4-budget-efficiency` | `8fbb75ff112c` | V4.3 work, **no PR of its own** |
| `feature/v4-evidence-planner` | `a5bde152d352` | PR #6 |
| `feature/v4-evidence-sufficiency` | `6689645a47d1` | PR #7, audit target |

Differences from the task's stated baseline: none for `main` and #1–#7. One thing the
brief did not state and the audit found: **`feature/v4-budget-efficiency` has no PR.**
PR #6 targets it as its base, so that layer of the stack is only reachable through a
branch without a review of its own (see P0-2).

Delta from `main` to the V4.5 tip: **160 files, +38,744 / −33 lines**. Everything
between V1 and V4.5 lives in that delta; `main` today cannot demonstrate tool calling,
MCP, BM25 retrieval, evidence provenance, completeness or any V4 research artefact.

## 2. Stacked PR dependency graph

```
main 8a8a84c
 └─ #1 v2-function-calling 14c6108      (15 commits, 87 files)
     └─ #2 v2-mcp-server dd03e0f        (18 files)
         └─ #3 v3-repo-rag 9659536      (22 files)
             ├─ #4 v3-hybrid-rag 54eb047   (19 files)  ← sibling, dead end
             └─ #5 v4-agent-reliability 59f3545 (36 files)
                 └─ (v4-budget-efficiency 8fbb75f)  ← no PR
                     └─ #6 v4-evidence-planner a5bde15 (12 files)
                         └─ #7 v4-evidence-sufficiency 6689645 (24 files)
```

Every branch except #4 is an ancestor of the next one (`git merge-base --is-ancestor`),
so each PR's diff against its base contains only its own commits. CI is green on all
seven PRs (Lint and tests, Browser UI checks), and all are `mergeable: true`.

**Can be merged as-is:** #1 → #2 → #3 → #5 → (#6, #7) in that order, once the budget
layer has a review (P0-2). Merging #7 brings everything, so merging the earlier PRs is
about review history, not content.

**Must be split or retargeted:** the budget layer. Either open a PR for
`feature/v4-budget-efficiency` (base `feature/v4-agent-reliability`) or retarget #6's
base to `feature/v4-agent-reliability`; leaving it as-is means #6 cannot merge without
an un-reviewed branch.

**Must not enter the Release 1.0 default path:** #4 (Hybrid). Its two commits
(`feat(hybrid)`, `chore(hybrid)`) are **not** ancestors of the V4 chain — the chain
skipped it. Skipping #4 therefore breaks nothing, and cherry-picking it into release
would drag a No-Go experiment into the product.

## 3. Release 1.0 feature matrix

| Capability | Branch | Default at V4.5 tip | Release 1.0 |
|---|---|---|---|
| Git diff parsing | `main` | on | **ship** |
| Ruff static review | `main` | on | **ship** |
| pytest evidence | `main` | on (trusted demo repo) | **ship** |
| DeepSeek structured review | `main` | on when key present and policy allows | **ship** |
| Human accept / reject | `main` | on | **ship** |
| JSON / Markdown export | `main` | on | **ship** |
| FastAPI web UI | `main` | on | **ship** |
| Function calling (read-only tools) | #1 | `tool_calls_enabled=False` | **ship, opt-in** |
| MCP stdio tools | #2 | backend `local` | **ship, opt-in** |
| GitSnapshot pinned SHA | #1 | on whenever tools run | **ship** |
| BM25 repository retrieval | #3 | `search_code` available when tools on | **ship** |
| Evidence provenance (`source_verified`) | #5 | on | **ship** |
| Review completeness (`partial`/`unknown`) | #5 | on | **ship** |
| Dense retrieval, RRF hybrid | #4 only | absent from chain | exclude |
| Evidence planner prompt | #6 | `evidence_planner=False` | keep code, default off |
| Sufficiency gate / shadow replay | #7 | not imported by `app` or `pipeline` | keep code, default off |

The three experimental items are already isolated: the planner is a prompt flag
defaulting to `False`, and `sufficiency.py` / `shadow.py` are imported only by each
other, the offline scripts and the tests — never by `pipeline.py` or `app/`. No code
removal is required for Release 1.0; it is a configuration and documentation question.

## 4. Release blockers

### P0 — must be resolved before a release candidate exists

| ID | Issue | Evidence | Fix |
|---|---|---|---|
| P0-1 | `main` contains only V1; the product cannot be demonstrated from `main` | diff of 160 files | complete the integration merge (see INTEGRATION_PLAN.md) |
| P0-2 | the V4.3 layer has no PR; #6's base points at it | `pr-inventory.json`: no PR with head `feature/v4-budget-efficiency` | open that PR (base #5) **or** retarget #6 to #5 |
| P0-3 | version disagreement: `pyproject.toml` = 0.1.0, FastAPI app = 0.2.0 | `pyproject.toml:3`, `app/main.py:49` | single source of truth, set to 1.0.0 |
| P0-4 | README documents only V1/V2; no section describes V4 capabilities or the experimental boundary | README has no V4 heading | document shipped vs experimental before announcing 1.0 |
| P0-5 | no release branch, tag or changelog | root has no CHANGELOG | create `release/1.0`, tag `v1.0.0` at RC |

### P1 — should be fixed in the RC window

| ID | Issue | Fix |
|---|---|---|
| P1-1 | CI runs Linux only; the project is developed on Windows | a Windows job in `ci.yml` (lint + pytest), keep browser job on Linux |
| P1-2 | `mcp==2.3.0` is pinned exactly; the rest are `>=` | document the pin rationale or loosen with a range |
| P1-3 | V4 research docs (20 files) are not linked from README | an index section with one-line "what this proved" per doc |
| P1-4 | `docs/export-sample.*` are regenerated by tests and churn in diffs | generate into a build dir and copy on purpose |
| P1-5 | `allow_test_execution` is on in the public demo | keep, but state in the UI that it runs the trusted demo repo only |

### P2 — nice to have

| ID | Issue |
|---|---|
| P2-1 | browser checks write screenshots into `docs/screenshots/` on every run |
| P2-2 | no `CHANGELOG.md`; release notes live in commit messages |
| P2-3 | `scripts/` grows research tooling (audit, eval) that is not part of the product |

## 5. Security and compatibility results

### Security — no blocking finding

| Check | Result |
|---|---|
| API key in tracked files | only placeholders: `.env.example` (`PR_GUARDIAN_API_KEY=` empty) and test strings `sk-should-never-be-written`, `sk-never-read` |
| API key in Git history | none (`git log --all -p` scan for `sk-…` found no literal) |
| API key in reports/exports/static pages | none; the child MCP process gets a whitelisted environment without the key |
| Host paths, usernames, `OneDrive` in tracked docs/app/src | none |
| Host paths in exported artefacts under `docs/*.json` | none |
| Tool read scope | `src/**/*.py` allowlist, pinned to the reviewed commit, symlinks and `tests/`, `docs/`, `.env` refused |
| Path traversal / arbitrary file read | refused by `_resolve` plus the allowlist; covered by tests |
| MCP transport | stdio only (`mcp.server.stdio`), no socket or HTTP transport |
| Public Render policy | `PR_GUARDIAN_PUBLIC_MODE=1`, `ALLOW_LIVE_LLM=0`, API key `sync: false`, global call cap 25 |
| Arbitrary repository review | not exposed: `POST /api/reviews` takes a preset `case_id`; no path parameter |
| Dependency risk | `mcp==2.3.0` exact pin; `playwright` is dev-only; no known-vulnerable pin was verified — this audit does not claim an advisory scan |

### Compatibility — passing on the V4.5 tip

| Check | Evidence |
|---|---|
| Legacy `ReviewReport` JSON still parses | `tests/test_review_status.py` legacy migration cases |
| V1 demo works with new fields | full suite 396 passed, including demo/browser tests |
| Function calling and one-shot LLM both work | `tests/test_tool_calling.py`, `tests/test_llm_counts.py` |
| Local vs MCP evidence parity | `tests/test_mcp_integration.py` (identical payloads) |
| No LLM key → Ruff/pytest still run, AI marked not evaluated | `tests/test_ui_states.py`, browser checks |
| `partial` states unfinished, not clean | `tests/test_review_status.py`, 76 browser checks |
| `unknown` / `failed` / `not_run` consistent wording | browser suite covers all three |
| `source_verified` keeps `suspected` + `claim_unjudged` | `tests/test_evidence.py` |
| Experimental flags do not affect defaults | `evidence_planner=False`, `tool_calls_enabled=False`, backend `local` |

## 6. Evaluation claims — external framing limits

| Artefact | What it measures | What it must **not** be called |
|---|---|---|
| V1 synthetic benchmark (37 cases, 17 held-out) | detection on self-authored synthetic repos; Combined P 88.89 % / R 100 % / F1 94.12 % | "accuracy on real code reviews" |
| V2 held-out / cross-file sets | tool-assisted detection on 6 pairs / 12 variants | a statistically significant improvement |
| RAG retrieval benchmark (103 chunks, 51 queries) | retrieval hit rate on one small corpus | a general retrieval quality |
| Dense / Hybrid comparison | a **negative** result, archived | a shipped feature |
| V4.4 six-case A/B | planner reduced calls but lost one required evidence range (No-Go) | evidence that the planner works |
| V4.5 sufficiency profiles (33 / 16) | internal consistency of an offline checker written by the same author | production evidence-sufficiency accuracy |
| "source verified" | the cited lines were delivered to the model | the claim is correct |
| "all CI tests pass" | the test suite passes | the product is accurate |

The negative results (Dense/Hybrid No-Go, planner No-Go, cache No-Go) are preserved in
`docs/v3-*`, `docs/v4.3-*`, `docs/v4.4-comparison.md` and must not be removed or
rewritten for the release.

## 7. Branch integration steps (summary)

Full commands, verification and rollback are in `INTEGRATION_PLAN.md`.

1. Open or retarget the missing budget-layer PR (P0-2).
2. Create `release/1.0` from `6689645` (the V4.5 tip) — not from `main`, so the
   release branch already contains every reviewed commit.
3. Merge the reviewed PRs in stack order for history (#1 → #2 → #3 → #5 → #6 → #7);
   content is identical because the stack is linear.
4. Fix P0-3 (version) and P0-4 (README) on `release/1.0`.
5. Run the full gates there; tag `v1.0.0-rc1`; deploy Render only after approval.

## 8. Rollback strategy

| Level | Mechanism |
|---|---|
| Abort integration | `git checkout release/1.0 && git reset --hard 8a8a84c` (release branch only; business branches untouched) |
| Undo one merge | `git revert -m 1 <merge-sha>` on `release/1.0` |
| Restore previous deploy | Render keeps the previous deploy; redeploy the last good commit, or set `ALLOW_LIVE_LLM=0` to fall back to Ruff+pytest only |
| Kill switch | the public instance already runs with live model calls off; disabling tools is `PR_GUARDIAN_TOOL_CALLS_ENABLED=0` |
| Data | no database; reports are per-process and per-request, so a rollback loses nothing durable |

## 9. Release Candidate acceptance criteria

An RC may be tagged only when all of the following hold on `release/1.0`:

1. P0-1…P0-5 closed; P1 items either closed or explicitly deferred with a note.
2. `ruff check src tests demo benchmark app scripts` clean.
3. `pytest -q` green, including the 66 API/MCP tests and the browser suite.
4. `main` and `release/1.0` differ only by the audited stack (no stray commits).
5. README states the shipped scope, the experimental boundary and the claim limits of
   §6, and links the negative results.
6. Version is `1.0.0` in one place, surfaced by `/health`.
7. Public deployment verified with live model calls **off**: Ruff + pytest evidence,
   AI clearly marked "Not Evaluated", no key in the page or the network payloads.
8. A recorded demo: one bug case and one clean case, with evidence badges and a
   partial/unknown example, reproducible from the tagged commit.
9. Rollback rehearsed once (revert a merge on a scratch branch; redeploy previous).

## 10. Distance to Release 1.0 — the short answer

The engineering is essentially there; the **release mechanics** are not. What is
missing, in order:

1. a reviewed PR for the V4.3 layer (P0-2), otherwise the stack cannot be merged in
   review order;
2. a release branch plus the integration merge, so `main` can demonstrate the product
   (P0-1);
3. one version number and a README that says what shipped, what is experimental and
   what the numbers actually measure (P0-3, P0-4);
4. a demo script and rollback rehearsal on the tagged commit;
5. optional but recommended: a Windows CI job, since the project is developed there.

No P0 item requires new AI capability, new retrieval work, or any change to the
research branches. All of them are integration and documentation work.
