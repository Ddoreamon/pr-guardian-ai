# Hybrid retrieval (V3.1) — local dense embeddings, RRF fusion, and what it actually bought

Branch `feature/v3-hybrid-rag`, stacked on `feature/v3-repo-rag`, Draft PR #4.
`main`, Render and the frozen V2 held-out are untouched; `docs/rag-eval/`,
`docs/rag-eval-v2/` and the held-out artefacts are unmodified.

The research question was narrow: **does dense retrieval find cross-file code that
BM25 misses, and is fusing the two worth the added cost?** The answers, measured
below, are *yes* and *not with the specified fusion*.

---

## 1. Retrieval fixes carried in this branch

1. **Cache provenance now covers AST-derived fields.** `calls`, `kind`,
   `qualname`, `part/parts`, imports and the chunk set are re-derived from the
   trusted `GitSnapshot` and compared with the cache. A digest stored inside the
   cache proves nothing (the same tampering could rewrite it), so the fresh
   derivation is authoritative. Tests: tampered `calls`/`kind`/`qualname`/`part`,
   a removed chunk, tampered imports, plus the earlier text/path/SHA cases.
2. **Hit accounting follows what the model sees.** Hit/miss is decided *after*
   capping, so a result whose hits were all dropped is a miss; `result_chars`
   counts the wrapped `UNTRUSTED TOOL OUTPUT` message; budget-denied records keep
   the provider `tool_call_id` and the refusal message length.
3. **Gold ambiguity is refused, not resolved.** Every evaluation identifies gold
   by `(file_path, qualname, start_line)`; the click corpus has 61 duplicate
   `(file, qualname)` pairs and the script exits rather than picking the first.

## 2. Dense index

| Item | Value |
|---|---|
| Model | `BAAI/bge-small-en-v1.5`, MIT licence |
| Revision (pinned) | `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` |
| Runtime | ONNX Runtime 1.31.1 on CPU (no PyTorch), `tokenizers` 0.23.3 |
| Files | `model.onnx` (133 MB), `tokenizer.json`, `config.json`, pinned by SHA-256 |
| Dimension | 384 |
| **Pooling (as implemented)** | **masked MEAN pooling** — see 2.1; this differs from the model's documented recommendation |
| Normalisation | L2, cosine similarity via dot product |
| Encoded text (`TEXT_VERSION=v1`) | `qualname \n file_path \n kind \n chunk text` |
| Truncation | 512 tokens; batches of 16 (an 864-chunk repository needs >10 GB of attention buffers in one pass) |

### 2.1 Pooling — what was actually run

The model card for `BAAI/bge-small-en-v1.5` recommends **CLS pooling**. This
implementation used **masked mean pooling** (`POOLING = "mean"`), which was the
code that produced every number in this document. That is a deviation from the
recommended recipe, and it is recorded rather than quietly corrected:

* the frozen results in `docs/hybrid-eval-v1/` apply **only** to mean pooling with
  revision `5c38ec7c`, encoded text `v1` and L2 normalisation;
* switching to CLS pooling changes the vectors, so it must be a **new version**:
  bump `TEXT_VERSION`/`POOLING` in the cache identity, rebuild the vector cache,
  and evaluate on a **fresh, independently written** query set — not on the click
  held-out that has now been seen;
* no result here may be attributed to "BGE-small-en-v1.5" in general.

Pinned file digests (SHA-256), for reproducibility:

| File | SHA-256 |
|---|---|
| `model.onnx` (133,093,490 B) | `828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35` |
| `tokenizer.json` (711,396 B) | `d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66` |
| `config.json` (743 B) | `094f8e891b932f2000c92cfc663bac4c62069f5d8af5b5278c4306aef3084750` |

Everything runs locally. The model is downloaded once into `.cache/models/` (never
committed), inference is a static graph — no `trust_remote_code`, no Python from
the hub — and nothing is uploaded to a third party. The heavy dependencies are an
**optional** `dense` extra, so the default install, the MCP server and Render do
not need them.

