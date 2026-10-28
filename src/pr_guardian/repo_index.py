"""Repository-level code index and keyword (BM25) retrieval.

Design notes:

* Everything is read from a **pinned commit** through `TrustedCodebase`, so the
  same ``src/**/*.py`` allowlist, symlink refusal and 200 KB file cap apply as for
  the other tools. The working tree is never consulted.
* Chunks come from the Python AST: functions, methods, classes, module-level
  constants and (for very long definitions) controlled windows of them.
* The index is cached per commit SHA, so two commits can never share rankings.
* Retrieval is BM25 over code tokens plus symbol/import/call signals. This is
  **not** an embedding search and does not claim semantic understanding.

The index is built locally; nothing is uploaded anywhere.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

LOGGER = logging.getLogger("pr_guardian.repo_index")

INDEX_VERSION = 2
MAX_CHUNK_LINES = 60
WINDOW_LINES = 40
MAX_CHUNK_CHARS = 4000
SNIPPET_CHARS = 280
MAX_QUERY_CHARS = 200
MAX_TOP_K = 10

STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "when", "what",
    "which", "where", "how", "does", "have", "has", "are", "was", "were", "but",
    "not", "all", "any", "can", "could", "should", "would", "def", "self", "cls",
    "return", "none", "true", "false", "import", "class", "code", "file",
}

_CALL_SPLIT = re.compile(r"[^A-Za-z0-9_]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def stable_chunk_id(chunk: Chunk) -> str:
    """Stable identity for fusion: path + span + qualified name."""
    raw = f"{chunk.file_path}:{chunk.start_line}:{chunk.end_line}:{chunk.qualname}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def tokenize(text: str) -> list[str]:
    """Lowercase identifier-ish tokens, with snake_case and camelCase split."""
    out: list[str] = []
    for raw in _CALL_SPLIT.split(text):
        if not raw:
            continue
        for part in _CAMEL.split(raw):
            for piece in part.split("_"):
                token = piece.strip().lower()
                if len(token) < 2 or token in STOPWORDS or token.isdigit():
                    continue
                out.append(token)
    return out


@dataclass
class Chunk:
    sha: str
    file_path: str
    start_line: int
    end_line: int
    kind: str                      # function | class | method | constant | function_part
    name: str
    qualname: str
    text: str
    calls: list[str] = field(default_factory=list)
    part: int = 0
    parts: int = 1

    def as_dict(self) -> dict:
        return {
            "sha": self.sha,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "kind": self.kind,
            "name": self.name,
            "qualname": self.qualname,
            "text": self.text,
            "calls": self.calls,
            "part": self.part,
            "parts": self.parts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Chunk:
        return cls(**data)


def _call_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.append(func.id)
            elif isinstance(func, ast.Attribute):
                names.append(func.attr)
    return sorted(set(names))


def _split_span(start: int, end: int) -> list[tuple[int, int]]:
    """Controlled windows for a definition that is too long to index whole."""
    total = end - start + 1
    if total <= MAX_CHUNK_LINES:
        return [(start, end)]
    windows: list[tuple[int, int]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + WINDOW_LINES - 1, end)
        windows.append((cursor, stop))
        if stop == end:
            break
        cursor = stop + 1
    return windows


def _chunk_text(lines: list[str], start: int, end: int) -> str:
    text = "\n".join(lines[start - 1:end])
    if len(text) > MAX_CHUNK_CHARS:
        text = text[:MAX_CHUNK_CHARS]
    return text


def _module_imports(tree: ast.Module) -> dict[str, str]:
    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports[alias.asname or alias.name] = f"{module}.{alias.name}"
    return imports


def _definitions(tree: ast.Module) -> list[tuple[ast.AST, str, str]]:
    """(node, kind, qualname) for functions, methods, classes and constants."""
    found: list[tuple[ast.AST, str, str]] = []

    def visit_body(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{node.name}"
                found.append((node, "method" if prefix else "function", qual))
            elif isinstance(node, ast.ClassDef):
                qual = f"{prefix}{node.name}"
                found.append((node, "class", qual))
                visit_body(node.body, f"{qual}.")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not prefix:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        found.append((node, "constant", target.id))

    visit_body(tree.body, "")
    return found


def build_chunks(codebase) -> tuple[list[Chunk], dict[str, dict[str, str]]]:
    """Parse every allowed Python file at the pinned commit."""
    sha = codebase.snapshot.sha
    chunks: list[Chunk] = []
    imports_by_file: dict[str, dict[str, str]] = {}

    for path in codebase.source_files():
        try:
            source = codebase.read_source(path)
        except Exception:  # a broken file must not break the index
            LOGGER.warning("skipping unreadable file in index: %s", path, exc_info=True)
            continue
        lines = source.splitlines()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            total = max(1, len(lines))
            windows = _split_span(1, total)
            for index, (w_start, w_end) in enumerate(windows):
                chunks.append(Chunk(
                    sha=sha, file_path=path, start_line=w_start, end_line=w_end,
                    kind="unparsed", name=Path(path).stem,
                    qualname=Path(path).stem,
                    text=_chunk_text(lines, w_start, w_end),
                    part=index, parts=len(windows)))
            continue

        imports_by_file[path] = _module_imports(tree)
        for node, kind, qualname in _definitions(tree):
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start) or start
            windows = _split_span(start, end)
            name = qualname.split(".")[-1]
            for index, (w_start, w_end) in enumerate(windows):
                chunks.append(Chunk(
                    sha=sha, file_path=path, start_line=w_start, end_line=w_end,
                    kind="function_part" if len(windows) > 1 and kind != "constant"
                    else kind,
                    name=name, qualname=qualname,
                    text=_chunk_text(lines, w_start, w_end),
                    calls=_call_names(node) if kind != "constant" else [],
                    part=index, parts=len(windows),
                ))
    return chunks, imports_by_file


class RepoIndex:
    """BM25 index over AST chunks, cached per pinned SHA."""

    def __init__(self, chunks: list[Chunk], imports: dict[str, dict[str, str]],
                 sha: str) -> None:
        self.chunks = chunks
        self.imports = imports
        self.sha = sha
        self._tokens = [tokenize(f"{c.qualname} {c.text}") for c in chunks]
        self._lengths = [len(t) for t in self._tokens]
        self._avg_len = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        self._df: dict[str, int] = {}
        for tokens in self._tokens:
            for token in set(tokens):
                self._df[token] = self._df.get(token, 0) + 1

    # ---- scoring ---------------------------------------------------------
    def _idf(self, token: str) -> float:
        n = len(self.chunks)
        df = self._df.get(token, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5)) if n else 0.0

    def _bm25(self, index: int, query_tokens: list[str]) -> float:
        tokens = self._tokens[index]
        if not tokens or not self._avg_len:
            return 0.0
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        score = 0.0
        k1, b = 1.5, 0.75
        for token in query_tokens:
            tf = counts.get(token, 0)
            if not tf:
                continue
            denom = tf + k1 * (1 - b + b * len(tokens) / self._avg_len)
            score += self._idf(token) * tf * (k1 + 1) / denom
        return score

    def search(self, query: str, top_k: int = 5) -> dict:
        query = (query or "").strip()
        if not query:
            return {"ok": False, "error": {"code": "bad_query",
                                           "message": "query must be a non-empty string"}}
        if len(query) > MAX_QUERY_CHARS:
            return {"ok": False, "error": {"code": "bad_query",
                                           "message": f"query exceeds {MAX_QUERY_CHARS} chars"}}
        top_k = max(1, min(int(top_k), MAX_TOP_K))
        query_tokens = tokenize(query)
        wanted = set(query_tokens)
        if not wanted:
            return {"ok": True, "found": False, "query": query, "ref": self.sha,
                    "hits": [], "count": 0, "reason": "query had no searchable tokens"}

        scored: list[tuple[float, list[str], Chunk]] = []
        for index, chunk in enumerate(self.chunks):
            score = self._bm25(index, query_tokens)
            if score <= 0:
                continue
            why = [f"bm25={score:.3f}"]
            base = {chunk.name.lower(), chunk.qualname.lower().split(".")[-1]}
            compact_query = query.lower().replace("_", "").replace(" ", "")
            compact_names = {name.replace("_", "") for name in base}
            if query.lower() in base or compact_query in compact_names:
                score += 3.0
                why.append("exact_symbol")
            elif wanted & set(tokenize(chunk.name)):
                score += 2.0
                why.append("symbol_tokens")
            calls = {c.lower() for c in chunk.calls}
            if wanted & calls:
                score += 1.0
                why.append("calls_symbol")
            file_imports = self.imports.get(chunk.file_path, {})
            if wanted & {name.lower() for name in file_imports}:
                score += 1.0
                why.append("imported_symbol")
            if wanted & set(tokenize(chunk.file_path)):
                score += 0.5
                why.append("path_token")
            scored.append((score, why, chunk))

        scored.sort(key=lambda item: (-item[0], item[2].file_path,
                                      item[2].start_line, item[2].part))
        hits = []
        for score, why, chunk in scored[:top_k]:
            hits.append({
                "chunk_id": stable_chunk_id(chunk),
                "file_path": chunk.file_path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "kind": chunk.kind,
                "qualname": chunk.qualname,
                "part": chunk.part,
                "parts": chunk.parts,
                "score": round(score, 4),
                "why": why,
                "snippet": chunk.text[:SNIPPET_CHARS],
                "ref": self.sha,
            })
        if not hits:
            return {"ok": True, "found": False, "query": query, "ref": self.sha,
                    "hits": [], "count": 0, "reason": "no chunk matched the query"}
        return {"ok": True, "found": True, "query": query, "ref": self.sha,
                "hits": hits, "count": len(hits), "match_count": len(hits),
                "tokens_estimated": sum(len(h["snippet"]) for h in hits) // 4}


# ---------------------------------------------------------------------------
# cache: one file per commit SHA, never shared across commits
# ---------------------------------------------------------------------------

def cache_root() -> Path:
    """Absolute cache root.

    Anchored to the project rather than the process working directory: the MCP
    server runs as a child process with its own cwd, and a relative path would
    silently give it a different (empty) cache and a different model location.
    """
    override = os.environ.get("PR_GUARDIAN_CACHE_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / ".cache"


def _cache_dir() -> Path:
    return cache_root() / "repo_index"


def _cache_path(sha: str) -> Path:
    return _cache_dir() / f"{sha}-v{INDEX_VERSION}.json"


def cache_mode() -> str:
    """'verified' (default) or 'off'. Only an explicit 'off' disables the cache."""
    return "off" if (os.environ.get("PR_GUARDIAN_RAG_CACHE") or "").strip().lower() == "off" else "verified"


def snapshot_manifest(codebase) -> dict[str, list]:
    """Blob SHA and size for every file the index is allowed to include."""
    entries = codebase.snapshot.entries()
    manifest: dict[str, list] = {}
    for path in codebase.source_files():
        entry = entries.get(path)
        if entry is None:
            continue
        manifest[path] = [entry.blob_sha, entry.size]
    return manifest


def _expected_text(codebase, chunk: Chunk) -> str | None:
    try:
        source = codebase.read_source(chunk.file_path)
    except Exception:  # noqa: BLE001 - unreadable file means the cache is suspect
        return None
    lines = source.splitlines()
    if chunk.end_line > len(lines) or chunk.start_line < 1:
        return None
    return _chunk_text(lines, chunk.start_line, chunk.end_line)


def verify_cached(codebase, chunks: list[Chunk],
                  manifest: dict[str, list],
                  imports: dict | None = None) -> tuple[bool, str]:
    """Check that cached chunks really describe this commit.

    The manifest binds file paths to git blob SHAs, and every chunk's text is
    compared with the source read through `TrustedCodebase`. A cache that fails
    any check is discarded and rebuilt; it is never returned to a model.
    """
    current = snapshot_manifest(codebase)
    if set(current) != set(manifest):
        return False, "manifest file set differs from the commit"
    for path, (blob, size) in current.items():
        cached = manifest.get(path)
        if not cached or list(cached)[:2] != [blob, size]:
            return False, f"blob mismatch for {path}"

    allowed = set(current)
    for chunk in chunks:
        if chunk.file_path not in allowed:
            return False, f"chunk path outside the allowlist: {chunk.file_path}"
        if chunk.sha != codebase.snapshot.sha:
            return False, "chunk sha does not match the pinned commit"
        if not (1 <= chunk.start_line <= chunk.end_line):
            return False, f"bad line range in {chunk.file_path}"
        if chunk.end_line - chunk.start_line + 1 > MAX_CHUNK_LINES:
            return False, f"chunk exceeds the line cap in {chunk.file_path}"
        if len(chunk.text) > MAX_CHUNK_CHARS:
            return False, f"chunk exceeds the char cap in {chunk.file_path}"
        expected = _expected_text(codebase, chunk)
        if expected is None:
            return False, f"chunk line range outside {chunk.file_path}"
        if expected != chunk.text:
            return False, f"chunk text differs from the commit in {chunk.file_path}"

    # AST-derived fields steer ranking, so they are re-derived from the trusted
    # snapshot rather than trusted from the cache. A digest stored in the cache
    # would prove nothing: the same tampering could rewrite it.
    fresh, fresh_imports = build_chunks(codebase)
    if [c.as_dict() for c in chunks] != [c.as_dict() for c in fresh]:
        return False, "cached chunks differ from a fresh AST derivation"
    if imports is not None and imports != fresh_imports:
        return False, "cached imports differ from a fresh AST derivation"
    return True, "ok"


def load_cached(codebase) -> RepoIndex | None:
    """Load the cache only if it can be proven to belong to this commit."""
    if cache_mode() == "off":
        return None
    sha = codebase.snapshot.sha
    path = _cache_path(sha)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("discarding unreadable index cache for %s", sha[:12])
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") != INDEX_VERSION or data.get("sha") != sha:
        return None
    manifest = data.get("files")
    raw_chunks = data.get("chunks")
    imports = data.get("imports")
    if not isinstance(manifest, dict) or not isinstance(raw_chunks, list) or not isinstance(imports, dict):
        LOGGER.warning("discarding incomplete index cache for %s", sha[:12])
        return None
    try:
        chunks = [Chunk.from_dict(item) for item in raw_chunks]
    except (TypeError, KeyError):
        LOGGER.warning("discarding malformed index cache for %s", sha[:12])
        return None
    ok, reason = verify_cached(codebase, chunks, manifest, imports)
    if not ok:
        LOGGER.warning("discarding index cache for %s: %s", sha[:12], reason)
        return None
    # The freshly derived objects are authoritative; the cached file only proved
    # that the two agree. Dense vectors are the artefact worth caching.
    fresh, fresh_imports = build_chunks(codebase)
    return RepoIndex(fresh, fresh_imports, sha)


def save_cached(index: RepoIndex, manifest: dict[str, list]) -> None:
    path = _cache_path(index.sha)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": INDEX_VERSION,
            "sha": index.sha,
            "files": manifest,
            "chunks": [chunk.as_dict() for chunk in index.chunks],
            "imports": index.imports,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return  # a cache miss is always safe


def build_index(codebase) -> RepoIndex:
    """Build (or reuse) the index for this codebase instance.

    The memo lives on the codebase object: one review = one snapshot = one index,
    so repeated `search_code` calls do not re-derive anything. It is deliberately
    not global, which would leak an index across unrelated snapshots and tests.
    """
    memo = getattr(codebase, "_repo_index_memo", None)
    if memo is not None and memo.sha == codebase.snapshot.sha:
        return memo
    built = _build_index_uncached(codebase)
    codebase._repo_index_memo = built
    return built


def _build_index_uncached(codebase) -> RepoIndex:
    cached = load_cached(codebase)
    if cached is not None:
        return cached
    chunks, imports = build_chunks(codebase)
    index = RepoIndex(chunks, imports, codebase.snapshot.sha)
    if cache_mode() != "off":
        save_cached(index, snapshot_manifest(codebase))
    return index


def search_code(codebase, query: object, top_k: object = 5, *,
                index: RepoIndex | None = None) -> dict:
    """Tool entry point; validation mirrors the other tools."""
    if not isinstance(query, str) or not query.strip():
        return {"ok": False, "error": {"code": "bad_query",
                                       "message": "query must be a non-empty string"}}
    if isinstance(top_k, bool) or not isinstance(top_k, int):
        if isinstance(top_k, str) and top_k.strip().isdigit():
            top_k = int(top_k.strip())
        else:
            return {"ok": False, "error": {"code": "bad_top_k",
                                           "message": "top_k must be an integer"}}
    if not 1 <= top_k <= MAX_TOP_K:
        return {"ok": False, "error": {"code": "bad_top_k",
                                       "message": f"top_k must be between 1 and {MAX_TOP_K}"}}
    active = index or build_index(codebase)
    return active.search(query, top_k)


def index_fingerprint(sha: str) -> str:
    """Short stable id for logs/tests."""
    return hashlib.sha256(f"{sha}:{INDEX_VERSION}".encode()).hexdigest()[:12]
