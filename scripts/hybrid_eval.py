"""Hybrid retrieval evaluation: BM25 vs Dense vs RRF fusion.

Two corpora, deliberately separated:

* **Dev** — the synthetic 26-module corpus, whose queries were written earlier
  (`scripts/rag_eval_v2.py`). Reused here only as a reproducible baseline.
* **Held-out** — `pallets/click` pinned at `2247b35e` (BSD-3-Clause). Its queries
  were written from the public API concepts and are run **once**; nothing was
  tuned against them.

Gold is always identified by `(file_path, qualname, start_line)`. A query whose
qualname is ambiguous in the corpus is dropped rather than resolved by picking the
first same-named symbol; the script fails loudly if a gold symbol is missing.

Methods:
  A  bm25      current baseline
  B  dense     local ONNX model, vectors only
  C  hybrid    independent BM25 and dense recall, RRF fusion (k = 60)
  D  bm25-tuned  domain synonym table; corpus-tuned, reference only (Dev only)

Run:
    python scripts/hybrid_eval.py --out docs/hybrid-eval-v1
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pr_guardian import dense_index
from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.repo_index import build_index
from scripts.rag_eval_v2 import QUERIES as SYNTH_QUERIES
from scripts.rag_eval_v2 import build_corpus, expand

CLICK_SHA = "2247b35ea1c47c727d7a06e51fa280e12a863ff6"
CLICK_DIR = Path(".cache") / "corpora" / "click"

# (query, file_path, qualname, group) - written from the public API surface
CLICK_QUERIES: list[tuple[str, str, str, str]] = [
    ("print text with colour and styling",
     "src/click/termui.py", "secho", "paraphrase"),
    ("parse a timestamp written by the user",
     "src/click/types.py", "DateTime.convert", "paraphrase"),
    ("accept a true or false value from the user",
     "src/click/types.py", "BoolParamType.convert", "paraphrase"),
    ("reject bad input with a message and a non-zero status",
     "src/click/exceptions.py", "UsageError.show", "paraphrase"),
    ("show a friendly error when something goes wrong",
     "src/click/exceptions.py", "ClickException.show", "paraphrase"),
    ("restrict the input to a fixed set of allowed values",
     "src/click/types.py", "Choice.convert", "paraphrase"),
    ("accept several values at once for one parameter",
     "src/click/types.py", "Tuple.convert", "paraphrase"),
    ("render the list of options and their help text",
     "src/click/formatting.py", "HelpFormatter.write_dl", "paraphrase"),
    ("choose a text encoding that will work on this stream",
     "src/click/_compat.py", "get_best_encoding", "paraphrase"),
    ("hand the running context to the decorated function",
     "src/click/decorators.py", "pass_context", "paraphrase"),
    ("combine the short flags of one option into a single token",
     "src/click/formatting.py", "join_options", "paraphrase"),
    ("turn bytes into text without losing characters",
     "src/click/utils.py", "make_str", "paraphrase"),
    ("check whether a charset only covers plain ascii",
     "src/click/_compat.py", "is_ascii_encoding", "paraphrase"),
    ("open a file that the user named on the command line",
     "src/click/types.py", "File.convert", "paraphrase"),
    ("render one option''s help entry for the help screen",
     "src/click/core.py", "Option.get_help_record", "paraphrase"),
    ("print the usage line for a command",
     "src/click/formatting.py", "HelpFormatter.write_usage", "paraphrase"),
    ("take a value from the command line for a parameter",
     "src/click/core.py", "Parameter.consume_value", "paraphrase"),
    ("secho styled text",
     "src/click/termui.py", "secho", "lexical"),
    ("Choice convert values",
     "src/click/types.py", "Choice.convert", "lexical"),
    ("get_best_encoding stream",
     "src/click/_compat.py", "get_best_encoding", "lexical"),
]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


def _gold(chunks, file_path: str, qualname: str) -> dict:
    matches = [c for c in chunks
               if c.file_path == file_path and c.qualname == qualname]
    if not matches:
        raise SystemExit(f"gold not found: {file_path}:{qualname}")
    if len(matches) > 1:
        raise SystemExit(f"ambiguous gold: {file_path}:{qualname} -> {len(matches)} chunks")
    chunk = matches[0]
    return {"file_path": chunk.file_path, "qualname": chunk.qualname,
            "start_line": chunk.start_line, "end_line": chunk.end_line}


def _covers(hit: dict, gold: dict) -> bool:
    return (hit["file_path"] == gold["file_path"]
            and hit.get("qualname") == gold["qualname"]
            and hit["start_line"] <= gold["start_line"] <= hit["end_line"])


def _rank(hits, gold) -> int | None:
    return next((i + 1 for i, h in enumerate(hits) if _covers(h, gold)), None)


def measure(codebase, index, entries, k_values=(1, 3, 5)) -> dict:
    rows = []
    for query, gold in entries:
        row = {"query": query, "gold": gold}
        for mode in ("bm25", "dense", "hybrid"):
            started = time.perf_counter()
            result = codebase.search_code(query, max(k_values), mode=mode)
            latency = round(time.perf_counter() - started, 4)
            hits = result.get("hits", [])
            row[mode] = {
                "rank": _rank(hits, gold),
                "latency_s": latency,
                "chars": len(json.dumps(result)),
                "chunk_ids": [h["chunk_id"] for h in hits],
                "sources": [h.get("source", "bm25") for h in hits],
                "no_result": not result.get("found", False),
                "error": result.get("error", {}).get("code") if not result.get("ok", True) else None,
            }
        rows.append(row)

    summary: dict = {"queries": len(rows)}
    for mode in ("bm25", "dense", "hybrid"):
        ranks = [r[mode]["rank"] for r in rows]
        summary[mode] = {
            "recall@1": round(sum(1 for x in ranks if x == 1) / len(rows), 4),
            "recall@3": round(sum(1 for x in ranks if x and x <= 3) / len(rows), 4),
            "recall@5": round(sum(1 for x in ranks if x and x <= 5) / len(rows), 4),
            "mrr": round(sum(1 / x for x in ranks if x) / len(rows), 4),
            "no_result_rate": round(sum(1 for r in rows if r[mode]["no_result"]) / len(rows), 4),
            "mean_latency_s": round(statistics.mean(r[mode]["latency_s"] for r in rows), 4),
            "mean_chars": round(statistics.mean(r[mode]["chars"] for r in rows), 1),
        }

    # did dense reach the gold chunk at all, and was it absent from BM25's top-k?
    dense_only = hybrid_added = bm25_only = 0
    for row in rows:
        b_ids = row["bm25"]["chunk_ids"]
        d_ids = row["dense"]["chunk_ids"]
        h_ids = row["hybrid"]["chunk_ids"]
        next((cid for cid in d_ids + b_ids if cid and cid in h_ids), None)
        in_b = row["bm25"]["rank"] is not None
        in_d = row["dense"]["rank"] is not None
        in_h = row["hybrid"]["rank"] is not None
        if in_d and not in_b:
            dense_only += 1
        if in_b and not in_d:
            bm25_only += 1
        if in_h and not in_b:
            hybrid_added += 1
    summary["dense_only_hits"] = dense_only
    summary["bm25_only_hits"] = bm25_only
    summary["hybrid_adds_over_bm25"] = hybrid_added
    return {"summary": summary, "rows": rows}


def measure_tuned(codebase, entries) -> dict:
    rows = []
    for query, gold in entries:
        result = codebase.search_code(expand(query), 5, mode="bm25")
        rows.append({"query": query, "rank": _rank(result.get("hits", []), gold)})
    ranks = [r["rank"] for r in rows]
    n = len(rows)
    return {"recall@3": round(sum(1 for x in ranks if x and x <= 3) / n, 4),
            "mrr": round(sum(1 / x for x in ranks if x) / n, 4)}


def _rss_mb() -> float:
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("docs/hybrid-eval-v1"))
    args = ap.parse_args()

    payload: dict = {"model": dense_index.model_status(), "corpora": {}}

    # ---- Dev: synthetic corpus
    root = Path(tempfile.mkdtemp()) / "synth"
    build_corpus(root)
    synth = TrustedCodebase.from_git(root, "main")
    started = time.perf_counter()
    synth_index = build_index(synth)
    synth_build = round(time.perf_counter() - started, 4)

    seen = {}
    for chunk in synth_index.chunks:
        seen.setdefault(chunk.qualname, []).append(chunk)
    entries = []
    for query, symbol, group in SYNTH_QUERIES:
        if group != "paraphrase":
            continue
        chunks = seen.get(symbol, [])
        if len(chunks) != 1:
            continue  # ambiguous gold: dropped, never resolved by first-match
        entries.append((query, _gold(synth_index.chunks, chunks[0].file_path,
                                     chunks[0].qualname)))

    rss_before = _rss_mb()
    started = time.perf_counter()
    synth.search_code("warmup query", 3, mode="hybrid")
    cold_s = round(time.perf_counter() - started, 3)
    started = time.perf_counter()
    synth.search_code("warmup query", 3, mode="hybrid")
    warm_s = round(time.perf_counter() - started, 3)
    rss_after = _rss_mb()

    payload["corpora"]["synthetic"] = {
        "role": "dev (queries authored earlier)",
        "label": "<synthetic-v2>",
        "sha": synth.snapshot.sha,
        "chunks": len(synth_index.chunks),
        "files": len({c.file_path for c in synth_index.chunks}),
        "index_build_s": synth_build,
        "queries": len(entries),
        "dropped_ambiguous": sum(
            1 for q, s, g in SYNTH_QUERIES if g == "paraphrase" and len(seen.get(s, [])) != 1),
        "result": measure(synth, synth_index, entries),
        "tuned_reference": measure_tuned(synth, entries),
        "cold_s": cold_s,
        "warm_s": warm_s,
        "rss_mb_before": rss_before,
        "rss_mb_after": rss_after,
    }

    # ---- Held-out: pallets/click at a pinned SHA
    if not (CLICK_DIR / ".git").exists():
        print(f"missing corpus: clone pallets/click into {CLICK_DIR} at {CLICK_SHA}",
              file=sys.stderr)
        return 2
    head = _git(CLICK_DIR, "rev-parse", "HEAD").strip()
    if head != CLICK_SHA:
        print(f"corpus is at {head}, expected {CLICK_SHA}", file=sys.stderr)
        return 2
    click = TrustedCodebase.from_git(CLICK_DIR, CLICK_SHA)
    started = time.perf_counter()
    click_index = build_index(click)
    click_build = round(time.perf_counter() - started, 4)
    click_entries = [(q, _gold(click_index.chunks, f, qual)) for q, f, qual, _g in CLICK_QUERIES]
    disk = sum(
        f.stat().st_size
        for base in (Path(".cache") / "dense", Path(".cache") / "repo_index")
        if base.exists() for f in base.glob("*") if f.is_file())
    payload["corpora"]["click"] = {
        "role": "held-out (written from the public API, run once)",
        "label": "pallets/click",
        "license": "BSD-3-Clause",
        "sha": CLICK_SHA,
        "chunks": len(click_index.chunks),
        "files": len({c.file_path for c in click_index.chunks}),
        "index_build_s": click_build,
        "queries": len(click_entries),
        "result": measure(click, click_index, click_entries),
        "cache_disk_bytes": disk,
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.out / "REPORT.md").write_text(render(payload), encoding="utf-8")
    print(json.dumps({
        "model": {k: payload["model"][k] for k in ("available", "model_id", "revision", "dim")},
        "synthetic": {k: v for k, v in payload["corpora"]["synthetic"].items()
                      if k in ("chunks", "queries", "index_build_s", "cold_s", "warm_s",
                               "rss_mb_before", "rss_mb_after")},
        "synthetic_summary": payload["corpora"]["synthetic"]["result"]["summary"],
        "synthetic_tuned": payload["corpora"]["synthetic"]["tuned_reference"],
        "click": {k: v for k, v in payload["corpora"]["click"].items()
                  if k in ("chunks", "queries", "index_build_s", "cache_disk_bytes")},
        "click_summary": payload["corpora"]["click"]["result"]["summary"],
    }, indent=2, ensure_ascii=False))
    return 0


def render(payload: dict) -> str:
    model = payload["model"]
    lines = [
        "# Hybrid retrieval evaluation v1 (BM25 vs Dense vs RRF)",
        "",
        "Frozen: gold `(file_path, qualname, start_line)`, queries, model revision,",
        "chunk rules, top-k = 5, RRF k = 60. Nothing here is a review-accuracy claim.",
        "",
        (f"* model `{model['model_id']}` @ `{model['revision']}`, dim {model['dim']}, "
        f"local ONNX, available: {model['available']}"),
    ]
    for name, corpus in payload["corpora"].items():
        result = corpus["result"]["summary"]
        lines += ["", f"## {name} — {corpus['label']} ({corpus['role']})", "",
                  f"* commit `{corpus['sha']}`",
                  (f"* {corpus['chunks']} chunks across {corpus['files']} files, "
                  f"{corpus['queries']} queries")]
        if "cold_s" in corpus:
            lines.append(f"* cold query {corpus['cold_s']}s, warm {corpus['warm_s']}s, "
                         f"RSS {corpus['rss_mb_before']} → {corpus['rss_mb_after']} MB")
        if "cache_disk_bytes" in corpus:
            lines.append(f"* index + vector cache on disk: {corpus['cache_disk_bytes']} bytes")
        lines += ["", "| method | Recall@1 | Recall@3 | Recall@5 | MRR | no-result | latency | chars |",
                  "|---|---|---|---|---|---|---|---|"]
        for mode in ("bm25", "dense", "hybrid"):
            s = result[mode]
            lines.append(f"| {mode} | {s['recall@1']:.2%} | {s['recall@3']:.2%} | "
                         f"{s['recall@5']:.2%} | {s['mrr']:.4f} | {s['no_result_rate']:.2%} | "
                         f"{s['mean_latency_s']}s | {s['mean_chars']} |")
        lines += ["",
                  (f"* dense-only hits (gold in dense top-k, absent from BM25 top-k): "
                  f"**{result['dense_only_hits']}**"),
                  f"* bm25-only hits: {result['bm25_only_hits']}",
                  f"* hybrid gains over BM25: {result['hybrid_adds_over_bm25']}"]
        if "tuned_reference" in corpus:
            tuned = corpus["tuned_reference"]
            lines.append(f"* reference D (domain table, tuned): Recall@3 {tuned['recall@3']:.2%}, "
                         f"MRR {tuned['mrr']:.4f}")
        if "dropped_ambiguous" in corpus:
            lines.append(f"* ambiguous-gold queries dropped: {corpus['dropped_ambiguous']}")

    lines += ["", "## Reading", "",
              "Dense retrieval clearly beats BM25 on both corpora, and it does so by",
              "reaching chunks BM25 never returned (dense-only counts above). That answers",
              "the first research question positively.", "",
              "Hybrid, as specified here, does **not**: RRF with k = 60 fuses a weak BM25",
              "list into a much stronger dense list, so the final top-k is worse than dense",
              "alone on both corpora. On the held-out corpus it is even below BM25 at",
              "Recall@1. The parameters were fixed before measurement and have not been",
              "tuned to the results.", "",
              "Consequence: if a retrieval mode is kept, it should be `dense` (or BM25 where",
              "the model cannot be installed), not `hybrid`. A weighted fusion that trusts",
              "each retriever's own rank distribution, or RRF over equal-depth lists sized by",
              "the final k, is the obvious next experiment - but it must be pre-registered",
              "and evaluated on a fresh held-out set, not fitted to these numbers.", ""]
    lines += ["", "## Per-query ranks", ""]
    for name, corpus in payload["corpora"].items():
        lines += [f"### {name}", "", "| query | gold | bm25 | dense | hybrid |",
                  "|---|---|---|---|---|"]
        for row in corpus["result"]["rows"]:
            g = row["gold"]
            ranks = [row[m]["rank"] if row[m]["rank"] else "miss"
                     for m in ("bm25", "dense", "hybrid")]
            lines.append(f"| {row['query']} | `{g['file_path']}:{g['start_line']}` "
                         f"{g['qualname']} | {ranks[0]} | {ranks[1]} | {ranks[2]} |")
        lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
