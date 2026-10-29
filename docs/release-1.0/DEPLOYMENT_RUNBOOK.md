# Release 1.0 — deployment runbook

For the authorised release of `v1.0.0`. **Nothing in this file has been executed.**
The public Render service still runs V1.

Subject: `v1.0.0-rc2` = `812a3129fff3984f7d584c1ca4aba5277fdd9d18`; `main` =
`8a8a84cc57d351c282d727c68d09705d619cc47f`.

Roles: one **operator** (runs the commands) and one **approver** (signs each gate). For
the merge and the deploy the approver must be a second person. Per-step evidence and
stop conditions are in `FINAL_APPROVAL_CHECKLIST.md`; this file is the procedure.

---

## 1. Order of operations (the only approved order)

1. Confirm the RC2 tag and the candidate commit agree.
2. Read the real Render settings: tracked branch and Auto-Deploy state.
3. Turn **Auto-Deploy off** and screenshot it.
4. Confirm public live-model calls are off (`ALLOW_LIVE_LLM=0`).
5. Merge to `main` **after explicit authorisation**.
6. Prove the merge commit's tree is identical to RC2.
7. Deploy manually **after explicit authorisation**.
8. Verify the running version, the deployed commit, public mode and the demos.
9. Create `v1.0.0` **after explicit authorisation**, only if 1–8 are green on the same
   commit.

Steps 3, 5, 7 and 9 are human actions. An agent must not perform them.

## 2. Preconditions

| # | Condition | Verify |
|---|---|---|
| 2.1 | RC verification signed off | `docs/release-1.0/PRE_RELEASE_VERIFICATION.md` §8 |
| 2.2 | Render service settings observed, not assumed | `FINAL_APPROVAL_CHECKLIST.md` step 2 |
| 2.3 | public instance still on `ALLOW_LIVE_LLM=0` | Render env panel |
| 2.4 | `main` still `8a8a84c…` | `git ls-remote origin main` |
| 2.5 | RC2 resolves to `812a3129…` | `git rev-parse v1.0.0-rc2^{commit}` |

## 3. Render settings to record before touching anything

Record each field verbatim; "unknown" is a valid answer and blocks the release.

| Field | Where | Why it matters |
|---|---|---|
| Service name | dashboard header | confirms the right service |
| Tracked branch | Settings → Build & Deploy | whether a `main` merge deploys at all |
| Auto-Deploy | Settings → Build & Deploy | the actual deploy trigger |
| Last deployed commit | Deploys list | the pre-release baseline for rollback |
| `PR_GUARDIAN_PUBLIC_MODE` | Environment | must be `1` on the public instance |
| `PR_GUARDIAN_ALLOW_LIVE_LLM` | Environment | must be `0` |
| `PR_GUARDIAN_ALLOW_TEST_EXECUTION` | Environment | expected `1`, trusted demo repo only |
| `PR_GUARDIAN_MAX_LIVE_LLM_CALLS` | Environment | expected `25` |
| API key present | Environment | record **yes/no only**, never the value |

`render.yaml` says `autoDeploy: true` with no `branch:` field, which would follow
`main`. That is a **file**, not observed service state, and a hand-created service
ignores it. Only the dashboard (or the Render API) settles it.

## 4. Disable Auto-Deploy (mandatory before merge)

1. Render → `pr-guardian-ai` → Settings → Build & Deploy.
2. Set Auto-Deploy to **Off** (manual trigger only).
3. Re-read the panel and screenshot it.
4. Optional repository alignment: `autoDeploy: false` in `render.yaml` plus an explicit
   `branch: main`. This only takes effect on a blueprint sync; it is **not** a substitute
   for step 2.

If step 2 is impossible → **stop**. Merging with auto-deploy on ships to production
without approval.

## 5. Merge (authorised)

```bash
git fetch --all --tags
git switch main
git merge --ff-only origin/main
git merge --no-ff v1.0.0-rc2 -m "release: PR Guardian 1.0.0 (rc2 verified, audited)"
git push origin main
```

Then prove the tree:

```bash
git diff --stat v1.0.0-rc2 HEAD          # must print nothing
git rev-parse HEAD^{tree} v1.0.0-rc2^{tree}   # must print the same hash twice
```

A non-empty diff or differing tree hash means the merge introduced something: revert
(`git revert -m 1 <merge-sha>`) and stop. Do not tag a merge you cannot reproduce.

