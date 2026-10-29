# Release 1.0 — status and handoff

Snapshot taken 2026-10-29. Read this first; it says what is done, what is blocked, and
what a human must do next. Nothing here has been executed against production.

## 1. Where the release stands

| Ref | SHA | State |
|---|---|---|
| `main` | `8a8a84cc57d351c282d727c68d09705d619cc47f` | untouched, still V1 |
| `v1.0.0-rc1` | tag `f96b5a3d…` → `8687746…` | verified in a clean room; **superseded** (missing page scope statement) |
| `v1.0.0-rc2` | tag `8dde3edc…` → `812a3129…` | **current candidate**, verified in a clean room |
| `release/1.0` | `8687746…` | rc1 line |
| `release/1.0-approval` | `c8bea17…` | approval docs + deployment verifier; **not part of rc2** |
| `v1.0.0` | — | **not created** |

PR #1–#9 are all still Draft; none was closed or merged. Render still runs V1.

## 2. Clean-room verification of rc2 (re-run this round)

Fresh clone of the tag on Windows 11, Python 3.12.13, `uv venv` + `uv pip install -e
".[dev]"` → `pr-guardian 1.0.0`.

| Gate | Result |
|---|---|
| Ruff | clean |
| `pytest -q` | **406 passed** |
| API + MCP | **67 passed** |
| Browser UI | **77/77** |
| `/health` | `version 1.0.0` |
| Deployment fingerprint | `status: match`, exit 0 (run from `release/1.0-approval`) |
| Demos A / B / C | `not_run` / `not_run` / **`partial`** (real local model call) |
| Sensitive scan of demos + exports | no key, no host path, no username |
| Tree pollution | unit/API/MCP leave the tree clean; the **browser suite rewrites 12 tracked artefacts** (known P1) |

## 3. What is blocked, and by whom

| # | Step | Blocked by | Who can unblock |
|---|---|---|---|
| 3 | Turn Render Auto-Deploy off (with screenshot) | no Render access from this environment | service owner in the Render dashboard |
| 5 | Merge `release/1.0-rc2` into `main` | requires explicit authorisation | approver |
| 7 | Manual deploy | requires authorisation **and** step 3 done | approver |
| 9 | Create `v1.0.0` | requires steps 1–8 green on the same commit | approver |

Everything else is prepared: procedure in `DEPLOYMENT_RUNBOOK.md`, per-step evidence and
sign-off table in `FINAL_APPROVAL_CHECKLIST.md`.

## 4. Two corrections made this round

1. **The verifier is not in the rc2 tree.** `scripts/verify_deployed_build.py` was added
   after rc2, so step 7.2 must be run from a `release/1.0-approval` checkout (or from
   `main` after the merge). Both the runbook and the checklist now say so. Verified: run
   from the approval branch against a clean-room rc2 server → `status: match`, exit 0.
2. **rc2 was previously only verified in CI and a dev workspace**, not in a clean room.
   It has now been cloned from the tag and verified end to end; see §2.

## 5. Known limitations that survive the release

| ID | Item | Impact |
|---|---|---|
| R-1 | Render service settings unverified (branch, Auto-Deploy, env) | merge may auto-deploy; step 3 is mandatory |
| R-2 | browser suite rewrites tracked artefacts | produces diffs; harmless to the product |
| R-3 | CI is Linux-only; clean-room runs are Windows | both covered manually, not continuously |
| R-4 | no GitHub environment protection rule | the deploy gate exists only in Render |
| R-5 | `constraints-rc1.txt` is a snapshot, not a hash-locked lockfile | transitive drift possible |
| R-6 | public instance runs V1; footer will show the old version until redeploy | expected behaviour, not an error |
| R-7 | the deployment fingerprint cannot distinguish commits that only touched templates/tests | the Render deploy record is the primary commit evidence |

## 6. Exact next actions for the human

1. Open Render → `pr-guardian-ai`; fill the `RENDER_FIELDS` table in
   `FINAL_APPROVAL_CHECKLIST.md` (service name, tracked branch, Auto-Deploy, last
   deployed commit, the four policy variables, API key present yes/no).
2. If Auto-Deploy is on, turn it off and screenshot the panel.
3. Sign the authorisation record for steps 3, 5, 7 and 9.
4. Run the runbook §5 → §8; collect the evidence listed in the checklist.

If step 1 cannot be completed, the release stays blocked. "Probably off" is not an
acceptable substitute for the screenshot.

## 7. Reproduce the verification

```bash
git fetch --all --tags
git rev-parse v1.0.0-rc2^{commit}        # 812a3129fff3984f7d584c1ca4aba5277fdd9d18
git switch release/1.0-approval
pytest -q                                # 408 (includes the verifier tests)
ruff check src tests demo benchmark app scripts
```

Clean room:

```bash
git clone --branch v1.0.0-rc2 --depth 1 https://github.com/Ddoreamon/pr-guardian-ai
cd pr-guardian-ai && uv venv .venv --python 3.12 && uv pip install -e ".[dev]"
pytest -q && ruff check src tests demo benchmark app scripts
```
