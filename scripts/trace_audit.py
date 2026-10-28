"""Audit the real recorded tool traces: where did the budget actually go?

Reads the committed smoke results (no model calls, no re-runs) and classifies every
executed tool call:

  A  exact repeat        same tool + identical normalised arguments, executed before
  B  overlapping read    different arguments, but the delivered lines overlap an
                         earlier delivered range of the same file
  C  invalid arguments   the call failed argument validation
  D  empty search        a search that returned no hits
  E  budget denied       refused before execution
  F  necessary           everything else

Writes docs/v4.3-trace-audit.md and prints the counts.

Run:
    python scripts/trace_audit.py
"""

from __future__ import annotations

import json
from pathlib import Path

SOURCES = [
    ("docs/v4-evidence-smoke/results.json", "v4 evidence smoke"),
    ("docs/rag-smoke/results.json", "RAG smoke"),
    ("docs/mcp-smoke/results.json", "MCP smoke"),
]

OUT = Path("docs/v4.3-trace-audit.md")

READ_TOOLS = {"read_code_context", "find_symbol_definition"}


def _runs(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "runs" in data:
        return data["runs"]
    if "cases" in data:
        return data["cases"]
    return []


def _key(call: dict) -> str:
    return json.dumps({"tool": call["name"], "args": call.get("arguments") or {}},
                      sort_keys=True, ensure_ascii=False)


def _range_of(call: dict) -> tuple[str, int, int] | None:
    """The file/line window a read tool delivered, when the record carries it."""
    args = call.get("arguments") or {}
    path = args.get("file_path")
    start = args.get("start_line")
    end = args.get("end_line")
    if isinstance(path, str) and isinstance(start, int) and isinstance(end, int):
        return path, start, end
    return None


def _overlaps(call: dict, delivered: list[tuple[str, int, int]]) -> bool:
    window = _range_of(call)
    if window is None:
        return False
    path, start, end = window
    for other_path, other_start, other_end in delivered:
        if other_path == path and start <= other_end and other_start <= end:
            return True
    return False


def classify(runs: list[tuple[str, str, dict]]) -> list[dict]:
    """Classify every call across all recorded runs (runs are independent)."""
    rows: list[dict] = []
    for source, case, run in runs:
        seen: dict[str, str] = {}
        delivered: list[tuple[str, int, int]] = []
        for index, call in enumerate(run.get("tool_calls") or [], start=1):
            name = call.get("name", "")
            outcome = call.get("outcome", "")
            key = _key(call)

            if outcome == "budget_denied":
                category, why = "E", "refused before execution"
            elif outcome == "error":
                category, why = "C", f"invalid call ({call.get('error_code') or 'error'})"
            elif outcome == "miss":
                category, why = "D", "search returned nothing usable"
            elif key in seen:
                category, why = "A", f"identical to call #{seen[key]}"
            elif _overlaps(call, delivered):
                category, why = "B", "line window overlaps an earlier delivered range"
            else:
                category, why = "F", "new evidence for this review"

            window = _range_of(call)
            if category == "F" and window:
                delivered.append(window)
            if category != "E" and key not in seen:
                seen[key] = index

            rows.append({
                "source": source, "case": case, "index": index,
                "round": call.get("round"), "call_id": call.get("call_id", ""),
                "tool": name, "arguments": call.get("arguments") or {},
                "outcome": outcome, "category": category, "why": why,
                "requested_chars": call.get("result_chars", 0),
            })
    return rows


def main() -> int:
    runs: list[tuple[str, str, dict]] = []
    for rel, label in SOURCES:
        path = Path(rel)
        if not path.is_file():
            print(f"missing {rel}; skipped")
            continue
        for run in _runs(path):
            runs.append((label, run.get("case", "?"), run))

    rows = classify(runs)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    executed = [r for r in rows if r["category"] != "E"]
    total = len(rows)
    percent = {k: round(v / total * 100, 1) for k, v in counts.items()} if total else {}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(runs, rows, counts, percent, executed, total), encoding="utf-8")
    print(json.dumps({"runs": len(runs), "calls": total, "counts": counts,
                      "percent": percent}, indent=2))
    return 0


def render(runs, rows, counts, percent, executed, total) -> str:
    lines = [
        "# V4.3 trace audit — where the tool budget actually goes",
        "",
        "Every call below comes from a committed real-model smoke result. Nothing was",
        "re-run and no request was synthesised. Categories:",
        "",
        "| Code | Meaning |",
        "|---|---|",
        "| A | exact repeat: same tool + identical normalised arguments, executed before |",
        "| B | overlapping read: different arguments, delivered lines overlap an earlier range |",
        "| C | invalid arguments (validation failure) |",
        "| D | search returned nothing |",
        "| E | budget denied (never executed) |",
        "| F | necessary new evidence |",
        "",
        f"Sources: {len(runs)} recorded runs, {total} calls.",
        "",
        "## Totals",
        "",
        "| code | calls | share |",
        "|---|---|---|",
    ]
    for code in ("A", "B", "C", "D", "E", "F"):
        lines.append(f"| {code} | {counts.get(code, 0)} | {percent.get(code, 0.0)}% |")
    lines += ["", f"Executed calls: **{len(executed)}** (E excluded).", "",
              "> A search hit is a *candidate*: the V4.1 rules do not accept a snippet as",
              "> proof of a line read, so `search_code` → `read_code_context` of the same",
              "> file is an evidence read, not a duplicate. That is why it lands in F.",
              "", "## Per run", "",
              "| source | case | calls | A | B | C | D | E | F |",
              "|---|---|---|---|---|---|---|---|---|"]
    for source, case, _run in runs:
        subset = [r for r in rows if r["source"] == source and r["case"] == case]
        per = {code: sum(1 for r in subset if r["category"] == code)
               for code in "ABCDEF"}
        lines.append(f"| {source} | {case} | {len(subset)} | " +
                     " | ".join(str(per[c]) for c in "ABCDEF") + " |")

    lines += ["", "## Every call", "",
              "| source | case | # | tool | arguments | outcome | code | reason |",
              "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        args = json.dumps(row["arguments"], ensure_ascii=False)
        lines.append(f"| {row['source']} | {row['case']} | {row['index']} | "
                     f"{row['tool']} | `{args[:70]}` | {row['outcome']} | "
                     f"{row['category']} | {row['why']} |")
    lines += ["", "## What this implies for a cache", ""]
    if counts.get("A", 0) == 0:
        lines += [
            "**No exact repeats exist in the measured traces.** An exact-repeat cache",
            "would have saved nothing here, so it is not implemented (see the Go/No-Go",
            "section of the report).",
        ]
    else:
        lines += [
            f"{counts['A']} exact repeat(s) were found. That is the only population an",
            "exact-repeat cache could serve; see the Go/No-Go decision for the threshold.",
        ]
    if counts.get("B", 0):
        lines += [
            "",
            f"{counts['B']} overlapping read(s) exist. Those are *near*-duplicates: a",
            "cache keyed on exact arguments would not catch them, and serving a partial",
            "overlap from cache would change what the model was shown. They are a",
            "candidate for a different optimisation (range de-duplication), not this one.",
        ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
