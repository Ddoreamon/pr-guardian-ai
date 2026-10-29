# Release 1.0 — final pre-merge audit

Read-only audit of the release tree. No merge, no deploy, no tag created or moved, no
history rewrite, no production setting changed.

Audited on 2026-10-29 (Asia/Shanghai) from a **fresh clone** of the remote repository at
the exact approval commit, on Windows 11 · Python 3.12.13 · `uv` virtual environment.

---

## 1. Git state

| Ref | SHA | Note |
|---|---|---|
| `origin/main` | `8a8a84cc57d351c282d727c68d09705d619cc47f` | unchanged, still V1 |
| tag `v1.0.0-rc1` | object `f96b5a3d…` → commit `8687746…` | not moved |
| tag `v1.0.0-rc2` | object `8dde3edc…` → commit `812a3129…` | not moved |
| `release/1.0-approval` | `23b4652a2788fb22e88e375f8cd1ce57d4f3048c` | audit subject |
| `v1.0.0` | — | **does not exist** |

No reference changed since the previous round; no pause condition triggered.

### Commit and tree SHAs

| Object | Commit | Tree |
|---|---|---|
| `main` (V1) | `8a8a84cc57d351c282d727c68d09705d619cc47f` | `b5d59d40118a81157619ed9992dc745621098622` |
| `v1.0.0-rc2` | `812a3129fff3984f7d584c1ca4aba5277fdd9d18` | `e0d6e59b0d613d27db211b4f120fd336af0a251f` |
| approval | `23b4652a2788fb22e88e375f8cd1ce57d4f3048c` | `341285621365501c3002576a49ee2ce194d61a5e` |

`v1.0.0-rc2` **is an ancestor** of the approval commit (`git merge-base --is-ancestor`
→ true). The approval commit is **not** in `main`.

## 2. RC2 → Approval delta (complete)

`git diff --name-status v1.0.0-rc2 release/1.0-approval` → **5 files, all documentation,
one script and one test**:

| File | Change | Classification |
|---|---|---|
| `docs/release-1.0/DEPLOYMENT_RUNBOOK.md` | modified | documentation |
| `docs/release-1.0/FINAL_APPROVAL_CHECKLIST.md` | added | documentation |
| `docs/release-1.0/STATUS.md` | added | documentation |
| `scripts/verify_deployed_build.py` | added | read-only release tooling |
| `tests/test_release_verification.py` | added | test |

Two commits: `c8bea17` (checklist, verifier, rollback stages) and `23b4652` (clean-room
verification record, verifier-location correction, STATUS).

Explicitly checked and **unchanged**:

| Area | Touched? |
|---|---|
| Agent / pipeline (`src/pr_guardian/pipeline.py`, `tool_calling.py`, `evidence.py`, `models.py`) | no |
| LLM client, prompts, planner flag | no |
| MCP server/client | no |
| RAG / BM25 / index | no |
| Settings, budgets, evidence sufficiency | no |
| `render.yaml` | no |
| `pyproject.toml`, dependency set | no |
| Public demo behaviour, templates, static assets | no |
| Secrets, host paths, test residue | none (see §4) |

`git diff --name-only` contains no file under `src/`, `app/`, `demo/`, `benchmark/`, and
no `render.yaml` / `pyproject.toml`.

## 3. Gates on the exact approval commit (fresh clone)

Clean clone → checkout `23b4652` → `uv venv --python 3.12` → `uv pip install -e ".[dev]"`
→ `pr-guardian 1.0.0`. Working tree clean before the gates (`git status --porcelain`
empty).

| Gate | Result |
|---|---|
| `ruff check src tests demo benchmark app scripts` | **clean** |
| `pytest -q` | **408 passed** |
| `pytest tests/test_api.py tests/test_mcp_protocol.py tests/test_mcp_integration.py -q` | **67 passed** |
| `pytest tests/test_version.py -q` | **9 passed** |
| `browser_test.py` | **77/77 checks** |
| `/health` | `{"status":"ok","version":"1.0.0","llm_status":"not_configured", …}` |
| deployment fingerprint | `status: match`, exit 0, expected page phrase present |
| package version | `1.0.0` from `importlib.metadata` |

