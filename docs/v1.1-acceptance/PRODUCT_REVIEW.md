# V1.1 product review — does upload & review actually help?

Exploratory, not statistical. Three self-authored projects (MIT, same licence as this
repository) were uploaded through the real HTTP API; case A was also driven through the
real browser page. No model was called and no uploaded code was executed.

Raw data: `results.json`. Generated tables: `REPORT.md`. Samples: `samples/`, fixed
versions: `fixed/`.

## 1. What was tested

| Case | Shape | Files | LOC | Upload | Elapsed |
|---|---|---|---|---|---|
| A `csv_report` | CSV → JSON data script, one file | 1 | 27 | single `.py` | 0.2 s |
| B `api` | FastAPI service with validation and error handling | 1 | 34 | single `.py` | 0.2 s |
| C `inventory` | package: model, service, CLI, `__init__` | 4 | 41 | ZIP | 0.3 s |

These are ordinary files written as a developer would write them; they were **not**
assembled to trigger `UP001`–`UP007`. The sample sources are authored for this test and
are committed as fixtures, not as user data.

## 2. Findings, judged one by one

| Case | Findings | Valid | Duplicate rule hit | False positive | Distinct valid issues |
|---|---|---|---|---|---|
| A | 4 | 3 | 1 (B006 = UP006) | 0 | 3 |
| B | 5 | 4 | 1 (E722 = UP005) | 0 | 4 |
| C | 5 | 3 | 1 (B006 = UP006) | 1 (RUF100) | 3 |
| **total** | **14** | **10** | **3** | **1** | **10** |

Denominator: every finding the enabled checks returned. 10 of 14 findings describe a
real, located problem; 3 are the same defect reported by a second rule; 1 is an artefact
(the `# noqa` we deliberately left in the sample is reported as unused in isolated mode).

**These are lint and pattern results, not a bug-detection accuracy measurement.** Ruff
reports rule hits; `UPxxx` reports patterns; neither proves a logic defect.

### What the checks caught that mattered

* C `shell=True` with a caller-controlled path (`UP002`) — a real injection risk, and the
  suggestion (argument list, `shell=False`) is directly actionable;
* B bare `except:` around a dictionary lookup (`UP005`/`E722`) — it also hid the
  `KeyError` that the handler was trying to translate;
* B `BLE001` on the delete handler — catches everything and returns `str(exc)` to the
  client; the reviewer would otherwise not have looked;
* A/C mutable default arguments (`UP006`/`B006`) — the shared list really does accumulate
  across calls;
* unused import and a placeholder-less f-string — real, if minor, cleanup.

### What it missed (kept in the record, not deleted)

* A: `next(reader)` skips the first data row because `DictReader` already consumed the
  header; `float()` on a malformed amount raises an unhandled error.
* B: `quantity` accepts negative values; the delete handler leaks exception text; the path
  parameter is untyped, so every name is accepted as an id.
* C: the CLI ignores the report subprocess exit status.

All three are the kind of defect a human reviewer finds by reading the diff. The tool
does not claim to find them.

## 3. Fix → re-upload

For each case the fixable findings were actually fixed (`fixed/` mirrors `samples/`), then
re-uploaded:

| Case | Disappeared | Remained | Review status after fix |
|---|---|---|---|
| A | F401, F541, UP006, B006 | none | `completed` |
| B | F401, UP005, E722 | I001, BLE001, BLE001-adjacent code untouched | `completed` |
| C | UP002, UP006, B006 | PLW1510 | `completed` |

The round trip works: the same finding does not come back, and the fixes did not
introduce a new finding (the one remaining in C is the `subprocess.run(... check=False)`
we kept on purpose; the finding is correct and we accept it).

## 4. Can a user understand and act on the output?

* Every finding shows file, line range, rule, severity, source (`ruff` / `heuristic`),
  verification (`confirmed` / `suspected`), a one-line summary and, for heuristics, a
  concrete suggestion.
