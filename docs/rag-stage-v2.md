# RAG V1 — boundary fixes, larger retrieval evaluation, real model smoke

Branch `feature/v3-repo-rag`, PR #3 (still draft, stacked on the MCP branch).
`main`, Render and the frozen V2 held-out are untouched. `docs/rag-eval/` is
historical and unmodified.

---

## 1. Boundary fixes

### 1.1 The cache now proves where its content came from

Before, `load_cached()` trusted a JSON file because it claimed a `version` and a
`sha`. A corrupted or tampered cache could therefore return code that is not in
the pinned commit.

Now the cache stores a **manifest** of `path -> (git blob SHA, size)` taken from
the snapshot, and a load is accepted only when all of the following hold:

| Check | Failure behaviour |
|---|---|
| manifest file set equals the commit's allowed files | discard, rebuild |
| every blob SHA and size matches the commit | discard, rebuild |
| every chunk path is inside the `src/**/*.py` allowlist | discard, rebuild |
| every chunk's SHA equals the pinned commit | discard, rebuild |
| line range valid, ≤ `MAX_CHUNK_LINES`, text ≤ `MAX_CHUNK_CHARS` | discard, rebuild |
| chunk text equals the source read **through `TrustedCodebase`** | discard, rebuild |

A rejected cache is logged and rebuilt from the commit; it is never returned to a
model. `PR_GUARDIAN_RAG_CACHE=off` disables the disk cache entirely.

Tests (`tests/test_repo_index.py`): hit, corrupt JSON, missing `files` field,
forged path outside `src/`, text that does not match the commit, a cache copied
under a different SHA, and cache disabled.

### 1.2 The tool output budget is a hard limit on the whole payload

The generic truncation only shortened `payload["content"]`, which does nothing for
`search_code`, whose evidence lives in `payload["hits"]`. `_cap_payload()` now
bounds the serialized object:

1. drop trailing hits until it fits, recording `omitted`;
2. shorten snippets (400 → 240 → 120 → 60 chars);
3. drop snippets entirely;
4. fall back to a minimal `{ok, truncated, error}` object.

Every result stays valid JSON, carries `truncated: true`, and the `result_chars`
written to the trace is the length of the text actually sent.

Tests: `top_k=10` with very long paths, long symbol names and large snippets;
generic oversized `content`.

### 1.3 Unparsable files obey the same chunking rule

A `SyntaxError` used to produce one chunk spanning the whole file. It now goes
through the same windowing, so an unparsable 240-line file yields multiple
`unparsed` chunks, each ≤ 60 lines and ≤ 4,000 characters, with correct line
numbers. Test: `test_syntax_error_file_is_chunked_within_bounds`.

271 tests pass; `ruff check` is clean.

---

## 2. Retrieval evaluation v2 (separate, versioned)

`docs/rag-eval-v2/`, produced by `scripts/rag_eval_v2.py`.

* corpus: 26 modules + 2 classes, **103 AST chunks** across 28 files, generated
  deterministically and committed to a temporary repository;
* **51 queries frozen before measurement** — 12 lexical, 31 paraphrase, 8 mixed;
* gold symbols declared with the queries; gold lines resolved through the index;
* index build 0.82 s, cached 0.013 s, cache file 37 KB, search latency 0.1 ms.

| Method | Recall@1 | Recall@3 | Recall@5 | MRR | no-result | mean candidates | payload |
|---|---|---|---|---|---|---|---|
| A · BM25 | 52.94% | 64.71% | 66.67% | 0.5791 | 7.84% | 3.20 | 1,352 chars |
| B · + domain synonym table | **78.43%** | **90.20%** | 94.12% | 0.8464 | 0.00% | 4.25 | 1,777 chars |
| C · + generic synonym table | 50.98% | 66.67% | 70.59% | 0.5791 | 5.88% | 3.24 | 1,352 chars |

Paraphrase group only (n = 31): A Recall@3 **41.94%**, B **83.87%**, C **45.16%**.

**B is a tuned upper bound, not a general result.** The domain table was written
while looking at this corpus. C exists to expose that: it uses everyday English
pairs only and barely moves the numbers. So the gain is corpus knowledge, not
expansion as such — an important distinction that the earlier v1 report could not
make.

---

## 3. Real DeepSeek → search_code → Finding (dev case)

`scripts/rag_smoke.py`, `docs/rag-smoke/`. The corpus is the evaluation corpus plus
a `checkout` module. The bug is a unit mismatch: `finalise()` passes
`order["cents"]` into `apply_credit(invoice, amount)`, whose docstring says the
amount is in whole currency units. The clean branch passes `order["amount"]` and an
extra `note="web"` the callee accepts.

Local/MCP parity on the same snapshot and query: payloads **identical**, `ref` ==
pinned SHA, gold file in the top 5.

| Case | Model calls | Tool calls | `search_code` used | Search hit gold file | Finding | Status |
|---|---|---|---|---|---|---|
| bug (`dev/credit-cents`) | 4 | 6 (2 denied) | **yes, 1 call** | no (its own query missed) | 1 — correctly identifies the cents/units defect | partial (`tool_limit`) |
| clean (`dev/credit-units`) | 4 | 6 (2 denied) | **yes, 1 call** | yes | 1 — low-severity claim that the callee ignores `note` | partial (`tool_limit`) |

Observed chain for the bug case: `read_code_context(checkout.py)` →
`find_symbol_definition(apply_credit)` → `read_code_context(invoices.py)` →
`search_code("order[\"cents\"] order[\"amount\"] finalise")` → budget exhausted →
finding at `src/shop/checkout.py:8`, severity high. Every tool result carried
`ref = 9a79ddc5…`, the pinned SHA, and the MCP session closed with no leaked
thread.

Honest reading:

* the model **chose `search_code` on its own** in both runs — no prompt forcing;
* on the bug case its own search query did not surface the gold file, yet symbol
  lookup did, and the finding is correct — search is not always the shortest path;
* on the clean case its search *did* reach the gold file and it still raised a
  low-severity false alarm about an unused parameter. Retrieval is not judgement;
* both runs exhausted the 4-call budget, so the traces are `partial`. That is
  recorded, not hidden.

This is a functional check of the loop, not a detection benchmark.

---

## 4. Do we need dense retrieval?

Evidence for:

* paraphrase Recall@3 is 41.94% with BM25 and 45.16% with an *uninformed* synonym
  table — the gap is vocabulary, and a lexical table written without corpus
  knowledge does not close it;
* the failures are total misses ("how much the buyer has to pay" returns nothing),
  not bad ordering, so **re-ranking alone cannot fix them**: a re-ranker can only
  reorder candidates that some retriever already produced.

Evidence against acting immediately:

* 31 paraphrase queries on one synthetic corpus is still a small sample, and the
  corpus is auto-generated, so its vocabulary may be easier than real code;
* a dense model adds a dependency, an index store and a privacy decision.

Recommended next step, if any: **two retrievers, one fusion** — BM25 top-k and
dense top-k computed independently, candidates unioned, then RRF or a weighted
merge, with the existing per-commit cache and the same gold-frozen harness. Not a
re-ranker on top of BM25 only, for the reason above. The evaluation harness already
supports adding a method without touching the frozen history.

---

## 5. Open items

* The synonym table is corpus-tuned; a corpus-independent query set is needed
  before any expansion claim.
* The corpus is synthetic; real repositories have longer files and more noise.
* Budget exhaustion in both smoke runs suggests the default 4 calls is tight when a
  model searches and reads; that is a product decision, not a correctness bug.
* Search results are one-hop; no call graph expansion yet.
* No cost estimation (no price table configured).