### Experimental defaults (runtime assertion)

`tool_calls_enabled=False`, `evidence_planner=False`, backend `local`,
`max_tool_rounds=3`, `max_tool_calls=4`, and in public mode `llm_configured=False`
(a paid model cannot be called without an explicit opt-in).

### Tree pollution from tests

Unit, API, MCP and version tests leave the tree **clean**. The **browser suite modifies
14 tracked files**: 11 `docs/screenshots/*.png`, 3 `docs/export-*`, plus
`docs/release-1.0/security-inventory.json` when the audit script is run. This is a
known P1 (R-2); the artefacts were **not** committed and the audit tree was not cleaned
with `git reset --hard`.

## 4. Security and dependencies

Independent, placeholder-aware scan over every tracked text file at this commit:

| Hit | Verdict |
|---|---|
| `tests/test_tool_calling.py:78` `PR_GUARDIAN_API_KEY=sk-leak-me` | test **fixture**: a temp repo's `.env` that the tools must refuse to read |
| `scripts/release_security_audit.py:18` | the scanner's own regex literal |
| `docs/release-1.0/PRE_RELEASE_VERIFICATION.md:117`, `FINAL_APPROVAL_CHECKLIST.md:110` | prose listing what to grep for |
| `docs/release-1.0/security-inventory.json` | the scanner's own previous output |

**No real credential, no host path, no username, no environment dump.** No key is
persisted in reports, exports, screenshots or static assets; the MCP child process gets a
whitelisted environment.

**Finding (tooling, not product):** the *shipped* `scripts/release_security_audit.py` at
this commit is the earlier version. It flags the placeholders above and rewrites its own
output file, so running it prints five "secret" hits and one path hit that are all
self-referential. A placeholder-aware variant exists only in a development working copy
and was lost in an earlier `git reset --hard`; it is **not** in the release tree. This
does not affect the product, but a security script that cries wolf is a quality defect.
Recorded as **R-9 (P2)**; fix in `v1.0.1`, not now, because changing the tree would
invalidate this verification.

Dependencies: the delta adds **no dependency** (`pyproject.toml` unchanged), so the
dependency set is exactly the one verified in `PRE_RELEASE_VERIFICATION.md` §4 — 49
distributions, OSV query over all of them with **no advisories**. Limits unchanged: OSV
only, dependencies of dependencies not enumerated individually, advisories after the
query date not covered.

## 5. Render — manual confirmation (user-provided, not API-verified)

The service settings were checked by the user in the Render dashboard:

| Field | Value |
|---|---|
| Tracked branch | `main` |
| Auto-Deploy | **Off** (screenshot saved) |
| `PR_GUARDIAN_PUBLIC_MODE` | `1` |
| `PR_GUARDIAN_ALLOW_LIVE_LLM` | `0` |
| `PR_GUARDIAN_ALLOW_TEST_EXECUTION` | `1` |
| `PR_GUARDIAN_MAX_LIVE_LLM_CALLS` | `25` |
| `PYTHON_VERSION` | `3.12.7` |

This is a **human confirmation record**. This audit environment has no Render API access
and did **not** query the service directly; the values are not independently verified by
tooling. With Auto-Deploy off, merging `main` no longer triggers a production deploy.

Note the version skew this creates: the live instance runs Python 3.12.7 while this audit
ran on 3.12.13. The project requires `>=3.12`; the release CI also uses 3.12.x. The
deployment verification in §6 checks the served build, not the interpreter patch level.

## 6. Reference relationships

