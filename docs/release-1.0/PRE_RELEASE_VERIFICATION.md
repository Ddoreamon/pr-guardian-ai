# Release 1.0 — isolated pre-release verification

Executed against `v1.0.0-rc1` in a clean directory cloned from GitHub, on
2026-10-28 (Asia/Shanghai), Windows 11 · Python 3.12.13 · `uv` virtual environment.

No merge, no deploy, no PR closed, no tag moved.

---

## 1. RC tag integrity

| Check | Result |
|---|---|
| `git cat-file -t v1.0.0-rc1` | `tag` (annotated object, not a lightweight ref) |
| Tag object SHA | `f96b5a3df264351c9f1f588a38488f3c4fd85c50` |
| Peeled commit | `868774602a641b4cbbd46276e1b477d0395613cc` |
| `release/1.0` local HEAD | `868774602a641b4cbbd46276e1b477d0395613cc` |
| `origin/release/1.0` | `868774602a641b4cbbd46276e1b477d0395613cc` |
| `git ls-remote --tags origin v1.0.0-rc1` | `f96b5a3d…` → `refs/tags/v1.0.0-rc1` |
| GitHub visibility | tag ref returned by the API (annotated object) |
| Code tested = code tagged | the clean clone of the tag reports `HEAD = 8687746…` and installs `pr-guardian 1.0.0` |

**No discrepancy.** The demo artefacts committed at `8687746` were produced from the
same tree.

## 2. Render auto-deploy risk

### What the repository says

`render.yaml` declares `autoDeploy: true` and **no `branch:` field**, so the blueprint
tracks the repository default branch, which is `main` (`default_branch: main` from the
GitHub API). `startCommand` is `uvicorn app.main:app`, `healthCheckPath` is `/health`,
and the service is a free-tier web service named `pr-guardian-ai`.

### What could not be verified

**The actual Render service configuration is unverified.** This environment has no
Render API token, so the following are unknown:

* whether the running service was created from this blueprint or by hand;
* whether the service currently tracks `main`, `release/1.0`, or another branch;
* whether auto-deploy is currently on for that service;
* whether `PR_GUARDIAN_ALLOW_LIVE_LLM` is still `0` and whether an API key is set;
* whether editing `render.yaml` on a branch has any effect at all (a blueprint only
  applies on sync, and a hand-made service ignores the file entirely).

GitHub gives no corroboration either: `/deployments`, `/environments` and `/hooks` are
all **empty**, so the repo contains no deployment record, no environment protection
rule, and no outbound webhook.

### Consequence

If the service does auto-deploy from `main`, then **merging this release would deploy
production immediately**, with no approval step. That is unacceptable for a release
that has not been signed off.

### Required actions before any merge (do not do them in this round)

1. In the Render dashboard, open the `pr-guardian-ai` service and read its **Settings →
   Build & Deploy → Auto-Deploy** value and its tracked branch. Record a screenshot.
2. If auto-deploy is on, switch it to **Off** (or "Deploy only on manual trigger"), or
   point the service at a non-`main` branch. Do this **before** merging.
3. Optionally align the blueprint: set `autoDeploy: false` and add `branch: main` with a
   comment that production deploy is manual. Understand that changing the file does not
   change the running service until someone syncs the blueprint — so step 2 is the
   actual control.
4. Confirm `ALLOW_LIVE_LLM=0` and a 25-call cap on the live service before the first
   deployment of this build.

## 3. Clean-room gates

Clean clone: `git clone --branch v1.0.0-rc1 --depth 1`, fresh `uv venv --python 3.12`,
`uv pip install -e ".[dev]"` → `pr-guardian 1.0.0` on Python 3.12.13.

| Gate | Command | Result |
|---|---|---|
| Ruff | `ruff check src tests demo benchmark app scripts` | clean |
| Unit + integration | `pytest -q` | **405 passed** |
| API + MCP | `pytest tests/test_api.py tests/test_mcp_protocol.py tests/test_mcp_integration.py -q` | **66 passed** |
| Browser | `browser_test.py --base-url http://127.0.0.1:8771` | **76/76** (rc1), **77/77** on the rc2 fix |
| `/health` | live request | `{"status":"ok","version":"1.0.0", …}` |
| Version metadata | `importlib.metadata` | `1.0.0` |

