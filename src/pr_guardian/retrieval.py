"""Retrieval modes: bm25 (default), dense, and hybrid with RRF fusion.

Hybrid is a real two-way retrieval, not a re-ranker:

    BM25 top-k  -> candidates A
    Dense top-k -> candidates B
    A ∪ B (deduplicated by stable chunk id) -> RRF -> final top-k

A candidate that BM25 never returned can therefore still reach the result through
the dense branch, which is the entire point of the design.
"""

from __future__ import annotations

import logging

from .dense_index import MODEL_DIM, MODEL_ID, MODEL_REVISION, DenseUnavailable
from .repo_index import build_index
from .repo_index import search_code as bm25_search

LOGGER = logging.getLogger("pr_guardian.retrieval")

MODES = ("bm25", "dense", "hybrid")
DEFAULT_MODE = "bm25"
RRF_K = 60               # fixed before any measurement; never tuned on results
BM25_TOP_K = 10
DENSE_TOP_K = 10
SNIPPET_CHARS = 280


def normalise_mode(value: object) -> str:
    mode = (value or DEFAULT_MODE).strip().lower() if isinstance(value, str) else DEFAULT_MODE
    return mode if mode in MODES else DEFAULT_MODE


def _hit_from_chunk(chunk, *, chunk_id: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "file_path": chunk.file_path,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "kind": chunk.kind,
        "qualname": chunk.qualname,
        "snippet": chunk.text[:SNIPPET_CHARS],
        "ref": chunk.sha,
    }


def _bm25_candidates(codebase, query: str, top_k: int) -> tuple[list[dict], str]:
    index = build_index(codebase)
    result = bm25_search(codebase, query, top_k, index=index)
    if not result.get("ok", True):
        return [], result.get("error", {}).get("code", "bad_query")
    hits = result.get("hits", [])
    for rank, hit in enumerate(hits, start=1):
        hit["bm25_rank"] = rank
    return hits, "ok"


def _dense_candidates(codebase, query: str, top_k: int) -> tuple[list[dict], list[str]]:
    from .dense_index import build_dense_index

    warnings: list[str] = []
    try:
        index = build_index(codebase)
        dense = build_dense_index(codebase, index.chunks)
    except DenseUnavailable as exc:
        return [], [exc.code]
    ranked = dense.search(query, top_k)
    hits: list[dict] = []
    for rank, (position, score) in enumerate(ranked, start=1):
        chunk = index.chunks[position]
        hit = _hit_from_chunk(chunk, chunk_id=dense.chunk_ids[position])
        hit["dense_rank"] = rank
        hit["score"] = round(score, 6)
        hits.append(hit)
    if not ranked:
        warnings.append("dense_no_match")
    return hits, warnings


def _rrf(merged: dict[str, dict], *, bm25_rank: int | None, dense_rank: int | None) -> float:
    score = 0.0
    if bm25_rank:
        score += 1.0 / (RRF_K + bm25_rank)
    if dense_rank:
        score += 1.0 / (RRF_K + dense_rank)
    return score


