"""Dense index, RRF fusion and retrieval-mode fallbacks.

Most tests use a deterministic fake embedder so they run without the model; one
test exercises the real ONNX model and skips when it is not installed.
"""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from pr_guardian import dense_index, retrieval
from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.repo_index import build_index, stable_chunk_id


class FakeEmbedder:
    """Deterministic bag-of-tokens vectors; plumbing only, no semantics."""

    def __init__(self, dim: int = dense_index.MODEL_DIM) -> None:
        import numpy as np

        self.np = np
        self.dim = dim

    def embed(self, texts):
        np = self.np
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in text.lower().replace("_", " ").split():
                bucket = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % self.dim
                out[row, bucket] += 1.0
            norm = np.linalg.norm(out[row])
            if norm:
                out[row] /= norm
        return out


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("PR_GUARDIAN_CACHE_DIR", str(tmp_path / ".cache"))
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "h@example.com")
    _git(root, "config", "user.name", "h")
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "rates.py").write_text(
        '"""Rates."""\n\nDEFAULT_RATE = 0.25\n\n\ndef rate_for(region):\n'
        '    """Return the rate for a region."""\n    return DEFAULT_RATE\n', encoding="utf-8")
    (root / "src" / "pricing.py").write_text(
        '"""Pricing."""\n\nfrom .rates import rate_for\n\n\ndef order_total(net, region):\n'
        '    """Return the taxed total."""\n    return net + net * rate_for(region)\n',
        encoding="utf-8")
    (root / "src" / "refunds.py").write_text(
        '"""Refunds."""\n\n\ndef refund_total(order):\n'
        '    """Return the money given back."""\n    return sum(order)\n', encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    monkeypatch.setattr(dense_index, "get_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(dense_index, "_EMBEDDER", None)


# ---- dense index -----------------------------------------------------------

def test_dense_index_is_one_to_one_with_chunks(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    chunks = build_index(codebase).chunks
    dense = dense_index.build_dense_index(codebase, chunks)
    assert len(dense.chunk_ids) == len(chunks)
    assert dense.vectors.shape == (len(chunks), dense_index.MODEL_DIM)
    assert dense.chunk_ids == [stable_chunk_id(c) for c in chunks]
    assert len(set(dense.chunk_ids)) == len(chunks)


def test_dense_cache_roundtrip_and_rebuild(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    chunks = build_index(codebase).chunks
    first = dense_index.build_dense_index(codebase, chunks)
    npy, meta_path = dense_index._cache_paths(codebase.snapshot.sha)
    assert npy.is_file() and meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["model_id"] == dense_index.MODEL_ID
    assert meta["revision"] == dense_index.MODEL_REVISION
    assert meta["dim"] == dense_index.MODEL_DIM
    assert meta["chunk_ids"] == first.chunk_ids
    second = dense_index.build_dense_index(codebase, chunks)
    assert (second.vectors == first.vectors).all()


@pytest.mark.parametrize("damage", ["corrupt_npy", "wrong_dim", "non_finite",
                                    "chunk_ids", "model_revision", "checksum"])
def test_bad_dense_cache_is_rejected(repo, damage):
    codebase = TrustedCodebase.from_git(repo, "main")
    chunks = build_index(codebase).chunks
    dense_index.build_dense_index(codebase, chunks)
    npy, meta_path = dense_index._cache_paths(codebase.snapshot.sha)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    if damage == "corrupt_npy":
        npy.write_bytes(b"not a numpy file")
    elif damage == "wrong_dim":
        import numpy as np

        np.save(npy, np.zeros((len(chunks), 8), dtype=np.float32))
        meta["vectors_sha256"] = dense_index._sha256(npy)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    elif damage == "non_finite":
        import numpy as np

        vectors = np.load(npy)
        vectors[0, 0] = float("nan")
        np.save(npy, vectors)
        meta["vectors_sha256"] = dense_index._sha256(npy)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    elif damage == "chunk_ids":
        meta["chunk_ids"] = ["deadbeef"] * len(meta["chunk_ids"])
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    elif damage == "model_revision":
        meta["revision"] = "0" * 40
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    elif damage == "checksum":
        meta["vectors_sha256"] = "f" * 64
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert dense_index._load_vectors(codebase.snapshot.sha, [stable_chunk_id(c) for c in chunks]) is None
    rebuilt = dense_index.build_dense_index(codebase, chunks)
    assert rebuilt.vectors.shape == (len(chunks), dense_index.MODEL_DIM)


def test_dense_cache_can_be_disabled(repo, monkeypatch):
    codebase = TrustedCodebase.from_git(repo, "main")
    chunks = build_index(codebase).chunks
    monkeypatch.setenv("PR_GUARDIAN_RAG_CACHE", "off")
    dense = dense_index.build_dense_index(codebase, chunks)
    npy, _ = dense_index._cache_paths(codebase.snapshot.sha)
    assert not npy.exists()
    assert dense.vectors.shape[0] == len(chunks)


def test_dense_vectors_are_normalised(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    chunks = build_index(codebase).chunks
    import numpy as np

    dense = dense_index.build_dense_index(codebase, chunks)
    norms = np.linalg.norm(dense.vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_encoded_text_is_documented_and_stable(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    chunk = build_index(codebase).chunks[0]
    encoded = dense_index.encode_text(chunk)
    assert chunk.qualname in encoded
    assert chunk.file_path in encoded
    assert chunk.text[:20] in encoded
    assert dense_index.TEXT_VERSION == "v1"


# ---- fusion ----------------------------------------------------------------

def test_hybrid_is_two_independent_recalls_not_a_rerank(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    result = retrieval.search(codebase, "money given back", 5, mode="hybrid")
    assert result["ok"] is True
    sources = {h["source"] for h in result["hits"]}
    assert "dense" in sources or "both" in sources
    dense_only = [h for h in result["hits"] if h["source"] == "dense"]
    # a dense-only candidate proves BM25 did not have to provide it
    assert dense_only, "hybrid returned no candidate that BM25 missed"


def test_rrf_scores_follow_the_fixed_formula(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    result = retrieval.search(codebase, "rate region", 10, mode="hybrid")
    for hit in result["hits"]:
        expected = 0.0
        if hit["bm25_rank"]:
            expected += 1.0 / (retrieval.RRF_K + hit["bm25_rank"])
        if hit["dense_rank"]:
            expected += 1.0 / (retrieval.RRF_K + hit["dense_rank"])
        assert hit["rrf_score"] == pytest.approx(round(expected, 8))
        assert hit["chunk_id"]


def test_hybrid_deduplicates_the_same_chunk(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    result = retrieval.search(codebase, "rate region", 10, mode="hybrid")
    ids = [h["chunk_id"] for h in result["hits"]]
    assert len(ids) == len(set(ids))


def test_hybrid_is_deterministic(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    first = retrieval.search(codebase, "money given back", 5, mode="hybrid")
    second = retrieval.search(codebase, "money given back", 5, mode="hybrid")
    assert [(h["chunk_id"], h["rrf_score"]) for h in first["hits"]] == \
           [(h["chunk_id"], h["rrf_score"]) for h in second["hits"]]


def test_hybrid_falls_back_to_bm25_and_says_so(repo, monkeypatch):
    def unavailable(*_a, **_k):
        raise dense_index.DenseUnavailable("dense_dependency_missing", "no onnx")

    monkeypatch.setattr(dense_index, "get_embedder", unavailable)
    codebase = TrustedCodebase.from_git(repo, "main")
    result = retrieval.search(codebase, "rate region", 5, mode="hybrid")
    assert result["ok"] is True
    assert result["mode"] == "hybrid"
    assert result["mode_effective"] == "bm25"
    assert result["retrieval"]["dense_status"] == "dense_dependency_missing"
    assert "dense_dependency_missing" in result["warnings"]


def test_dense_mode_reports_unavailable_instead_of_pretending(repo, monkeypatch):
    def unavailable(*_a, **_k):
        raise dense_index.DenseUnavailable("model_files_missing", "no model")

    monkeypatch.setattr(dense_index, "get_embedder", unavailable)
    codebase = TrustedCodebase.from_git(repo, "main")
    result = retrieval.search(codebase, "rate region", 5, mode="dense")
    assert result["ok"] is False
    assert result["error"]["code"] == "model_files_missing"


def test_unknown_mode_falls_back_to_bm25(repo):
    TrustedCodebase.from_git(repo, "main")
    assert retrieval.normalise_mode("carrier-pigeon") == "bm25"
    assert retrieval.normalise_mode(None) == "bm25"
    assert retrieval.normalise_mode("hybrid") == "hybrid"


def test_result_budget_is_bounded_for_hybrid(repo):
    from pr_guardian.tool_calling import MAX_TOOL_OUTPUT_CHARS, _cap_payload

    codebase = TrustedCodebase.from_git(repo, "main")
    result = retrieval.search(codebase, "rate region money order", 10, mode="hybrid")
    capped, text = _cap_payload(result)
    assert len(text) <= MAX_TOOL_OUTPUT_CHARS
    assert json.loads(text) == capped


# ---- the real model (skipped when it is not installed) ---------------------

@pytest.mark.skipif(not dense_index.model_status()["available"],
                    reason="local ONNX model not installed")
def test_real_model_embeds_and_ranks(repo, monkeypatch):
    real = dense_index.OnnxEmbedder()
    monkeypatch.setattr(dense_index, "get_embedder", lambda: real)
    codebase = TrustedCodebase.from_git(repo, "main")
    chunks = build_index(codebase).chunks
    dense = dense_index.build_dense_index(codebase, chunks)
    assert dense.vectors.shape[1] == dense_index.MODEL_DIM
    ranked = dense.search("money returned to the buyer", 3)
    assert ranked
    best = chunks[ranked[0][0]]
    assert best.file_path.endswith(".py")

@pytest.mark.skipif(not dense_index.model_status()["available"],
                    reason="local ONNX model not installed")
def test_local_and_mcp_hybrid_agree(repo, monkeypatch):
    """Same snapshot, query, mode and model: identical business payloads."""
    from pr_guardian.mcp_client import MCPToolBackend

    real = dense_index.OnnxEmbedder()
    monkeypatch.setattr(dense_index, "get_embedder", lambda: real)
    codebase = TrustedCodebase.from_git(repo, "main")
    local = retrieval.search(codebase, "money given back", 5, mode="hybrid")
    with MCPToolBackend(repo, "HEAD", retrieval_mode="hybrid") as backend:
        remote = backend.search_code("money given back", 5)
    assert remote["ref"] == local["ref"]
    assert [h["chunk_id"] for h in remote["hits"]] == [h["chunk_id"] for h in local["hits"]]
    for a, b in zip(local["hits"], remote["hits"], strict=True):
        assert a["source"] == b["source"]
        assert a["rrf_score"] == pytest.approx(b["rrf_score"], abs=1e-9)
        assert a["bm25_rank"] == b["bm25_rank"]
        assert a["dense_rank"] == b["dense_rank"]
    assert local["retrieval"]["rrf_k"] == remote["retrieval"]["rrf_k"] == retrieval.RRF_K


def test_retrieval_mode_setting_is_validated(monkeypatch):
    from pr_guardian.settings import get_settings

    monkeypatch.setenv("PR_GUARDIAN_RETRIEVAL_MODE", "hybrid")
    assert get_settings().retrieval_mode == "hybrid"
    monkeypatch.setenv("PR_GUARDIAN_RETRIEVAL_MODE", "telepathy")
    assert get_settings().retrieval_mode == "bm25"
    monkeypatch.delenv("PR_GUARDIAN_RETRIEVAL_MODE")
    assert get_settings().retrieval_mode == "bm25"
