"""Local dense retrieval for AST chunks (ONNX, CPU, no external API).

Everything runs on this machine:

* the model file and tokenizer are pinned by **revision and SHA-256** and loaded
  from `.cache/models/`, never fetched at query time and never committed;
* inference is a static ONNX graph executed by `onnxruntime` — no remote code
  execution, no `trust_remote_code`, no Python from the model repository;
* vectors are cached per commit SHA, model revision, text version, dimension and
  normalisation, and validated before use.

The heavy dependencies (`numpy`, `onnxruntime`, `tokenizers`) live in the optional
`dense` extra, so the default install, the MCP server and Render never pull them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger("pr_guardian.dense")

MODEL_ID = "BAAI/bge-small-en-v1.5"
MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
MODEL_DIM = 384
MAX_TOKENS = 512
BATCH_SIZE = 16          # bounds peak memory on CPU; 864 chunks would otherwise OOM
TEXT_VERSION = "v1"
NORMALISE = True

# sha256 of the exact files downloaded from
# https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/<revision>/...
MODEL_FILES = {
    "model.onnx": "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35",
    "tokenizer.json": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
    "config.json": "094f8e891b932f2000c92cfc663bac4c62069f5d8af5b5278c4306aef3084750",
}


class DenseUnavailable(RuntimeError):
    """Raised when the local model cannot be used; carries a machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_payload(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


def _root() -> Path:
    from .repo_index import cache_root

    return cache_root()


def model_dir() -> Path:
    """Where the pinned model files live.

    Deliberately independent of PR_GUARDIAN_CACHE_DIR: tests redirect caches to a
    temporary directory, while the model is a fixed local asset.
    """
    rev = MODEL_REVISION[:12]
    override = os.environ.get("PR_GUARDIAN_DENSE_MODEL_DIR")
    if override:
        return Path(override)
    project = Path(__file__).resolve().parents[2]
    return project / ".cache" / "models" / f"bge-small-en-v1.5@{rev}"


def dense_cache_dir() -> Path:
    override = os.environ.get("PR_GUARDIAN_DENSE_CACHE_DIR")
    return Path(override) if override else _root() / "dense"


def cache_enabled() -> bool:
    return (os.environ.get("PR_GUARDIAN_RAG_CACHE") or "").strip().lower() != "off"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def model_status() -> dict:
    """Report whether dense retrieval can run here, without loading the model."""
    info = {
        "model_id": MODEL_ID,
        "revision": MODEL_REVISION,
        "dim": MODEL_DIM,
        "text_version": TEXT_VERSION,
        "normalise": NORMALISE,
        "dir": str(model_dir()),
    }
    try:
        import numpy  # noqa: F401
    except Exception:  # noqa: BLE001
        info.update(available=False, reason="numpy_missing")
        return info
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except Exception:  # noqa: BLE001
        info.update(available=False, reason="dense_dependency_missing")
        return info
    directory = model_dir()
    for name, expected in MODEL_FILES.items():
        path = directory / name
        if not path.is_file():
            info.update(available=False, reason="model_files_missing",
                        missing=name)
            return info
        if _sha256(path) != expected:
            info.update(available=False, reason="model_file_corrupt", file=name)
            return info
    info.update(available=True, reason="ready")
    return info


def chunk_id(chunk) -> str:
    """Stable id, shared with the BM25 index so fusion can deduplicate."""
    from .repo_index import stable_chunk_id

    return stable_chunk_id(chunk)


def encode_text(chunk) -> str:
    """The exact text that is embedded (versioned by TEXT_VERSION)."""
    return f"{chunk.qualname}\n{chunk.file_path}\n{chunk.kind}\n{chunk.text}"


class OnnxEmbedder:
    """Mean-pooled, L2-normalised sentence embeddings on CPU."""

    def __init__(self, directory: Path | None = None) -> None:
        status = model_status()
        if not status["available"]:
            raise DenseUnavailable(status["reason"],
                                   f"dense model unavailable: {status['reason']}")
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        directory = directory or model_dir()
        self.np = np
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        # padding must be configured before truncation, otherwise sequences are
        # padded past the model's position limit and attention explodes in memory
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2)))
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(directory / "model.onnx"),
                                            sess_options=options,
                                            providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.session.get_inputs()}

    def _embed_batch(self, texts: list[str]):
        import numpy as np

        encoded = self.tokenizer.encode_batch(texts)
        ids = np.asarray([e.ids for e in encoded], dtype=np.int64)
        mask = np.asarray([e.attention_mask for e in encoded], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        hidden = self.session.run(None, feeds)[0]
        mask_f = mask.astype(np.float32)[..., None]
        pooled = (hidden * mask_f).sum(axis=1) / np.clip(mask_f.sum(axis=1), 1e-9, None)
        if NORMALISE:
            norm = np.linalg.norm(pooled, axis=1, keepdims=True)
            pooled = pooled / np.clip(norm, 1e-12, None)
        return pooled.astype(np.float32)

    def embed(self, texts: list[str], batch_size: int = BATCH_SIZE) -> object:
        """Batched so peak memory stays bounded regardless of repository size.

        Attention is quadratic in sequence length, so embedding a whole repository
        at once (864 chunks here) needs >10 GB for the softmax matrices alone.
        """
        import numpy as np

        if not texts:
            return np.zeros((0, MODEL_DIM), dtype=np.float32)
        blocks = [self._embed_batch(texts[i:i + batch_size])
                  for i in range(0, len(texts), batch_size)]
        return np.vstack(blocks).astype(np.float32)


_EMBEDDER: OnnxEmbedder | None = None
_EMBEDDER_LOCK = threading.Lock()


def get_embedder() -> OnnxEmbedder:
    global _EMBEDDER
    with _EMBEDDER_LOCK:
        if _EMBEDDER is None:
            _EMBEDDER = OnnxEmbedder()
        return _EMBEDDER


def reset_embedder() -> None:
    global _EMBEDDER
    with _EMBEDDER_LOCK:
        _EMBEDDER = None


# ---------------------------------------------------------------------------
# dense index + cache
# ---------------------------------------------------------------------------

@dataclass
class DenseIndex:
    sha: str
    chunk_ids: list[str]
    vectors: object          # numpy (n, dim)
    embedder: OnnxEmbedder

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        np = self.embedder.np
        if not query.strip() or not self.chunk_ids:
            return []
        query_vec = self.embedder.embed([query])[0]
        if not np.all(np.isfinite(query_vec)):
            return []
        scores = self.vectors @ query_vec
        order = np.argsort(-scores, kind="stable")[:top_k]
        return [(int(i), float(scores[i])) for i in order]


def _cache_paths(sha: str) -> tuple[Path, Path]:
    stem = f"{sha}-{MODEL_REVISION[:12]}-{TEXT_VERSION}-d{MODEL_DIM}"
    base = dense_cache_dir()
    return base / f"{stem}.npy", base / f"{stem}.json"


def _load_vectors(sha: str, index_chunk_ids: list[str]):
    import numpy as np

    if not cache_enabled():
        return None
    npy, meta_path = _cache_paths(sha)
    if not npy.is_file() or not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("dense cache metadata unreadable for %s", sha[:12])
        return None
    expected = {
        "sha": sha, "model_id": MODEL_ID, "revision": MODEL_REVISION,
        "text_version": TEXT_VERSION, "dim": MODEL_DIM, "normalise": NORMALISE,
        "chunk_ids": index_chunk_ids,
    }
    for key, value in expected.items():
        if meta.get(key) != value:
            LOGGER.warning("dense cache rejected for %s: %s differs", sha[:12], key)
            return None
    try:
        vectors = np.load(npy, allow_pickle=False)
    except (OSError, ValueError):
        return None
    if vectors.shape != (len(index_chunk_ids), MODEL_DIM):
        LOGGER.warning("dense cache rejected for %s: shape mismatch", sha[:12])
        return None
    if not np.all(np.isfinite(vectors)):
        LOGGER.warning("dense cache rejected for %s: non-finite values", sha[:12])
        return None
    if _sha256(npy) != meta.get("vectors_sha256"):
        LOGGER.warning("dense cache rejected for %s: checksum mismatch", sha[:12])
        return None
    return vectors


def _save_vectors(sha: str, chunk_ids: list[str], vectors) -> None:
    import numpy as np

    if not cache_enabled():
        return
    npy, meta_path = _cache_paths(sha)
    try:
        npy.parent.mkdir(parents=True, exist_ok=True)
        tmp = npy.with_suffix(".tmp.npy")
        np.save(tmp, vectors)
        tmp.replace(npy)
        meta = {
            "sha": sha, "model_id": MODEL_ID, "revision": MODEL_REVISION,
            "text_version": TEXT_VERSION, "dim": MODEL_DIM, "normalise": NORMALISE,
            "chunk_ids": chunk_ids, "count": len(chunk_ids),
            "vectors_sha256": _sha256(npy),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        return


def build_dense_index(codebase, chunks) -> DenseIndex:
    """Vectors for the given chunks, from cache when it validates."""
    sha = codebase.snapshot.sha
    memo = getattr(codebase, "_dense_index_memo", None)
    if memo is not None and memo.sha == sha and len(memo.chunk_ids) == len(chunks):
        return memo
    embedder = get_embedder()
    ids = [chunk_id(chunk) for chunk in chunks]
    cached = _load_vectors(sha, ids)
    if cached is not None:
        index = DenseIndex(sha, ids, cached, embedder)
        codebase._dense_index_memo = index
        return index
    vectors = embedder.embed([encode_text(chunk) for chunk in chunks])
    _save_vectors(sha, ids, vectors)
    index = DenseIndex(sha, ids, vectors, embedder)
    codebase._dense_index_memo = index
    return index
