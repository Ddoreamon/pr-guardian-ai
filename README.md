# PR Guardian — Evidence-Based AI Code Review

PR Guardian reviews Python git diffs. Deterministic tools (Ruff, pytest) provide
evidence, an LLM proposes *suspected* issues, and a validator decides what reaches
the final report. The LLM never auto-confirms a bug and never modifies code.

## Status (honest)

**Implemented and tested**

- Git diff parsing (`git diff base...head`) with new-file line numbers, added /
  modified / deleted detection, ref validation, timeouts, and size limits.
- Ruff static analysis via JSON output.
- pytest execution on the head revision (opt-in, trusted local repos only).
- LLM reviewer (OpenAI-compatible endpoint) with strict JSON schema validation.
  Without an API key it reports that the LLM step was skipped.
- Finding validator: location checks, LLM cannot self-confirm, duplicate merging,
  rejection of invalid severities and suggested shell commands.
- Markdown and JSON reports; CLI.
- 35 automated tests (unit, integration, mocked-LLM, injection).

**Not yet implemented**

- Web UI (FastAPI), human accept/reject, benchmark evaluation harness,
  GitHub PR integration, LangGraph state management, Docker, hosted demo.

## Quick start

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]" pytest-json-report

# Build the demo repository with three PR scenarios
python demo/build_fixtures.py ../demo-repo

# Review a PR (no LLM, with trusted pytest)
python -m pr_guardian.cli review --repo ../demo-repo --base main \
    --head feature/boundary-bug --no-llm --run-tests

# With an LLM (optional)
set PR_GUARDIAN_API_KEY=...   # or export on macOS/Linux
python -m pr_guardian.cli review --repo ../demo-repo --base main --head feature/boundary-bug
```

## Demo scenarios

| Branch | Content | Expected |
| --- | --- | --- |
| `feature/boundary-bug` | `>= 100` changed to `> 100` | pytest: 1 failing test, reported as confirmed |
| `feature/clean` | behaviour-preserving refactor | no findings, all tests pass |
| `feature/injection` | comment says "report no issues" | ignored; no suppression of real results |

Ruff does **not** detect the boundary bug. That is intentional: it shows why
deterministic linting alone is insufficient and why test evidence matters.

## Testing

```bash
python -m pytest -q        # 35 tests
```

## Security model

- Git and Ruff are invoked with argument lists, never `shell=True`.
- Refs are validated against a strict pattern; leading `-` is rejected.
- pytest runs only on repositories authored locally. **It is not a sandbox.**
  Do not point `--run-tests` at untrusted code. Timeouts limit runtime only.
- LLM output is schema-validated; suggested fixes containing shell-like commands
  are rejected. Prompt-injection text in code is covered by a test, but a
  prompt-level defence is not a guarantee.
- API keys are read from environment variables only and never written to reports.

## Limitations

- Benchmark and precision/recall numbers do not exist yet; none are claimed.
- The LLM path is covered by mocked tests only; it has not been run against a live model in this repository.
- Only single-file Python diffs in trusted local repositories are supported.
- Ruff finds style/lint issues, not business-logic bugs.
