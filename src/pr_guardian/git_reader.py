"""Read changed files and new-side line numbers from `git diff <base>...<head>`.

Security notes:
- Git is invoked with an argument list (never shell=True).
- Refs are validated against a conservative pattern before use.
- Output is size-limited and a timeout is enforced.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .models import ChangedFile, ChangeType

MAX_DIFF_BYTES = 1_000_000
GIT_TIMEOUT_SECONDS = 30
_REF_RE = re.compile(r"^[A-Za-z0-9._/\-^~]{1,200}$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class GitReaderError(Exception):
    """Raised for invalid refs, git failures, or oversized diffs."""


def validate_ref(ref: str) -> str:
    if not _REF_RE.match(ref) or ref.startswith("-"):
        raise GitReaderError(f"Invalid git ref: {ref!r}")
    return ref


def _run_git(repo: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitReaderError("git command timed out") from exc
    if proc.returncode != 0:
        raise GitReaderError(proc.stderr.decode("utf-8", "replace").strip() or "git failed")
    if len(proc.stdout) > MAX_DIFF_BYTES:
        raise GitReaderError("diff too large")
    return proc.stdout.decode("utf-8", "replace")


def get_raw_diff(repo: Path, base: str, head: str) -> str:
    base = validate_ref(base)
    head = validate_ref(head)
    return _run_git(repo, ["diff", "--no-color", "--unified=0", f"{base}...{head}"])


def parse_unified_diff(diff_text: str) -> list[ChangedFile]:
    """Parse a unified diff (zero context) into ChangedFile records.

    Line numbers are the NEW-file line numbers, so they can be matched
    against the source file that the reviewer actually sees.
    """
    files: list[ChangedFile] = []
    current: ChangedFile | None = None
    new_line = 0
    old_line = 0

    def flush() -> None:
        nonlocal current
        if current is not None:
            files.append(current)
        current = None

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            flush()
            m = re.match(r"diff --git a/(.+?) b/(.+)$", raw)
            path = m.group(2) if m else raw.split()[-1]
            current = ChangedFile(
                file_path=path,
                change_type=ChangeType.MODIFIED,
                language="python" if path.endswith(".py") else "other",
            )
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current.change_type = ChangeType.ADDED
        elif raw.startswith("deleted file mode"):
            current.change_type = ChangeType.DELETED
        elif raw.startswith("@@"):
            m = _HUNK_RE.match(raw)
            if not m:
                continue
            old_line = int(m.group(1))
            new_line = int(m.group(3))
        elif raw.startswith("+") and not raw.startswith("+++"):
            current.added_lines.append(new_line)
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            current.deleted_lines.append(old_line)
            old_line += 1
    flush()
    return files


def read_changed_files(repo: Path, base: str, head: str) -> tuple[list[ChangedFile], str]:
    """Return (changed files, raw diff text)."""
    diff = get_raw_diff(repo, base, head)
    return parse_unified_diff(diff), diff
