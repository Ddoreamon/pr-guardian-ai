# Release 1.0 — dependency check

Two claims are kept apart here: **what is installed and runs** (checked against the OSV
advisory database) and **what the declared lower bounds allow** a fresh environment to
install.

## Installed environment (checked)

`python scripts/release_security_audit.py` records the environment; the OSV batch query
result is in `osv-advisories.json`.

| Package | Installed | Advisories (OSV) |
|---|---|---|
| mcp | 2.3.0 | none |
| pydantic | 2.14.1 | none |
| fastapi | 0.162.0 | none |
| starlette | 1.10.0 | none |
| uvicorn | 0.54.0 | none |
| jinja2 | 3.1.6 | none |
| httpx | 0.28.1 | none |
| pytest | 9.1.1 | none |
| ruff | 0.17.3 | none |
| python-multipart | 0.0.32 | none |

No P0 dependency vulnerability was found in the installed set.

## Declared lower bounds (found and fixed)

The `pyproject.toml` floors were raised because the old floors allowed vulnerable
versions to be selected on a clean install:

| Requirement | Old | New | Why |
|---|---|---|---|
| `jinja2` | `>=3.1` | `>=3.1.6` | 3.1.0–3.1.5 carry GHSA-cpwx-vrp4-4pq7, GHSA-gmj6-6f8f-6699, GHSA-q2x7-8rv6-6q7h and PYSEC-2026-147x; 3.1.6 is clean |
| `pytest` | `>=8` | `>=9.1.1` | GHSA-6w46-j5rx-g56g / PYSEC-2026-1845 affect 8.0–9.0.x; 9.1.1 is clean |

The remaining requirements (`pydantic>=2.7`, `httpx>=0.27`, `ruff>=0.5`,
`fastapi>=0.115`, `uvicorn>=0.30`, `python-multipart>=0.0.9`, `mcp==2.3.0`) were
queried at their floor versions where an exact version exists, and their installed
versions are clean. `mcp` stays pinned exactly because the v2.3 line renamed
`FastMCP` to `MCPServer`.

## What this check does not prove

* OSV is queried over the network; if the API is unreachable the result file records
  `{"status": "unverified", "reason": ...}` and no "no vulnerabilities" claim is made.
* Transitive dependencies of the declared packages were not enumerated individually;
  only the packages above were queried.
* Advisories published after the audit date are not covered.
* `playwright` is a dev-only extra and was not audited.