### Generated-test tree pollution

The unit/API/MCP suites leave the tree clean. The **browser suite modifies tracked
files**: it regenerates `docs/export-{approved.md,sample.json,sample.md}` and
`docs/screenshots/*.png`. Observed `git status --porcelain` after the browser gate:
10 modified tracked files, all generated artefacts. This is a P1 (pre-existing), not a
functional defect, but it means "run the browser suite" is not side-effect free.

## 4. Dependencies and reproducibility

* 49 distributions installed; full list in `constraints-rc1.txt`.
* Direct requirements keep **version ranges** in `pyproject.toml`; `constraints-rc1.txt`
  is a *deployment* snapshot, not a package constraint. `pip install pr-guardian` users
  are not pinned; the RC environment is.
* Recommended reproducible build for production:
  `uv pip install -e . -c docs/release-1.0/constraints-rc1.txt` (or the equivalent
  `pip` form), and regenerate the constraints file only through a reviewed commit.
* Vulnerability scan: OSV `querybatch` over **all 49 installed distributions** —
  **no advisories**. Query time: during this verification run on 2026-10-28;
  raw result in `osv-tree.json`.
* Scope limits: OSV only; platform-specific and yanked-release issues are not covered;
  advisories published after the query date are not covered; `playwright` browsers are
  not audited.

## 5. Demo verification (clean clone)

| Scenario | Mode | status | Findings | Verified | Notes |
|---|---|---|---|---|---|
| A Bug PR (`boundary-bug`) | public mode, no model | `not_run` | 1 static | 0 | pytest fails on the seeded defect; Ruff reports nothing |
| B Clean PR (`clean`) | public mode, no model | `not_run` | 0 | 0 | report says the absence of a finding is **not** proof the change is correct |
| C Partial AI (`boundary-bug`) | local AI, real DeepSeek call, tool budget 1 | `partial` | 2 | 1 | stops early; findings marked incomplete coverage |

Sensitive-data scan over demo JSON/Markdown, the two export samples and the rendered
page: **no API key, no `C:\Users\…`, no `/Users/…`, no `OneDrive`, no environment
dump**. The exported JSON contains SHAs, file paths inside the demo repo and findings
only.

## 6. Blocker found in rc1 and fixed in rc2

The release checklist requires the page to state *"Tests run only against the trusted,
bundled demo repository."* rc1 had no such sentence; it only said "three preset pull
requests on a trusted, self-authored repository", which does not say what pytest is
allowed to touch.

Fixed on `release/1.0-rc2` (`app/templates/index.html` + a browser assertion + an API
test). rc1's tag is **not moved**; rc2 supersedes it.

Gates re-run on the rc2 commit: Ruff clean · `pytest -q` **406 passed** ·
API/MCP **67 passed** · browser **77/77** · `/health` reports `1.0.0`.

## 7. Remaining risks

| ID | Severity | Item |
|---|---|---|
| R-1 | **P0 (process)** | Render service settings unverified; merging `main` may auto-deploy |
| R-2 | P1 | browser suite rewrites tracked artefacts |
| R-3 | P1 | CI is Linux-only; this verification ran on Windows, so both platforms are covered only by manual runs |
| R-4 | P1 | no environment protection rule on GitHub; a deploy gate exists only in Render's settings |
| R-5 | P2 | constraints file is a snapshot, not a hash-locked lockfile; transitive drifts are possible |
| R-6 | P2 | the public instance runs V1; the footer will correctly show an older version until it is redeployed |

## 8. Verdict

* **rc1**: content verified in a clean room; **one release-checklist blocker** (missing
  scope statement) → superseded.
* **rc2**: blocker fixed, gates re-run, ready for the isolated sign-off.
* **Formal release (`v1.0.0`) is NOT authorised by this round.** It requires R-1 to be
  resolved in the Render dashboard and the deployment runbook in
  `DEPLOYMENT_RUNBOOK.md` to be executed with a named approver.
