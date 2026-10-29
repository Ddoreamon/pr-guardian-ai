# Release 1.0 — Final approval checklist

One row per step. **Nothing below has been executed.** Steps 3, 5, 7 and 9 require
explicit human authorisation and must not be run by an agent.

Subject: merge `release/1.0-approval` @ `23b4652a2788fb22e88e375f8cd1ce57d4f3048c`
(tree `3412856…`); `v1.0.0-rc2` = `812a3129…` is the verified content ancestor;
`main` = `8a8a84c…`.

Roles: **O** = operator (runs it), **A** = approver (signs it, may be the same person
except for step 5 and step 7, where a second person is required).

---

## Step 0 — Release preconditions (O, A)

| | |
|---|---|
| Action | Confirm the candidate is the approved one |
| Command | `git fetch --all --tags && git rev-parse release/1.0-approval` and `git rev-parse origin/main` |
| Expected | `23b4652a2788fb22e88e375f8cd1ce57d4f3048c` and `8a8a84cc57d351c282d727c68d09705d619cc47f` |
| Evidence | command output pasted into the release ticket |
| On failure | **STOP**. The branch moved; re-verify before anything else. |

## Step 1 — Tag/commit consistency (O, A)

| | |
|---|---|
| Action | Verify the annotated tag object and its peeled commit |
| Command | `git cat-file -t v1.0.0-rc2 && git rev-parse v1.0.0-rc2 && git rev-parse v1.0.0-rc2^{commit}` |
| Expected | `tag`; annotated object `8dde3edc9a5d436a256e381f4cf52c39b2768bfe`; commit `812a3129…` |
| Evidence | output + `git ls-remote --tags origin v1.0.0-rc2` |
| On failure | **STOP**. Do not merge a tag you cannot resolve. |

## Step 2 — Render service state, read-only (O, A)

| | |
|---|---|
| Action | Open Render dashboard → `pr-guardian-ai` → Settings; record the fields in `RENDER_FIELDS` below |
| Expected | documented values, not assumed ones |
| Evidence | screenshot of Settings (Build & Deploy) and Environment panels |
| On failure | if the panel cannot be read, mark **UNVERIFIED** and stop before step 5 |

### RENDER_FIELDS (fill in; do not guess)

| Field | Observed value | Status |
|---|---|---|
| Service name | | |
| Tracked branch | | |
| Auto-Deploy | | |
| Last deployed commit | | |
| `PR_GUARDIAN_PUBLIC_MODE` | | |
| `PR_GUARDIAN_ALLOW_LIVE_LLM` | | |
| `PR_GUARDIAN_ALLOW_TEST_EXECUTION` | | |
| `PR_GUARDIAN_MAX_LIVE_LLM_CALLS` | | |
| API key present (yes/no only, never the value) | | |

## Step 3 — Disable Auto-Deploy (O, A) · **needs authorisation**

| | |
|---|---|
| Prerequisite | step 2 shows the tracked branch is `main` (or is unknown) |
| Action | Toggle Auto-Deploy off (manual deploy only) |
| Verification | re-read Settings: Auto-Deploy = Off; screenshot |
| On failure | **STOP the release.** A merge with auto-deploy on is an unapproved production deploy. |

## Step 4 — Public cost policy check (O, A)

| | |
|---|---|
| Action | Confirm live model calls are off before the new build lands |
| Verify | `curl -fsS https://<host>/health` → `llm_status` is not `ok`; dashboard `ALLOW_LIVE_LLM=0` |
| On failure | set `ALLOW_LIVE_LLM=0` and restart; if the key is present but unwanted, delete it |

## Step 5 — Merge to `main` (O, A) · **needs authorisation**

| | |
|---|---|
| Command | `git switch main && git merge --ff-only origin/main && git merge --no-ff release/1.0-approval -m "release: PR Guardian 1.0.0 (pre-merge audit 23b4652)" && git push origin main` |
| Expected | merge succeeds without conflict |
| Evidence | merge commit SHA |
| On failure | if the merge conflicts or `--ff-only` fails: **STOP**, do not resolve by hand |

## Step 6 — Prove the merge tree equals RC2 (O, A)

| | |
|---|---|
| Command | `git diff --stat release/1.0-approval <merge-sha>` |
| Expected | **empty output** |
| Evidence | the empty diff, plus `git rev-parse <merge-sha>^{tree}` vs `git rev-parse release/1.0-approval^{tree}` — both `341285621365501c3002576a49ee2ce194d61a5e` |
| On failure | `git revert -m 1 <merge-sha>` and stop; do not deploy |

## Step 7 — Manual deploy (O, A) · **needs authorisation**

| | |
|---|---|
| Prerequisite | step 3 done; step 6 empty diff; step 4 green |
| Action | Render → Manual Deploy → Deploy latest commit of `main` |
| Expected | build succeeds; health check `/health` passes; status Live |
| Evidence | deploy log tail, deploy record with its commit SHA |
| On failure | Render → previous deploy → **Redeploy**; record the failure |

## Step 8 — Verify the running build (O, A)

| | |
|---|---|
| Primary source | Render deploy record: the **deployed commit** must equal the merge commit from step 5 |
| Independent check | from a `release/1.0-approval` checkout (the helper is not in the rc2 tree): `python scripts/verify_deployed_build.py --base-url https://<host> --ref <merge-sha> --expect-text "Tests run only against the trusted, bundled demo repository"` |
| Expected | `status: match`, exit 0; `health.version == 1.0.0`; `public_mode: true`; `llm_status: not_configured`; footer `PR Guardian v1.0.0` |
| Demo | run `boundary-bug` (real Ruff/pytest, AI "Not Evaluated") and `clean` (0 findings, wording says absence is not proof) |
| Exports | download JSON + Markdown; grep for `sk-`, `C:\Users`, `/Users/`, `OneDrive` → none |
| On failure | do **not** tag; see `DEPLOYMENT_RUNBOOK.md` §6 |

> The asset fingerprint proves the image was built from a tree containing these assets.
> It cannot distinguish commits that only changed templates or tests, which is why the
> Render deploy record is the primary evidence and the fingerprint is corroboration.

## Step 9 — Create `v1.0.0` (O, A) · **needs authorisation**

| | |
|---|---|
| Prerequisite | steps 0–8 all green on the same commit |
| Command | `git tag -a v1.0.0 -m "PR Guardian 1.0.0" <merge-sha> && git push origin v1.0.0` |
| Expected | annotated tag; peeled commit = merge commit = deployed commit |
| Evidence | `git rev-parse v1.0.0^{commit}` compared with the Render deploy record |
| On failure | if the tag points elsewhere, delete the **new** tag before it is used publicly (see runbook §6.1) |

## Step 10 — Post-release record (O)

| | |
|---|---|
| Action | Attach to the release ticket: RC tag, merge SHA, deploy record, fingerprint output, two demo screenshots, rollback commands rehearsed |
| Expected | one artefact set, no missing evidence |

---

## Stop conditions (any one ends the release before tagging)

1. Step 0 or 1 mismatch, or the tag cannot be resolved.
2. Step 2 fields unreadable → release blocked, not "probably fine".
3. Step 3 cannot be completed → **do not merge**.
4. Step 5 conflict, or step 6 diff not empty.
5. Step 8 deploy record commit ≠ merge commit, or fingerprint mismatch.
6. Any secret or host path appears in a public response.

## Authorisation record

| Step | Authorised by | Time | Signature/thread link |
|---|---|---|---|
| 3 disable auto-deploy | | | |
| 5 merge | | | |
| 7 deploy | | | |
| 9 tag | | | |
