# Release 1.0 — deployment runbook

For the authorised release of `v1.0.0`. **Nothing in this file has been executed.**
The public Render service still runs V1.

Roles: one *operator* (runs the commands) and one *approver* (signs each gate). The same
person may not be both for the production step.

---

## 0. Preconditions

| # | Condition | Verify |
|---|---|---|
| 0.1 | RC verification signed off | `docs/release-1.0/PRE_RELEASE_VERIFICATION.md` §8 |
| 0.2 | Render auto-deploy state known | dashboard screenshot; see §1 |
| 0.3 | public instance still on `ALLOW_LIVE_LLM=0` | Render env panel |
| 0.4 | `main` still `8a8a84cc57d351c282d727c68d09705d619cc47f` | `git ls-remote origin main` |
| 0.5 | release branch contains the RC tag | `git merge-base --is-ancestor v1.0.0-rc2 main` after merge |

## 1. Disable automatic production deploy (mandatory, before merge)

1. Render dashboard → `pr-guardian-ai` → **Settings → Build & Deploy**.
2. Record the current **Auto-Deploy** value and the **branch** it tracks.
3. Set Auto-Deploy to **Off** (manual deploy only). If the service tracks a branch other
   than `main`, still turn auto-deploy off for the release window.
4. Screenshot the panel and attach it to the release ticket.
5. (Optional, repository side) change `render.yaml` to `autoDeploy: false` in the release
   commit **and** sync the blueprint; note that a hand-created service ignores the file,
   so this is not a substitute for step 3.

**Stop here if step 3 cannot be done.** Merging with auto-deploy on means an
unapproved production deploy.

## 2. Merge to `main` (operator, approved)

Preferred: a single integrated merge, because the stack is linear.

```bash
git fetch --all
git switch main
git merge --ff-only origin/main            # ensure a clean main
git merge --no-ff v1.0.0-rc2 \
  -m "release: PR Guardian 1.0.0 (rc2 verified, audited)"
git push origin main
git tag -a v1.0.0 -m "PR Guardian 1.0.0" <merge-sha>
git push origin v1.0.0
```

After the merge, `git diff --stat v1.0.0-rc2..v1.0.0` must be **empty** (no content
change between the verified RC and the release tag).

## 3. Deploy (operator, approved, manual trigger only)

1. Render dashboard → `pr-guardian-ai` → **Manual Deploy → Deploy latest commit**.
2. Watch the build log; the start command is
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/health`.
3. Wait for "Live".

## 4. Post-deploy verification

| Check | Command / action | Expected |
|---|---|---|
| Health | `curl -fsS https://<host>/health` | `status: ok`, `version: 1.0.0` |
| Footer | open `/` | `PR Guardian v1.0.0`, note about the running build |
| Public policy | same JSON | `public_mode: true`, `llm_status: not_configured` (no key deployed) |
| AI honesty | run the `boundary-bug` case | AI panel says "Not Evaluated"; Ruff/pytest evidence real |
| Clean case | run `clean` | no findings; the page says absence of findings is not proof of correctness |
| Export | download JSON and Markdown | no key, no host path, no username |
| No spend | Render logs | no outbound call to the model provider |

If any check fails → §6 rollback.

## 5. Kill switches (any time)

| Control | Effect | Where |
|---|---|---|
| `PR_GUARDIAN_ALLOW_LIVE_LLM=0` | no paid model call is possible; page shows "Not Evaluated" | Render env (and restart) |
| `PR_GUARDIAN_MAX_LIVE_LLM_CALLS=0` | hard refusal after zero calls | Render env |
| `PR_GUARDIAN_TOOL_CALLS_ENABLED=0` | function calling off, no repo reads by the model | Render env |
| Auto-Deploy off | a future `main` push cannot redeploy | Render settings |
| Delete the API key | model calls fail closed | Render env / provider dashboard |

Rotating a leaked key is a provider-side action; the app never logs the key.

## 6. Rollback

### 6.1 Git

| Situation | Action |
|---|---|
| release tag is wrong | `git tag -d v1.0.0 && git push origin :refs/tags/v1.0.0` (only before deploy) |
| merge is wrong, no deploy yet | `git revert -m 1 <merge-sha>` on `main`, push |
| merge is wrong, deployed | revert first, then §6.2 |
| a single PR is the cause | `git revert <merge-sha>`, keep the branch for diagnosis |

Never rewrite `main`; revert instead. `release/1.0*` branches are never force-pushed.

### 6.2 Render

1. Dashboard → **Deploys** → previous successful deploy → **Redeploy** (or "Rollback").
2. Confirm `/health` reports the previous version and the page footer agrees.
3. If the previous build predates the footer, expect no version line — that is correct,
   not an error.

### 6.3 Immediate mitigation without a redeploy

Set `PR_GUARDIAN_ALLOW_LIVE_LLM=0` (cost) and/or redeploy the previous build
(correctness). No database exists, so there is no data rollback; in-process review state
is lost on restart by design.

## 7. Communication

* Before merge: post the RC tag, the verification link and the auto-deploy screenshot.
* After deploy: post `/health` output, the two demo screenshots, and the rollback
  command that would be used.
* On rollback: post the reason, the revert SHA, the deployed SHA, and the follow-up.

## 8. What is explicitly out of scope

No new features, no planner/sufficiency/shadow work, no hybrid retrieval, no history
rewrite, no PR closed without review, no re-running frozen held-out sets.