| Check | Result |
|---|---|
| RC2 is an ancestor of approval | **yes** |
| `main` still points at V1 | **yes** (`8a8a84c`) |
| Hybrid (PR #4) in the release tree | **no** (`feature/v3-hybrid-rag` is not an ancestor; no dense/hybrid import in `src/` or `app/`) |
| PR #1–#9 merged | **none** (`merged_at` empty for all; 9 open, 0 closed-unmerged) |
| Formal `v1.0.0` tag | **absent** |
| RC1 / RC2 tags moved | **no** |
| Secrets in commit history | none found in earlier scans; no new commits add any |

## 7. Merge subject and the expected tree — correction

The earlier runbook says "the merge tree must equal the RC2 tree". That is now **wrong**:
approval adds five files, so the approval tree differs from rc2 by design.

* Merge subject: **`release/1.0-approval` @ `23b4652`**.
* Expected post-merge tree: **`341285621365501c3002576a49ee2ce194d61a5e`** (approval
  tree), which is rc2's tree plus the five additive files.
* rc2 remains the verified content ancestor; nothing in it changed.

`DEPLOYMENT_RUNBOOK.md` and `FINAL_APPROVAL_CHECKLIST.md` are corrected on this audit
branch (documentation only, approval commit untouched).

## 8. Risk register

| ID | Severity | Item | Status |
|---|---|---|---|
| R-1 | — | Render settings unverified by tooling | **mitigated**: human record, Auto-Deploy Off |
| R-2 | P1 | browser suite rewrites 14 tracked artefacts | open |
| R-3 | P1 | CI is Linux-only; audit runs on Windows | open (both exercised manually) |
| R-4 | P1 | no GitHub environment protection rule | open (deploy gate lives in Render) |
| R-5 | P2 | `constraints-rc1.txt` is a snapshot, not a hash lock | open |
| R-6 | P2 | live instance runs V1; footer will show an older version until redeploy | expected |
| R-7 | P2 | asset fingerprint cannot distinguish template-only commits | documented |
| R-8 | P2 | runbook said "tree equals RC2"; corrected here | resolved on this branch |
| R-9 | P2 | shipped security scanner reports its own placeholders | open, fix in 1.0.1 |
| R-10 | P2 | Render runs Python 3.12.7, audit ran 3.12.13 | acceptable (>=3.12) |

## 9. Go / No-Go

| Dimension | Verdict | Basis |
|---|---|---|
| **Code** | **GO** | 408 tests, 67 API/MCP, 77/77 browser, ruff clean on the exact commit; delta adds only docs, one read-only script and one test |
| **Security** | **GO** | no real secret or host path in the tree; MCP stdio with whitelisted env; public mode cannot call a paid model; no dependency change; R-9 is a tooling quality issue, not a vulnerability |
| **Release process** | **GO for entering merge approval** | refs stable, rc2 an ancestor, no PR merged, no formal tag, tags unmoved, rollback stages documented and rehearsed |
| **Production deployment** | **CONDITIONAL GO** | deploy only after the merge with the corrected tree check; Auto-Deploy is Off per the human record; post-deploy verification must use the Render deploy record plus the asset fingerprint |

**Recommendation: approve entering the human merge approval.** The remaining items are
P1/P2 quality issues that do not change the product's behaviour and are listed for
`v1.0.1`.

## 10. Post-merge tree verification (exact commands)

```bash
git fetch --all --tags
git switch main
git merge --ff-only origin/main
git merge --no-ff release/1.0-approval -m "release: PR Guardian 1.0.0 (pre-merge audit 23b4652)"
MERGE=$(git rev-parse HEAD)

git rev-parse "${MERGE}^{tree}"          # must equal 341285621365501c3002576a49ee2ce194d61a5e
git rev-parse "release/1.0-approval^{tree}"   # same value
git diff --stat release/1.0-approval "$MERGE" # must print nothing
git merge-base --is-ancestor v1.0.0-rc2 "$MERGE" && echo "rc2 still an ancestor"

# only then
git tag -a v1.0.0 -m "PR Guardian 1.0.0" "$MERGE"
git push origin main v1.0.0
```

If the merge tree differs from `3412856…`, do **not** tag: something was resolved by hand
or the wrong branch was merged.