Vector cache: `.cache/dense/<sha>-<rev12>-v1-d384.{npy,json}`, validated on load
for commit SHA, model id/revision, text version, dimension, normalisation,
chunk-id list and order, count, shape, finiteness and a SHA-256 of the array;
`PR_GUARDIAN_RAG_CACHE=off` disables it.

## 3. Modes and fusion

`PR_GUARDIAN_RETRIEVAL_MODE=bm25|dense|hybrid` (default **bm25**); the model cannot
choose it. Hybrid executes two independent recalls — BM25 top-10 and dense top-10 —
unions them by stable chunk id, and applies RRF with **k = 60**, fixed before any
measurement. Results carry `chunk_id`, path, span, qualname, `source`
(`bm25`/`dense`/`both`), `bm25_rank`, `dense_rank`, `rrf_score` and a bounded
snippet, all bound to the snapshot SHA. If the model is unavailable, `dense` mode
returns `dense_unavailable`, and `hybrid` falls back to BM25 **and says so** in
`mode_effective`, `retrieval.dense_status` and `warnings`.

## 4. Evaluation

`docs/hybrid-eval-v1/`. Two corpora: the synthetic 26-module corpus as **Dev**
(queries authored earlier), and `pallets/click` @ `2247b35e` (BSD-3-Clause, 864
chunks) as **Held-out**, run once.

| Corpus | Method | Recall@1 | Recall@3 | Recall@5 | MRR | no-result | latency |
|---|---|---|---|---|---|---|---|
| Synthetic (31 q) | BM25 | 25.81% | 41.94% | 45.16% | 0.324 | 12.90% | 0.4 ms |
| | Dense | **58.06%** | **90.32%** | 96.77% | **0.737** | 0.00% | 7.7 ms |
| | Hybrid | 45.16% | 74.19% | 87.10% | 0.597 | 0.00% | 8.1 ms |
| | BM25 + domain table (reference) | — | 83.87% | — | 0.780 | — | — |
| click (20 q) | BM25 | 20.00% | 20.00% | 25.00% | 0.210 | 0.00% | 13.7 ms |
| | Dense | **30.00%** | **40.00%** | **60.00%** | **0.395** | 0.00% | 9.2 ms |
| | Hybrid | 15.00% | 35.00% | 40.00% | 0.246 | 0.00% | 22.7 ms |

**Dense-only recoveries** — gold absent from BM25's top-5 but present in dense:
**16/31** synthetic queries and **7/20** click queries, with no query where BM25
found it and dense did not. Examples: "who carries the parcel and what it costs" →
`shipping.estimate_shipping` at dense rank 1 (BM25: miss); "accept a true or false
value from the user" → `types.BoolParamType.convert` (dense rank 2, BM25: miss).

**Hybrid loses to dense on both corpora**, and on click it is even below BM25 at
Recall@1 (15% vs 20%). The cause is structural, not a bug: RRF treats the two
lists as equally trustworthy, so a weak BM25 list (Recall@3 20% on click) injects
candidates into the top slots that displace correct dense results. The fusion
parameters were fixed before measurement and were **not** tuned afterwards.

Resources: model load 0.36 s; 103-chunk dense index cold 2.9 s, warm 7 ms; 864-chunk
corpus index build 1.6 s, warm query 9 ms; RSS 64 → 272 MB; vector + index cache
4.4 MB; mean result 2.2–3.8 KB.

## 5. Real DeepSeek + hybrid smoke (`docs/hybrid-smoke/`)

Same development case as before (a cents/units mismatch across two files), run
through `search_code` in **hybrid** mode over MCP.

| Case | Model calls | Tool calls | `search_code` | Search hit gold | Findings | Status |
|---|---|---|---|---|---|---|
| bug | 4 | 6 (2 denied) | yes, 1 call | no (its query missed) | 1 — correct, high severity | partial |
| clean | 4 | 6 (2 denied) | yes, 1 call | yes | **0** | partial |

