"""Task A regression cases: pure deletions and empty diffs.

Six scenarios, each a real repository:
1. a guard inside a surviving function is removed;
2. a return branch is removed;
3. an exception handler is removed;
4. only a comment is removed;
5. a change outside the indexed definitions;
6. an empty diff.

Expected: every scenario with real executable change yields either a required
obligation or `unknown`, never a silent `sufficient`; comment-only deletion and an
empty diff are both `sufficient` but for explicitly different reasons.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

FIXED_DATE = "2026-01-01T00:00:00+0000"

BASE = {
    "src/__init__.py": "",
    "src/guard.py": '''"""Guard."""


def withdraw(balance, amount):
    """Withdraw when the amount is valid and covered."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    if amount > balance:
        return 0
    return balance - amount
''',
    "src/branch.py": '''"""Branch."""


def grade(score):
    """Return a letter grade."""
    if score >= 90:
        return "A"
    if score >= 60:
        return "pass"
    return "fail"
''',
    "src/handler.py": '''"""Handler."""


def parse_number(text):
    """Parse an integer, returning None when malformed."""
    try:
        return int(text)
    except ValueError:
        return None
''',
    "src/notes.py": '''"""Notes."""


def size(items):
    """Return the number of items."""
    # a comment that documents nothing executable
    return len(items)
''',
    "src/loose.py": '''"""Loose module-level code."""

CONFIG = {"retries": 1}
''',
}

HEAD = {
    # 1: the guard is removed, the function survives
    "A1-guard": {
        "file": "src/guard.py",
        "body": '''"""Guard."""


def withdraw(balance, amount):
    """Withdraw when the amount is valid and covered."""
    if amount > balance:
        return 0
    return balance - amount
''',
    },
    # 2: the last return branch is removed
    "A2-branch": {
        "file": "src/branch.py",
        "body": '''"""Branch."""


def grade(score):
    """Return a letter grade."""
    if score >= 90:
        return "A"
    return "pass"
''',
    },
    # 3: the exception handler is removed
    "A3-handler": {
        "file": "src/handler.py",
        "body": '''"""Handler."""


def parse_number(text):
    """Parse an integer, returning None when malformed."""
    return int(text)
''',
    },
    # 4: only a comment is removed
    "A4-comment": {
        "file": "src/notes.py",
        "body": '''"""Notes."""


def size(items):
    """Return the number of items."""
    return len(items)
''',
    },
    # 5: module-level executable code appears outside any indexed definition
    "A5-module": {
        "file": "src/loose.py",
        "body": '''"""Loose module-level code."""

import os

CONFIG = {"retries": 1}
os.environ.setdefault("RETRIES", "1")
''',
    },
    # 6: no change at all
    "A6-empty": {
        "empty": True,
    },
}

CASES = [
    {"id": "A1-guard", "head": "A1-guard",
     "expect": "insufficient",
     "note": "guard removal changes behaviour; the function must be re-read"},
    {"id": "A2-branch", "head": "A2-branch",
     "expect": "insufficient",
     "note": "removing a return branch changes behaviour"},
    {"id": "A3-handler", "head": "A3-handler",
     "expect": "insufficient",
     "note": "removing the handler changes the error surface"},
    {"id": "A4-comment", "head": "A4-comment",
     "expect": "sufficient",
     "note": "a comment-only deletion must not create a behaviour duty"},
    {"id": "A5-module", "head": "A5-module",
     "expect": "unknown",
     "note": "module-level change outside the index cannot be attributed"},
    {"id": "A6-empty", "head": "A6-empty",
     "expect": "sufficient",
     "note": "an empty diff is sufficient but says so explicitly"},
]


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update(GIT_AUTHOR_DATE=FIXED_DATE, GIT_COMMITTER_DATE=FIXED_DATE,
               GIT_AUTHOR_NAME="del", GIT_AUTHOR_EMAIL="del@example.com",
               GIT_COMMITTER_NAME="del", GIT_COMMITTER_EMAIL="del@example.com")
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=env).stdout


def build_case(root: Path, case: dict) -> Path:
    repo = root / case["id"]
    if repo.exists():
        return repo
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "del@example.com")
    _git(repo, "config", "user.name", "del")
    for rel, body in BASE.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")

    branch = "case/" + case["id"].lower()
    _git(repo, "checkout", "-q", "-b", branch)
    change = HEAD[case["head"]]
    if not change.get("empty"):
        (repo / change["file"]).write_text(change["body"], encoding="utf-8")
        _git(repo, "commit", "-qam", case["id"])
    _git(repo, "checkout", "-q", "main")
    return repo