## 6. Deploy (authorised, manual only)

1. Render → `pr-guardian-ai` → **Manual Deploy** → deploy the `main` commit from §5.
2. Watch the build; health check is `/health`, start command
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
3. Wait for **Live**.

## 7. Verify the deployed build — two independent sources

A version string is a claim. Use both:

**7.1 Primary — Render's deploy record.** The Deploys list (and `GET
/v1/services/{id}/deploys` with an API token) records the commit for each deploy. It must
equal the merge commit from §5 and the peeled commit of RC2.

**7.2 Independent — served-content fingerprint.**

```bash
# run this from a clone that has the release tags and the helper script:
# the helper is NOT part of the rc2 tree (it was added on release/1.0-approval),
# so use that branch, or main once the release merge has landed.
git switch release/1.0-approval
python scripts/verify_deployed_build.py \
  --base-url https://<host> \
  --ref v1.0.0-rc2 \
  --expect-text "Tests run only against the trusted, bundled demo repository"
```

The script hashes `/static/app.js` and `/static/style.css` as served (line endings
normalised) and compares them with the same blobs at the ref, then checks `/health` and
the page text. `status: match`, exit 0.

Limits, stated so nobody over-reads it: static assets identify the **image**, but a
commit that only changed templates or tests hashes the same. The deploy record is what
names the commit; the fingerprint catches a stale image, a wrong branch, or a build that
did not pick up the merge.

**7.3 Functional checks** on the live instance: `/health` → `version 1.0.0`,
`public_mode true`, `llm_status not_configured`; footer `PR Guardian v1.0.0`; run
`boundary-bug` (real Ruff/pytest, AI "Not Evaluated") and `clean` (0 findings, and the
page says absence of findings is not proof of correctness); download JSON + Markdown and
confirm no key, no host path, no username.

If any of 7.1–7.3 fails → do not tag; see §9.

## 8. Create the formal tag (authorised)

```bash
git tag -a v1.0.0 -m "PR Guardian 1.0.0" <merge-sha>
git push origin v1.0.0
```

Verify `git rev-parse v1.0.0^{commit}` equals both the merge commit and the commit in
the Render deploy record, then attach the evidence set to the release ticket.

## 9. Rollback, by stage

### 9.1 Before the merge

Nothing to roll back. Close the release ticket; branches and tags stay as they are.

### 9.2 Merged, not yet deployed

```bash
git revert -m 1 <merge-sha>     # keeps history, no rewrite
git push origin main
```

The RC tags continue to exist. Do not deploy. Document why in the ticket.

### 9.3 Deployed, something is wrong

1. Render → Deploys → the previous successful deploy → **Redeploy**.
2. Confirm `/health` and the footer report the previous version.
3. Then revert on `main` (§9.2) so the next deploy does not repeat the fault.
4. If the previous build predates the footer, the absence of a version line is correct,
   not a failure.

### 9.4 Cost or model emergency (no redeploy needed)

| Control | Effect |
|---|---|
| `PR_GUARDIAN_ALLOW_LIVE_LLM=0` | no paid model call is possible; page shows "Not Evaluated" |
| `PR_GUARDIAN_MAX_LIVE_LLM_CALLS=0` | hard refusal after zero calls |
| `PR_GUARDIAN_TOOL_CALLS_ENABLED=0` | no tool reads by the model |
| Auto-Deploy off | no future push can redeploy |
| delete the provider key | calls fail closed |

Rotate a leaked key at the provider; the app never logs it.

### 9.5 After `v1.0.0` exists and has been used

A public release tag is **immutable**: never move or delete it. Ship a patch release
`v1.0.1` from a new commit (same procedure, steps 0–9, with `--ref v1.0.1`), and mark the
old tag as superseded in the release notes. Deleting a tag that users may have built
from destroys reproducibility.

## 10. Communication

* Before merge: RC tag, verification link, auto-deploy screenshot.
* After deploy: deploy record, fingerprint output, two demo screenshots, the exact
  rollback command.
* On rollback: reason, revert SHA, deployed SHA, follow-up owner.

## 11. Out of scope

No new features, no planner/sufficiency/shadow work, no hybrid retrieval, no history
rewrite, no PR closed without review, no frozen held-out re-runs.
