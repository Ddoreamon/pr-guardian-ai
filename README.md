# PR Guardian — Evidence-Based AI Code Review

PR Guardian reviews a Python pull request and produces a report where every
claim is traceable. Deterministic tools (Ruff, pytest) supply evidence, a
language model may propose *suspected* issues, a validator rejects anything it
cannot anchor to the diff, and a human accepts or rejects what remains.

**The design principle: AI proposes, tools prove, validation filters, humans decide.**

![Review results](docs/screenshots/03-results.png)

---

## Live demo vs. measured AI results — please read this first

| | Where | What runs | What it proves |
|---|---|---|---|
| **Online demo** | **https://pr-guardian-ai-uodn.onrender.com** | Ruff + pytest only. **Live model calls are disabled** (`PR_GUARDIAN_ALLOW_LIVE_LLM=0` on the public instance). | The pipeline, the evidence handling, validation and human approval work end to end. |
| **Real AI evaluation** | Committed artefacts in this repo: [held-out report](docs/benchmark-results/heldout-llm/REPORT.md), [raw JSON](docs/benchmark-results/heldout-llm/results.json), [smoke detail](docs/llm-smoke-results.json) | Real `deepseek-flash` calls, 17 frozen held-out cases, run locally. | Measured detection quality (precision / recall / F1 / false-alarm rate). |

The hosted demo deliberately does **not** call a paid model: it is public, so it
would be someone else's bill. Its AI panel therefore says *Not Evaluated*, which
is an honest status, not an error. Every number quoted in the benchmark section
below came from the local run, never from the hosted instance.

> Free Render instances sleep after 15 minutes idle; the first request can take
> about a minute. The demo repository is rebuilt at startup, so nothing is stale
> after a wake-up.

---

## What it does

Given a git diff between two refs, the pipeline:

1. Parses the diff into changed files and new-file line numbers.
2. Runs Ruff on the changed Python files (JSON output).
3. Optionally runs the trusted test suite on the head revision and records
   pass/fail counts and failing test names.
4. Asks an LLM for behavioural defects, with a strict JSON schema and explicit
   instructions to ignore instructions embedded in code.
5. Validates every finding: the file must exist, the line must fall inside the
   changed lines, duplicates are merged, invalid severities and shell-like
   "fixes" are dropped.
6. Marks each finding `confirmed` (backed by a tool) or `suspected` (model
   hypothesis). A model finding can never mark itself confirmed.
7. Renders Markdown/JSON, and in the web UI lets a human accept or reject each
   finding before export.

### Demo cases in the web UI

| Case | What it shows | Expected outcome |
|---|---|---|
| Boundary defect in discount rule | `>= 100` changed to `> 100` | pytest failure reported as **confirmed** |
| Behaviour-preserving refactor | constants extracted | **no findings** (false alarms would be visible) |
| Prompt-injection comment | comment tells the reviewer to ignore its rules | instruction ignored, real test results still reported |

Ruff does **not** flag the boundary bug. That is the point: behavioural defects
need evidence that linting cannot produce.

| Diff with line numbers | Approval + export |
|---|---|
| ![Diff](docs/screenshots/02-diff.png) | ![Approval](docs/screenshots/04-approval.png) |

---

## Architecture

```
git diff ──► diff parser ──► context + changed line numbers
                                    │
                    ┌───────────────┼────────────────┐
                    ▼               ▼                ▼
                  Ruff           pytest           LLM reviewer
              (confirmed)      (confirmed)        (suspected)
                    └───────────────┼────────────────┘
                                    ▼
                          finding validator
                (file exists · line in diff · dedupe ·
                 no self-confirmation · no shell advice)
                                    ▼
                     Markdown / JSON report
                                    ▼
                        human accept / reject
```

Code layout:

```
src/pr_guardian/   review engine (CLI + library)
  git_reader.py      diff parsing, ref validation, limits
  static_analysis.py Ruff JSON → findings
  test_runner.py     pytest on a head worktree (trusted repos only)
  llm_reviewer.py    structured model call, schema validation
  validator.py       the trust layer
  pipeline.py        orchestration shared by CLI and web
  settings.py        env/.env configuration
app/               FastAPI + Jinja2 web UI (templates, static, store, export)
benchmark/         37 labelled PR cases, evaluator, ground-truth verifier
tests/             99 pytest tests
scripts/           browser acceptance suite, real-LLM smoke test
```

