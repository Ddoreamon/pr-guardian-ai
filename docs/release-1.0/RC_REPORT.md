# Release 1.0 — Release Candidate report

This file is the evidence bundle for `v1.0.0-rc1`. It is written on the release branch
and describes the exact commit it is committed in; the tag points at that commit.

## 1. Branch and version

| Item | Value |
|---|---|
| Branch | `release/1.0` |
| Base | `6689645a47d1c067a1b8847ba725474007be3b90` (audited V4.5 tip) |
| Audit commits | `002b041` (audit docs, cherry-picked), `08ba271` (ruff fix) |
| Product version | `1.0.0`, single source `src/pr_guardian/version.py` |
| Version surfaces | `pyproject.toml` (dynamic), package, FastAPI `app.version`, `/health`, page footer, README, CHANGELOG |
| Hybrid (PR #4) | not present: branch is not an ancestor, no dense/hybrid import in `src/` or `app/` |

`main` is untouched at `8a8a84cc57d351c282d727c68d09705d619cc47f`.

## 2. Stacked PR structure

| PR | Head | Base | Status |
|---|---|---|---|
| #1 | `feature/v2-function-calling` | `main` | Draft, CI green |
| #2 | `feature/v2-mcp-server` | #1 | Draft, CI green |
| #3 | `feature/v3-repo-rag` | #2 | Draft, CI green |
| #4 | `feature/v3-hybrid-rag` | #3 | Draft, **excluded from 1.0** |
| #5 | `feature/v4-agent-reliability` | #3 | Draft, CI green |
| #9 | `feature/v4-budget-efficiency` | #5 | **new Draft**, closes audit P0-2 |
| #6 | `feature/v4-evidence-planner` | #9 | Draft, CI green |
| #7 | `feature/v4-evidence-sufficiency` | #6 | Draft, CI green |
| #8 | `release/1.0-audit` | #7 | Draft, audit docs |

No base of an existing PR was changed, nothing was merged or closed.

## 3. Gates on this branch

| Gate | Command | Result |
|---|---|---|
| Lint | `ruff check src tests demo benchmark app scripts` | clean |
| Tests | `pytest -q` | **405 passed** |
| API + MCP | `pytest tests/test_api.py tests/test_mcp_protocol.py tests/test_mcp_integration.py -q` | **66 passed** |
| Browser | `python scripts/browser_test.py --base-url http://127.0.0.1:8766` | **76/76 checks** |
| Version | `pytest tests/test_version.py -q` | **9 passed** (source, packaging metadata, app, `/health`, footer, README, CHANGELOG, no stale strings) |
| Security scan | `python scripts/release_security_audit.py` | 0 secret hits, 0 host-path hits, 0 history hits, 0 export leaks |
| Defaults | runtime assertion | `tool_calls_enabled=False`, `evidence_planner=False`, backend `local`, rounds/calls `3/4`, public mode cannot call a paid model |
| Dependencies | OSV query for installed versions | no advisory for any installed package; floors raised (`jinja2>=3.1.6`, `pytest>=9.1.1`) — see `DEPENDENCIES.md` |

`/health` on the local release build reported `{"status":"ok","version":"1.0.0", ...}`.

## 4. Demo evidence (`docs/release-1.0/demo/`)

| Scenario | Mode | Case | review_status | Findings | Source verified | Head SHA |
|---|---|---|---|---|---|---|
| A — bug PR | public mode (Ruff + pytest, no model) | `boundary-bug` | `not_run` | 1 (static) | 0 | `f9052669230f…` |
| B — clean PR | public mode (Ruff + pytest, no model) | `clean` | `not_run` | 0 | 0 | `8852c5248919…` |
| C — partial AI | local AI mode, real DeepSeek call, tool budget forced to 1 | `boundary-bug` | `partial` | 2 | 1 | `f9052669230f…` |

The demo repository is generated at startup, so its head SHAs are rebuilt on every
run; the stable anchor for these artefacts is the product commit tagged
`v1.0.0-rc1`, and each NOTES.md records the SHAs of its own run. Each folder
contains `report.json`, `report.md` and `NOTES.md` with the base/head SHAs,
the product version and an explicit mode line. C was produced by a real model call; if
no key is available the script records the committed trace instead and says so — it
never fabricates a model response. A and B are exactly what the public deployment can
do: real static evidence with the AI panel marked "Not Evaluated".

## 5. Release Audit P0 closure

| P0 | Status | Evidence |
|---|---|---|
| P0-1 product not on `main` | **open by design** — the release branch exists; merging to `main` needs authorisation | `release/1.0` at this commit |
| P0-2 missing PR for the V4.3 layer | **closed** | Draft PR #9 (`feature/v4-budget-efficiency` → #5) |
| P0-3 version disagreement | **closed** | single source + 9 consistency tests |
| P0-4 README lacks V4 scope / boundaries | **closed** | README rewritten: scope, experiments, claim limits, demo modes |
| P0-5 no release branch / changelog | **closed** | `release/1.0` + `CHANGELOG.md` |

Remaining P1: Windows CI job, research-doc index (partly addressed by README table),
export-sample churn (present: the browser run regenerated `docs/export-*`), statement
about pytest running on the public demo. P2 unchanged.

## 6. Rollback rehearsal

Performed on a scratch branch, then removed:

1. `git switch -c scratch/rollback-rehearsal` from `release/1.0`.
2. Added a commit `52f4185` ("scratch: rehearse a bad release commit").
3. `git revert --no-edit HEAD` → `d7504da`.
4. `git diff --stat release/1.0 HEAD` → **empty**, the tree is identical to the release
   branch.
5. `git switch release/1.0 && git reset --hard release/1.0`, branch deleted.

Deployment rollback (documented in `INTEGRATION_PLAN.md` §7): redeploy the previous
Render build; `PR_GUARDIAN_ALLOW_LIVE_LLM=0` is the immediate cost kill switch. No
database, so no data rollback is required.

## 7. Tag decision

All §3 gates pass on this commit, P0-2 … P0-5 are closed, P0-1 is intentionally left
for the authorised integration step, hybrid is absent, old reports still parse, demo
evidence exists and the rollback was rehearsed.

**Decision: tag `v1.0.0-rc1` on `release/1.0`.** No `v1.0.0`, no merge to `main`, no
Render deploy.

## 8. What still blocks a public 1.0

1. Authorised integration merge into `main` (needs the architecture review to approve).
2. Deployment of the release build, then verification that `/health` and the footer
   report `1.0.0` on the live instance (the current public instance runs V1 and says so).
3. The remaining P1 items, at minimum the Windows CI job and the public-demo wording.

## 9. Reproduction

```bash
git switch release/1.0
uv pip install -e ".[dev]"
pytest -q
ruff check src tests demo benchmark app scripts
python scripts/release_security_audit.py
python scripts/release_demo.py            # offline; add a local key for scenario C
```
