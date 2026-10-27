"""Parse a unified diff into display-ready hunks with line numbers.

Used by the web UI to show the actual change. Display uses context lines
(`--unified=3`); review logic still uses the zero-context diff.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .git_reader import (
    GIT_TIMEOUT_SECONDS,
    MAX_DIFF_BYTES,
    GitReaderError,
    validate_ref,
)

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$")


@dataclass
class DiffLine:
    kind: str          # "add" | "del" | "ctx"
    old_no: int | None
    new_no: int | None
    text: str


@dataclass
class Hunk:
    header: str
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    file_path: str
    change_type: str   # added | modified | deleted
    hunks: list[Hunk] = field(default_factory=list)


def get_display_diff(repo: Path, base: str, head: str) -> str:
    base = validate_ref(base)
    head = validate_ref(head)
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "diff", "--no-color", "--unified=3", f"{base}...{head}"],
            capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitReaderError("git command timed out") from exc
    if proc.returncode != 0:
        raise GitReaderError(proc.stderr.decode("utf-8", "replace").strip() or "git failed")
    if len(proc.stdout) > MAX_DIFF_BYTES:
        raise GitReaderError("diff too large")
    return proc.stdout.decode("utf-8", "replace")


def parse_display_diff(diff_text: str) -> list[FileDiff]:
    """Parse a context diff into per-file hunks with old/new line numbers."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", raw)
            path = m.group(2) if m else raw.split()[-1]
            current = FileDiff(file_path=path, change_type="modified")
            files.append(current)
            hunk = None
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current.change_type = "added"
            continue
        if raw.startswith("deleted file mode"):
            current.change_type = "deleted"
            continue
        if raw.startswith("@@"):
            m = _HUNK_RE.match(raw)
            if not m:
                continue
            old_no, new_no = int(m.group(1)), int(m.group(2))
            hunk = Hunk(header=raw)
            current.hunks.append(hunk)
            continue
        if hunk is None:
            continue
        if raw.startswith("+"):
            hunk.lines.append(DiffLine("add", None, new_no, raw[1:]))
            new_no += 1
        elif raw.startswith("-"):
            hunk.lines.append(DiffLine("del", old_no, None, raw[1:]))
            old_no += 1
        elif raw.startswith(" "):
            hunk.lines.append(DiffLine("ctx", old_no, new_no, raw[1:]))
            old_no += 1
            new_no += 1
    return files