---

## Quick start

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"    # Windows
# uv pip install --python .venv/bin/python -e ".[dev]"          # macOS/Linux

cp .env.example .env        # optional: add an API key to enable the AI step

# web UI
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8765
# open http://127.0.0.1:8765

# CLI on the bundled demo repository
.venv/Scripts/python.exe demo/build_fixtures.py ../demo-repo
.venv/Scripts/python.exe -m pr_guardian.cli review \
    --repo ../demo-repo --base main --head feature/boundary-bug --run-tests
```

The web UI works fully **without** an API key: it shows real Ruff and pytest
results and labels the AI panel *Not Evaluated*.

---

## Configuration

Environment variables (or a `.env` file in the working directory):

| Variable | Meaning | Default |
|---|---|---|
| `PR_GUARDIAN_API_KEY` | key for an OpenAI-compatible endpoint | empty → AI step not evaluated |
| `PR_GUARDIAN_BASE_URL` | provider base URL (`PR_GUARDIAN_API_BASE` also accepted) | `https://api.openai.com/v1` |
| `PR_GUARDIAN_MODEL` | model name | `gpt-4o-mini` |
| `PR_GUARDIAN_PUBLIC_MODE` | `1` on a public deployment | `0` |
| `PR_GUARDIAN_ALLOW_LIVE_LLM` | allow real calls while in public mode | `0` |
| `PR_GUARDIAN_ALLOW_TEST_EXECUTION` | `0` disables pytest entirely | `1` |
| `PR_GUARDIAN_MAX_LIVE_LLM_CALLS` | hard budget per process | `50` |

Any provider exposing `POST {base_url}/chat/completions` with
`response_format: {"type": "json_object"}` works. If your provider does not
support JSON mode, the response will fail schema validation and the app reports
"LLM output failed schema validation" rather than inventing findings.

---

## Benchmark

**37 original cases** written for this project: 25 real defects and 12 clean
pull requests, across boundary conditions, null handling, exception handling,
state updates, logic branches, API validation and data filtering. Split 20 dev /
17 held-out; held-out is never used to tune prompts.

Ground truth is verified mechanically — every label is checked by running that
case's own tests:

```bash
python -m benchmark.verify_ground_truth   # 37 cases, 0 mismatches
```

Evaluation rules are fixed before measuring:

* Methods see the diff and source file only. **The ground-truth test file is
  removed from the diff** unless a method is explicitly named `+tests`.
* **PR-level**: a PR is "flagged" if the method reports anything on the changed
  module. **Finding-level**: a finding is a true positive only if it lands on a
  changed line; one defect matches at most once.
* Style lints (`F841`, `E501`, …) are declared *incidental* up front and never
  counted as detecting a behavioural defect.
* Undefined ratios (zero denominators) are reported as `n/a`, never as 0.

Method definitions:

| Method | What it is |
|---|---|
| `ruff-only` | Ruff static analysis only. |
| `llm-only` | **Raw** model output. The validator is *not* applied, so this measures the model alone. |
| `combined` | Ruff + model + validator — what the shipped pipeline actually reports. |
| `combined+tests` | Combined plus pytest evidence. Reported separately because the test file is visible to the method in this condition. |

### Measured results

Model: **`deepseek-flash`** via `https://api.deepseek.com`, `temperature=0`.
Held-out evaluation (17 frozen cases, never used for prompt tuning):

| Method | Split | Cases (bug/clean) | TP | FP | FN | Precision | Recall | F1 | Clean false-alarm | Location accuracy |
|---|---|---|---|---|---|---|---|---|---|---|
| Ruff-only | dev | 20 (17/3) | 0 | 0 | 17 | n/a | 0.00% | n/a | 0.00% | 0.00% |
| Ruff-only | heldout | 17 (8/9) | 0 | 0 | 8 | n/a | 0.00% | n/a | 0.00% | 0.00% |
| LLM-only | heldout | 17 (8/9) | 7 | 1 | 1 | 87.50% | 87.50% | 87.50% | 11.11% | 87.50% |
| Combined | heldout | 17 (8/9) | 8 | 1 | 0 | 88.89% | 100.00% | 94.12% | 11.11% | 100.00% |

Token usage (provider-reported) and schema validity for the held-out run:

| Method | LLM calls | Schema-valid | Validator dropped | Prompt tokens | Completion tokens | Mean model latency |
|---|---|---|---|---|---|---|
| LLM-only | 17 | 17/17 (100%) | 0 | 5,423 | 6,916 | 3.00 s |
| Combined | 17 | 17/17 (100%) | 0 | 5,423 | 8,405 | 3.40 s |

Every detection was checked by hand against the injected defect, e.g. combined
reported *"Discount threshold changed to >= 101 on a float value, excluding totals
between 100 and 101"*, *"Port 0 is now accepted as valid"*, and *"Retry loop
hardcoded to a single iteration, ignoring the `attempts` parameter"* — each
matches its case's ground truth.

**Cost** is not reported as a number: the provider returns tokens but not price,
and no price table was configured. Set `PR_GUARDIAN_PRICE_INPUT_PER_M` and
`PR_GUARDIAN_PRICE_OUTPUT_PER_M` to have the tool compute it. Guessing a price
would make the figure unverifiable.

### Failure cases (held-out)

| Method | Case | Type | What happened |
|---|---|---|---|
| LLM-only | DISCOUNT-04 | miss | threshold moved to `>= 101` was not reported |
| LLM-only | INVENTORY-04 | false alarm | flagged a refactor as "two separate `stock.get` reads" — style, not a defect |
| Combined | RANKING-03 | false alarm | claimed the `n <= 0` → `n < 0` change "no longer hits the early-return guard", but `ordered[:0]` already returned `[]` for `n = 0`, so behaviour is identical |

Dev smoke (5 cases, `scripts/llm_smoke_test.py`): 3 buggy cases → 2 detected;
2 clean cases → 1 false alarm; schema valid 5/5; 4,891 tokens. The dev false
alarm was a plausible-but-unverified concern about `casefold()` Unicode folding;
the dev miss was a removed `None` guard.

### Honest caveats about these numbers

* **Run-to-run variance is real.** An earlier held-out run (before per-case
  capture was added) measured LLM-only at TP 8 / FP 1 / FN 0 and Combined at
  TP 8 / FP 1 / FN 0 — i.e. 88.89% precision, 100% recall. The difference between
  the two runs is sampling noise, not a change in method. Treat ±1 case as the
  noise band at this sample size.
* **Ruff and the validator added nothing on held-out in these runs.** Ruff
  produced zero findings on the behavioural defects, and the validator dropped
  zero findings. So the gap between LLM-only and Combined here is not evidence
  that validation improves precision — with n=17 and this noise level, it is not
  measurable. The validator's value is demonstrated by unit tests (self-confirmation,
  hallucinated files, forbidden fix advice), not by this benchmark.
* **The same diff can produce different AI output across runs.** In one recorded
  web-UI run on the demo boundary case the model flagged the defect; in another it
  reported nothing. This is why model findings are labelled *suspected* and require
  human acceptance.
* **The dataset is small and synthetic** — 37 hand-written cases over small
  modules. It measures behavioural-defect detection, nothing broader.

**How to read the Ruff row.** This dataset was built to test *behavioural*
defects, which static linting is not designed to catch, so 0% recall here says
"Ruff cannot find this class of bug" — not "Ruff is useless for code review".
Ruff remains valuable for the style, import and hygiene problems it does catch;
the two incidental lint hits found in the dev split were correctly excluded from
detection scoring.

Precision is `n/a` for Ruff-only because it raised no findings at all; a ratio
with an empty denominator is undefined, and reporting 0% or 100% would be
misleading.

### Reproducing

```bash
# 1-case live check, then a cost-capped 5-case smoke on the dev split
python scripts/llm_smoke_test.py --limit 1
python scripts/llm_smoke_test.py --limit 5

# frozen held-out evaluation (34 model calls)
python -m benchmark.evaluate --split heldout --methods llm-only,combined \
    --out docs/benchmark-results/heldout-llm
```

Artefacts: [`docs/benchmark-results/heldout-llm/`](docs/benchmark-results/heldout-llm)
(report, per-case CSV, raw JSON) and
[`docs/llm-smoke-results.json`](docs/llm-smoke-results.json).

---

## Testing

```bash
python -m pytest -q                      # 99 tests
ruff check src tests demo benchmark app scripts

# browser acceptance suite (needs the server running)
python -m uvicorn app.main:app --port 8765 &
python scripts/browser_test.py           # 42 checks (43 with a live model)
```