Local and MCP with the same snapshot, query and mode returned identical payloads
(same chunk ids, same `source`, same `rrf_score`, same ranks); every tool result
carried the pinned SHA; the session closed with no leaked thread. Both runs hit the
4-call budget again, so both are `partial`.

## 6. Answers

1. **Does dense find code BM25 completely missed?** Yes — 16/31 dev and 7/20
   held-out queries, including cases where BM25 returned nothing relevant.
2. **Does hybrid bring a stable gain worth its cost?** No. On both corpora it is
   worse than dense alone, and on the held-out set worse than BM25 at Recall@1.
3. **Should hybrid be kept as an official option?** Not in this form. Keep `dense`
   as the optional retrieval mode and `bm25` as the default/offline fallback;
   treat `hybrid` as an experiment that failed its own pre-registered test.

## 7. Remaining risks

* One held-out corpus (click) and 20 queries: enough to reject this fusion, not
  enough to size dense's true advantage.
* Click queries were written from the public API surface; targets were then chosen
  among unambiguous symbols, which is a selection bias worth stating.
* Dense adds a 133 MB asset and ~270 MB RSS; unacceptable for the free Render tier,
  which is why it stays an optional extra and is never loaded there.
* Fusion alternatives (weighted RRF, equal-depth lists, convex combination) are
  untested; testing them on this corpus would be fitting to the test set.
* No model run with `dense` mode was made; the smoke used `hybrid`, as specified.

## 8. Boundaries — how to read these numbers

* **A zero no-result rate does not mean relevant results.** Dense always returns
  its top-k, so "found something" is guaranteed; only the gold-based Recall/MRR
  columns measure whether that something was right.
* **The vector cache is not tamper-proof.** Load-time SHA-256 checks catch
  accidental corruption (truncated file, wrong shape, non-finite values, stale
  chunk list). An attacker who can write to the cache directory can rewrite the
  data *and* the checksum together. It is an integrity check, not a security
  boundary. Environments that need one should run with
  `PR_GUARDIAN_RAG_CACHE=off` and rebuild per process.
* **The model cannot choose the retrieval mode.** It is bound at startup from
  configuration; a tool call cannot request a repository, a commit, or a mode.
* **The two real-model smoke runs were both `partial`** (4-call budget exhausted).
  They demonstrate that the tool loop, transport and validator work end to end.
  They are **not** evidence about review accuracy and must not be quoted as such.
* Published metadata is limited to model id, revision, dimension, pooling,
  normalisation, text version and availability — no local paths, no user names.
  `scripts/hybrid_eval.py` serialises `public_model_info()` rather than the raw
  status dict, and a test asserts that neither the descriptor nor any committed
  results file contains a host path.
* **History is not rewritten.** The earlier commit `5b43239` still contains the
  local model directory inside `docs/hybrid-eval-v1/results.json`. It was removed
  from the current tree in the archival commit; the numbers, queries, gold
  positions and ranks are unchanged.

## 9. Decision record

| Decision | Status | Evidence |
|---|---|---|
| `bm25` remains the default retrieval mode | **accepted** | zero extra dependencies, works offline, is the fallback everywhere |
| `dense` kept as an experimental, optional local mode | **accepted** | 16/31 and 7/20 gold chunks recovered that BM25's top-5 missed, with no BM25-only cases |
| `hybrid` (equal-weight RRF, k = 60) | **experimental / not recommended** | worse than dense alone on both corpora; below BM25 at Recall@1 on the held-out set |
| RRF weights tuned on the click held-out | **rejected** | would fit the only held-out set; a new fusion needs a new pre-registered evaluation |
| Negative hybrid result kept | **accepted** | `docs/hybrid-eval-v1/` is frozen and unmodified apart from removing a local path from `results.json` |
| CLS pooling | **deferred** | changing pooling invalidates the frozen vectors and needs a fresh query set |

The embedding stack therefore stays an opt-in extra: the default install, the MCP
server and the Render deployment never load it.
