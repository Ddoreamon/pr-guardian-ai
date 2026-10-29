"""Subprocess analyser for uploaded Python files.

Invoked as `python -I -m pr_guardian.upload_worker <work-dir>` and prints one JSON
object on stdout. The parent enforces a hard timeout and kills this process.

It **only** reads and parses files. It never imports, executes, compiles, tests or
installs the uploaded code; Ruff is the only external process, and it is a static
checker that itself does not execute the files it reads.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

MAX_FINDINGS_PER_FILE = 200
MAX_FINDINGS_TOTAL = 500
RUFF_TIMEOUT_S = 8.0

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pr_guardian.upload_heuristics import scan_tree


def _ruff(work: Path) -> tuple[str, list[dict]]:
    """Run Ruff over the work directory. Ruff parses; it does not execute."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache",
             "--output-format", "json", "--exit-zero", str(work)],
            capture_output=True, text=True, timeout=RUFF_TIMEOUT_S, check=False,
            cwd=str(work), env={"PATH": "", "PYTHONHOME": ""},
        )
    except subprocess.TimeoutExpired:
        return "timeout", []
    except OSError as exc:
        return f"error:{exc}", []
    if proc.returncode != 0 and proc.returncode != 0:
        pass
    text = proc.stdout.strip()
    if not text:
        return "empty", []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return "unparsable", []
    if not isinstance(parsed, list):
        return "unexpected", []
    return "ok", parsed


def analyse(work: Path) -> dict:
    files: list[dict] = []
    findings: list[dict] = []
    for path in sorted(work.rglob("*.py")):
        rel = path.relative_to(work).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            files.append({"path": rel, "lines": 0, "syntax_error": f"unreadable: {exc}"})
            continue
        lines = source.count("\n") + (0 if source.endswith("\n") else 1)
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError as exc:
            files.append({"path": rel, "lines": lines,
                          "syntax_error": f"{exc.msg} (line {exc.lineno})"})
            findings.append({
                "file_path": rel, "start_line": max(1, exc.lineno or 1),
                "end_line": max(1, exc.lineno or 1), "rule": "AST001",
                "severity": "high", "category": "syntax",
                "summary": f"SyntaxError: {exc.msg}",
                "explanation": "The file cannot be parsed, so no other check ran on it.",
                "suggested_fix": "Fix the syntax error; the rest of the file was skipped.",
                "source": "syntax",
            })
            continue
        files.append({"path": rel, "lines": lines, "syntax_error": ""})
        hits = scan_tree(tree)[:MAX_FINDINGS_PER_FILE]
        for heuristic, start, end in hits:
            findings.append({
                "file_path": rel, "start_line": start, "end_line": end,
                "rule": heuristic.rule, "severity": heuristic.severity,
                "category": "heuristic",
                "summary": heuristic.message,
                "explanation": ("Matched a statically visible code pattern. This is a "
                                "heuristic signal, not a proven defect."),
                "suggested_fix": heuristic.suggestion,
                "source": "heuristic",
            })

    status, ruff_items = _ruff(work)
    for item in ruff_items[:MAX_FINDINGS_PER_FILE]:
        try:
            location = item.get("location") or {}
            end_location = item.get("end_location") or location
            filename = str(item.get("filename") or "")
            rel = Path(filename).resolve().relative_to(work.resolve()).as_posix()
            findings.append({
                "file_path": rel,
                "start_line": int(location.get("row") or 1),
                "end_line": int(end_location.get("row") or location.get("row") or 1),
                "rule": str(item.get("code") or "RUFF"),
                "severity": _ruff_severity(str(item.get("code") or "")),
                "category": "lint",
                "summary": str(item.get("message") or "Ruff finding"),
                "explanation": "Reported by Ruff, a deterministic linter.",
                "suggested_fix": "",
                "source": "ruff",
            })
        except (ValueError, OSError, TypeError):
            continue

    seen: set[tuple] = set()
    unique: list[dict] = []
    for item in findings:
        key = (item["file_path"], item["rule"], item["start_line"], item["end_line"],
               item["summary"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return {"files": files, "findings": unique[:MAX_FINDINGS_TOTAL],
            "ruff_status": status}


def _ruff_severity(code: str) -> str:
    if code in {"invalid-syntax"} or code.startswith(("E9", "F8", "F7")):
        return "high"
    if code.startswith(("F", "E7", "B")):
        return "medium"
    return "low"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"error": "usage: upload_worker <work-dir>"}))
        return 2
    work = Path(argv[1])
    if not work.is_dir():
        print(json.dumps({"error": "work directory missing"}))
        return 2
    print(json.dumps(analyse(work)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