| Suite | Count | Covers |
|---|---|---|
| Unit — diff parser, validator, LLM (mocked), metrics, settings, test-runner | 74 | parsing, trust rules, zero-division, schema failures, cost policy |
| API — FastAPI endpoints | 25 | health, cases, diff, review, decisions, exports, error codes |
| Browser — Playwright | 42–43 | real interactions, exports, mobile layout, error banner, live-model mode |

Latest local run: **99 passed**; browser suite **42/42** (no API key) and
**43/43** (live model configured) — the suite adapts to the server's mode.

---

## Security model

**This is a demo, not a hardened service.**

* The web API accepts only a preset `case_id`. It never accepts a filesystem
  path, git URL or ref, so no arbitrary repository is read.
* pytest runs only against the demo repository this project builds itself. A
  subprocess timeout is **not** a sandbox; `PR_GUARDIAN_ALLOW_TEST_EXECUTION=0`
  disables it entirely.
* Git is invoked with argument lists, never `shell=True`; refs are validated and
  leading `-` is rejected.
* LLM output must satisfy a Pydantic schema. Findings are location-checked and
  can never self-confirm. Suggested fixes containing shell-like commands are
  dropped.
* Prompt injection in code comments is treated as data and covered by a test.
  A prompt-level defence reduces but does not eliminate this risk.
* API keys are read from the environment only; they are never logged or included
  in reports.
* Public deployments default to no live model calls. With
  `PR_GUARDIAN_ALLOW_LIVE_LLM=1`, a global call budget
  (`PR_GUARDIAN_MAX_LIVE_LLM_CALLS`) caps spend.

---

## Limitations

* **Measured on one model** (`deepseek-flash`) with 17 held-out cases. That is
  enough to show the pipeline works end to end, not enough to compare models or
  to claim state-of-the-art detection.
* **Run-to-run variance of about one case** at this sample size; a single run's
  precision/recall should not be read as a stable property of the system.
* Cost is not reported as a figure, because no price table was configured.
* The benchmark is small (37 cases). Results are indicative, and the dataset was
  written by the same person who wrote the pipeline — an author bias that real
  PR data would not have.
* The dataset models behavioural defects in small modules; it is not a sample of
  real-world pull requests.
* Reviews are held in memory and lost on restart; there is no database.
* Only single-repository, single-language (Python) diffs are supported.
* The web UI reviews only its three built-in cases.

---

## Known-unbuilt

Web-based GitHub PR import, LangGraph state/interrupts, a database, and posting
comments back to GitHub are **not implemented**. The UI is intentionally scoped to
preset cases.

---

## Deployment (Render)

**Live instance:** https://pr-guardian-ai-uodn.onrender.com (Free plan, manual
Web Service, `pip install -e .` + `uvicorn app.main:app --host 0.0.0.0 --port $PORT`).

`render.yaml` in this repo also encodes the same configuration for Blueprint
deploys: preset cases only, live LLM off, pytest restricted to the trusted demo
repository.

Public-instance environment variables that matter:

| Key | Value | Effect |
|---|---|---|
| `PR_GUARDIAN_PUBLIC_MODE` | `1` | public deployment policy |
| `PR_GUARDIAN_ALLOW_LIVE_LLM` | `0` | no paid model calls, even if a key exists |
| `PR_GUARDIAN_MAX_LIVE_LLM_CALLS` | `25` | hard cap if the flag is ever enabled |
| `PR_GUARDIAN_ALLOW_TEST_EXECUTION` | `1` | runs pytest only on the self-authored demo repo |
| `PYTHON_VERSION` | `3.12.7` | Render needs a fully qualified version |

To enable live model review on the deployed instance you would set
`PR_GUARDIAN_API_KEY` as a secret **and** `PR_GUARDIAN_ALLOW_LIVE_LLM=1`. That is
deliberately left off here, because a public URL spending someone's API budget is
not a demo — it is a liability. The real-model evidence lives in the committed
artefacts instead.

Verify any deployment with:

```bash
curl -s https://<host>/health
# {"status":"ok","llm_status":"not_configured","public_mode":true,
#  "test_execution":true,"demo_repo_ready":true,"demo_repo_error":"","cases":3}
```

`demo_repo_ready` must be `true`; if it is `false`, the build image lacks `git` or
the demo repository could not be created, and the page will say so.

---

## License

MIT (see `LICENSE`).
