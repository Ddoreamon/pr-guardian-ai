# Repository-level retrieval (v1) — keyword index behind `search_code`

Branch `feature/v3-repo-rag`, stacked on the MCP branch. No embeddings, no vector
database, no external service: this round builds a **local, deterministic keyword
index** and exposes it as a tool. It does not claim semantic retrieval.

---

## 1. Index design

`src/pr_guardian/repo_index.py`

```
TrustedCodebase.snapshot (pinned SHA)
   └── source_files()            → tracked src/**/*.py, same guards as reads
        └── read_source()       → whole file, 200 KB cap, symlink refusal
             └── ast.parse      → definitions
                  ├── function / async function
                  ├── class  →  methods (qualified names)
                  ├── module-level CONSTANT (Assign / AnnAssign)
                  └── long definition → controlled windows (40 lines, ≤60 per chunk)
```

Each chunk stores `sha`, `file_path`, `start_line`, `end_line`, `kind`, `qualname`,
`text` (≤4,000 chars), `calls` and, when split, `part`/`parts`. Nothing is read
from the working tree, so an uncommitted edit cannot enter the index.

**Cache:** `.cache/repo_index/<sha>-v1.json`, written atomically, keyed by the
commit SHA *and* the index version. Two commits therefore cannot share rankings,
and a stale cache is impossible: a mismatch on either field is a miss.
`.cache/` is git-ignored.

## 2. Retrieval

BM25 (k1 = 1.5, b = 0.75) over identifier tokens, with these additive signals:

| Signal | Boost | Why |
|---|---|---|
| exact symbol / qualname match (snake and compact forms) | +3.0 | a named symbol should win |
| query tokens inside the chunk name | +2.0 | partial name match |
| chunk calls a queried symbol | +1.0 | finds call sites of a symbol |
| file imports a queried name | +1.0 | import-level relation |
| query token in the file path | +0.5 | weak locality hint |

Ties break on `file_path`, then `start_line`, then `part`, so results are
reproducible. Every hit carries `score`, `why` (the signals that fired), `snippet`,
line range and `ref`. A miss returns `found: false` with a `reason` — it never
invents a file.

Tool surface: `search_code(query: str, top_k: int = 5)`, available in both
backends. `top_k` is bounded to 1–10, `query` to 200 chars; the call consumes the
existing tool budget like the other two tools.

## 3. Local vs MCP

The same tool is registered in the MCP server with a real schema, the shared
parameter validator and the same `TrustedCodebase` dispatch. A test runs
`_execute(SEARCH_CODE, …)` through the in-process backend and through a real MCP
stdio session on the same snapshot and asserts the payloads are **identical**
(no latency key, no metadata), which is the same guarantee the other tools have.

## 4. Retrieval quality

Independent dev corpus (7 files, 10 chunks), 12 queries with gold spans fixed
before running. No model calls. `docs/rag-eval/REPORT.md`.

| Query group | n | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---|---|---|---|---|
| Lexical (shares vocabulary) | 8 | 100% | 100% | 100% | 1.000 |
| **Paraphrase (little overlap)** | 4 | **25%** | 25% | 25% | **0.250** |
| Overall | 12 | 75% | 75% | 75% | 0.750 |

Cost: index build **0.20 s** for 10 chunks, **0.011 s** from cache, search latency
**0.5 ms**, mean result payload 1,145 chars.

Baseline (`find_symbol_definition` + read) reaches the gold chunk 100% of the
time — but only because the evaluation hands it the *gold symbol name*. A caller
who does not already know the symbol cannot issue that query, which is exactly the
gap `search_code` fills. The two are not on the same axis and are not compared as
if they were.

## 5. Tests

`tests/test_repo_index.py` (11): chunks carry SHA/path/lines/text, cross-file call,
symbol boost, duplicate names, constants, explicit miss, long-function splitting
bounded by 60 lines and 4,000 chars, allowlist-only indexing (`tests/`, `.env`
excluded), prompt-injection text inert in both source and query, per-SHA cache
isolation, query/top_k validation.

Plus MCP protocol tests for the new tool (schema, ranked results with `ref`, explicit
miss, unknown/missing/mistyped parameters) and the local/MCP parity test.

## 6. Do we need embeddings?

Evidence for: the paraphrase group fails almost completely (1/4), while lexical
queries are perfect. The failures are vocabulary gaps, not ranking noise — "how
much money the customer owes" has no token in common with `total(net, region)`.
That is precisely the case embeddings address.

Evidence against acting now: the corpus is 10 chunks and 4 paraphrase queries; a
single hit or miss swings the number by 25 points. Any embedding layer would also
add a model dependency, an index store and a privacy decision (a local model or an
external endpoint), none of which is justified by n = 4.

**Recommendation:** keep this as the baseline; if the next round adds embeddings,
do it as a *hybrid* — BM25 stays the default, vectors only re-rank, and the
paraphrase set must grow to at least 30 queries before any claim is made. The
evaluation harness here is designed to be reused for that comparison.

## 7. Open items

* No query expansion, stemming or code-specific tokenizer beyond snake/camel split.
* Chunks are syntactic, not call-graph aware; "calls_symbol" is one hop only.
* The cache is per-process filesystem; a read-only deployment would rebuild each time.
* `search_code` results are capped by character count, so a very large chunk set can
  still truncate snippets.
* No model run was made for this stage; only retrieval metrics are reported.
