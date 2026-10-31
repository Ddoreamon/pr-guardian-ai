# PR Guardian 1.1.0rc1 — user guide (local)

This build reviews Python code you give it. **Nothing is uploaded anywhere**: the server
runs on your machine, bound to `127.0.0.1`, and your source never leaves it.

## 1. Get the project

```bash
git clone https://github.com/Ddoreamon/pr-guardian-ai
cd pr-guardian-ai
git checkout v1.1.0-rc1        # the tag for this candidate
```

You need **Python 3.12 or newer**. The launchers check this for you.

## 2. Start it

### Windows

Double-click `start-local.bat`, or run it from a terminal:

```bat
start-local.bat
:: a different port:
start-local.bat 8100
```

If the Python environment is missing the project dependencies, the script **asks first**
(`Install the project dependencies now? [y/N]`) and installs nothing unless you answer
`y`. The equivalent manual command is `python -m pip install -e .[dev]`.

### macOS / Linux

```bash
chmod +x start-local.sh      # once
./start-local.sh
./start-local.sh 8100        # a different port
```

The same dependency prompt applies. Nothing is installed silently.

### What the launcher pins

| Setting | Value | Why |
|---|---|---|
| bind address | `127.0.0.1` | reachable only from this machine; never `0.0.0.0` |
| uploads | **on** | this is a local tool; you asked for it by running the script |
| AI review | off | no model call, no key, no cost |
| public mode | off | no public deployment semantics |
| test execution | on | the three demo cases use their bundled trusted repository |

## 3. Review your own code

Open **http://127.0.0.1:8000/upload** (or the port you chose).

* choose one or more `.py` files, or a single `.zip` containing `.py` files;
* click **Analyse**; the status line shows the elapsed time and, when it finishes, the
  four stage states (`AST ok · Ruff ok · worker ok · AI not_run`);
* findings are grouped by file, each with line range, rule, severity, source
  (`ruff` / `heuristic`), verification (`confirmed` / `suspected`) and, for heuristics, a
  fix suggestion.

Limits: 25 files, 256 KiB per file, 4 MiB total, 8 MiB archive, 20 s analysis budget,
2 concurrent analyses.

## 4. Read a finding

| Field | Meaning |
|---|---|
| severity | `high` / `medium` / `low`, assigned by the rule, not by a model |
| rule | Ruff code (e.g. `F401`) or `UP00x` for the built-in patterns |
| source | `ruff` = linter, `heuristic` = pattern match, `syntax` = parse failure |
| verification | `confirmed` = a deterministic tool reported it; `suspected` = a heuristic |
| fix | a concrete suggestion where one is known |

`confirmed` means *the rule fired*, not *the code is broken*. `suspected` means *look at
this*, not *this is a defect*.

## 5. Accept, reject, export

* **Accept / Reject** each finding on the demo pages; the decision is stored with the
  review and reflected in the export summary.
* **Export Markdown** and **Export JSON** download the report; the JSON is the machine
  view, the Markdown is for a pull-request comment.
* Exports contain the file paths inside your upload, the findings and the analysis state.
  They do **not** contain your source text, credentials or host paths.

## 6. Stop it

Press `Ctrl+C` in the terminal, or close the window. The server stops; nothing is left
running in the background and no data is retained beyond the process (the review store is
in memory).

## 7. What this tool cannot do

* It does **not** find all logic bugs. It parses code, runs Ruff and matches a small set
  of explainable patterns. A clean report is not proof of correctness — the page says so.
* It does **not** execute your code, run your tests, or install your dependencies.
* It does **not** understand your intent, your data or your domain.
* Heuristics are deliberately few; missing a pattern is expected.
* Findings are rule-level: the fix suggestion can be correct while the surrounding code
  still has a problem.

## 8. Privacy

* The server binds `127.0.0.1`. No inbound connection from another machine is possible.
* Uploaded files are written to a temporary directory for analysis and deleted
  immediately afterwards; the report keeps paths and findings, not source text.
* AI review is off in this launcher. If you enable it yourself, your code (or an excerpt)
  is sent to the model provider you configure — that is your choice, not a default.
* The launcher does not change system environment variables, does not start on boot, and
  does not send telemetry.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| "Python 3.12 or newer is required" | install 3.12+, or run the script with a 3.12 interpreter on `PATH` |
| "Missing Python packages" then a prompt | answer `y`, or run `pip install -e .[dev]` yourself |
| port already in use | `start-local.bat 8100` / `./start-local.sh 8100` |
| page shows "Upload analysis is disabled" | you launched uvicorn manually without `PR_GUARDIAN_UPLOAD_ENABLED=1`; use the launcher |
| "Analysis incomplete" | one stage failed (Ruff timeout/unavailable, or a file could not be parsed); findings shown are still real, coverage is not complete |