* The page states what ran: `AST ok · Ruff ok · worker ok · AI not_run`, and the limits
  block repeats that uploaded code is never executed.
* The clean case says: *"No issues detected by the enabled checks. This does not prove the
  code is bug-free."*

Subjectively (one reviewer, three files): the suggestions were actionable in the cases
where they existed, and the missing suggestions on Ruff findings are acceptable because
the rule code links to documented behaviour.

## 5. Ruff reliability fix

`_ruff()` previously contained dead code (`if proc.returncode != 0 and proc.returncode != 0:
pass`) and collapsed every failure into one status. It now returns a distinct outcome:

| Status | Meaning | Report effect |
|---|---|---|
| `ok` | the check ran; the list may be empty | `completed` (if AST is whole) |
| `empty` | the check produced no output | `completed` |
| `timeout` | exceeded 8 s and was killed | `partial`, findings kept |
| `unavailable` | the interpreter could not start Ruff | `partial`, findings kept |
| `exit_error` | Ruff exited non-zero | `partial`, findings kept |
| `unparsable` | output was not JSON | `partial`, findings kept |
| `unexpected` | JSON was not the expected list | `partial`, findings kept |

`tests/test_upload_ruff_status.py` pins all seven, plus the rule that every failure keeps
AST/heuristic findings and marks the review `partial` — a failed lint can never read as a
clean review.

A separate real-world defect was found while running this: files written by Windows
editors often start with a UTF-8 BOM, which previously produced a spurious `AST001`
syntax error on perfectly valid code. The analyser now strips the BOM (`uploads._decode`)
and writes normalised text into the work directory; a regression test covers it.

## 6. Security and privacy checks (this run)

| Check | Result |
|---|---|
| uploaded source in the server log | **0** occurrences of source fragments |
| temporary analyser directories left behind | **0** |
| uploaded code executed | never — the analyser only parses and lints |
| pytest on uploaded code | never |
| `pip install` of uploaded deps | never |
| exported report contains credentials | no (no key, no host path) |
| public Render exposes uploads | **no** — `/api/uploads/config` 404, `/upload` 404 |
| paid model called | no — `AI not_run`, `llm_status=disabled_by_request` |

## 7. Release decisions

1. **Does local V1.1 have real user value?** **Yes, with a narrow claim.** For 14 findings
   across three ordinary files, 10 pointed at a real located problem and the fixes were
   verifiable by re-upload. It is a lint-and-pattern reviewer with honest provenance, not
   a bug finder.
2. **Is the output understandable?** Yes: file, line, rule, severity, source, verification,
   explanation, suggestion, and an explicit limits block.
3. **Are suggestions actionable?** Mostly: the heuristic suggestions are concrete; Ruff
   findings carry a rule code and no rewrite advice.
4. **Are false positives acceptable?** In this run 1 of 14 findings was an artefact
   (`RUF100` on a deliberate `# noqa`) and 3 were duplicate rule hits. That is acceptable
   for a local tool that shows provenance, but it is not "zero noise".
5. **How should this be described to users?** "Deterministic lint plus a small set of
   explainable risk patterns, every finding located and labelled; absence of findings is
   not proof of correctness; your code is never executed."
6. **Ready for a local V1.1 release candidate?** **Yes**, as an opt-in feature
   (`PR_GUARDIAN_UPLOAD_ENABLED=1`), with the limitations above stated in the UI.
7. **Should public Render stay No-Go?** **Yes.** Unchanged from the hardening review: no
   OS-level resource sandbox, no per-IP rate limiting, no reverse-proxy body limit, and the
   free instance has no RSS cap per worker. This round adds no mitigation for abuse.

## 8. What would change the public decision

A container (or equivalent) with per-process CPU/RSS limits, a per-IP rate limiter and a
bounded queue, a reverse-proxy body limit, and a startup sweep for temp directories left by
a hard kill. Then re-run this acceptance plus the hardening suite, and measure abuse
behaviour with a deliberately hostile client.