def search(codebase, query: object, top_k: object = 5, *, mode: str | None = None) -> dict:
    """Retrieval entry point shared by the local and MCP backends."""
    from .repo_index import MAX_QUERY_CHARS, MAX_TOP_K

    if not isinstance(query, str) or not query.strip():
        return {"ok": False, "error": {"code": "bad_query",
                                       "message": "query must be a non-empty string"}}
    if len(query) > MAX_QUERY_CHARS:
        return {"ok": False, "error": {"code": "bad_query",
                                       "message": f"query exceeds {MAX_QUERY_CHARS} chars"}}
    if isinstance(top_k, bool) or not isinstance(top_k, int):
        if isinstance(top_k, str) and top_k.strip().isdigit():
            top_k = int(top_k.strip())
        else:
            return {"ok": False, "error": {"code": "bad_top_k",
                                           "message": "top_k must be an integer"}}
    if not 1 <= top_k <= MAX_TOP_K:
        return {"ok": False, "error": {"code": "bad_top_k",
                                       "message": f"top_k must be between 1 and {MAX_TOP_K}"}}

    effective = normalise_mode(mode or getattr(codebase, "retrieval_mode", DEFAULT_MODE))
    sha = codebase.snapshot.sha
    warnings: list[str] = []
    meta = {
        "mode": effective,
        "bm25_top_k": BM25_TOP_K,
        "dense_top_k": DENSE_TOP_K,
        "rrf_k": RRF_K,
        "model": {"id": MODEL_ID, "revision": MODEL_REVISION, "dim": MODEL_DIM},
    }

    if effective == "bm25":
        hits, error = _bm25_candidates(codebase, query, top_k)
        if error != "ok":
            return {"ok": False, "error": {"code": error, "message": "query rejected"}}
        meta["mode_effective"] = "bm25"
        return {"ok": True, "found": bool(hits), "query": query, "ref": sha,
                "mode": effective, "hits": hits, "count": len(hits),
                "match_count": len(hits), "retrieval": meta,
                **({} if hits else {"reason": "no chunk matched the query"}),
                "tokens_estimated": sum(len(h["snippet"]) for h in hits) // 4}

    if effective == "dense":
        hits, codes = _dense_candidates(codebase, query, top_k)
        if codes:
            return {"ok": False, "ref": sha, "mode": effective,
                    "error": {"code": codes[0],
                              "message": "local dense model is not available"}}
        meta["mode_effective"] = "dense"
        return {"ok": True, "found": bool(hits), "query": query, "ref": sha,
                "mode": effective, "hits": hits, "count": len(hits),
                "match_count": len(hits), "retrieval": meta,
                **({} if hits else {"reason": "no chunk matched the query"}),
                "tokens_estimated": sum(len(h["snippet"]) for h in hits) // 4}

    # hybrid: two independent recalls, then RRF
    bm25_hits, error = _bm25_candidates(codebase, query, BM25_TOP_K)
    if error != "ok":
        return {"ok": False, "error": {"code": error, "message": "query rejected"}}
    dense_hits, codes = _dense_candidates(codebase, query, DENSE_TOP_K)
    if codes:
        warnings.extend(codes)
        meta["dense_status"] = codes[0]
        meta["mode_effective"] = "bm25"
        # explicit, never silent: the caller sees the fallback in the payload
        return {"ok": True, "found": bool(bm25_hits), "query": query, "ref": sha,
                "mode": "hybrid", "mode_effective": "bm25",
                "hits": bm25_hits[:top_k], "count": min(len(bm25_hits), top_k),
                "match_count": len(bm25_hits), "warnings": warnings,
                "retrieval": meta,
                "tokens_estimated": sum(len(h["snippet"]) for h in bm25_hits[:top_k]) // 4}

    meta["dense_status"] = "ok"
    meta["mode_effective"] = "hybrid"
    merged: dict[str, dict] = {}
    for hit in bm25_hits:
        entry = dict(hit)
        entry["source"] = "bm25"
        entry["dense_rank"] = None
        merged[hit["chunk_id"]] = entry
    for hit in dense_hits:
        existing = merged.get(hit["chunk_id"])
        if existing is None:
            entry = dict(hit)
            entry["source"] = "dense"
            entry["bm25_rank"] = None
            merged[hit["chunk_id"]] = entry
        else:
            existing["source"] = "both"
            existing["dense_rank"] = hit["dense_rank"]
            existing["score"] = hit.get("score")

    for entry in merged.values():
        entry["rrf_score"] = round(_rrf(merged, bm25_rank=entry.get("bm25_rank"),
                                        dense_rank=entry.get("dense_rank")), 8)
        entry.setdefault("bm25_rank", None)
    ordered = sorted(merged.values(),
                     key=lambda e: (-e["rrf_score"], e["file_path"], e["start_line"]))
    final = ordered[:top_k]
    meta["candidates"] = {"bm25": len(bm25_hits), "dense": len(dense_hits),
                          "union": len(merged)}
    return {"ok": True, "found": bool(final), "query": query, "ref": sha,
            "mode": "hybrid", "hits": final, "count": len(final),
            "match_count": len(final),
            **({} if final else {"reason": "no chunk matched the query"}),
            "warnings": warnings, "retrieval": meta,
            "tokens_estimated": sum(len(h["snippet"]) for h in final) // 4}
