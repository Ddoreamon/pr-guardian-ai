"""Security and behaviour tests for the read-only agent tools.

The tools read an immutable git snapshot, so these tests prove:
  * the snapshot is the requested commit, not the working tree or another branch
  * only `src/**/*.py` tracked at that commit is readable
  * every other path is refused with a specific error code
  * a call that finds nothing is a miss, not a successful retrieval
"""

import subprocess
from pathlib import Path

import pytest

from pr_guardian.agent_tools import (
    MAX_LINES_PER_READ,
    TOOL_OUTPUT_PREFIX,
    GitSnapshot,
    ToolError,
    TrustedCodebase,
    is_effective_hit,
    wrap_tool_output,
)

APP_MAIN = '''"""Application entry."""

LIMIT_MAIN_MARKER = 100


def apply(total):
    """Apply a discount when the total reaches the limit."""
    if total >= LIMIT_MAIN_MARKER:
        return total * 0.1
    return 0.0
'''

APP_FEATURE = '''"""Application entry."""

LIMIT_FEATURE_MARKER = 200


def apply(total):
    """Apply a discount when the total reaches the limit."""
    if total >= LIMIT_FEATURE_MARKER:
        return total * 0.1
    return 0.0
'''

CONFIG = '''"""Constants used by the app."""

DISCOUNT_RATE = 0.10
TAX_RATE: float = 0.20
'''


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True)
    return proc.stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "app.py").write_text(APP_MAIN, encoding="utf-8")
    (root / "src" / "config.py").write_text(CONFIG, encoding="utf-8")
    (root / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (root / "docs" / "notes.py").write_text("SECRET = 1\n", encoding="utf-8")
    (root / ".env").write_text("PR_GUARDIAN_API_KEY=sk-never-read\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "feature")
    (root / "src" / "app.py").write_text(APP_FEATURE, encoding="utf-8")
    _git(root, "commit", "-qam", "feature")
    return root


@pytest.fixture
def head(repo) -> TrustedCodebase:
    return TrustedCodebase.from_git(repo, "feature")


# ---- the snapshot is the requested commit, not the worktree ----------------

def test_snapshot_reads_the_requested_branch(repo):
    head = TrustedCodebase.from_git(repo, "feature")
    main = TrustedCodebase.from_git(repo, "main")

    feature_body = head.read_code_context("src/app.py", 1, 5)["content"]
    main_body = main.read_code_context("src/app.py", 1, 5)["content"]

    assert "LIMIT_FEATURE_MARKER" in feature_body
    assert "LIMIT_MAIN_MARKER" in main_body
    assert "LIMIT_MAIN_MARKER" not in feature_body


def test_snapshot_ignores_uncommitted_worktree_changes(repo, head):
    """A dirty working tree must not leak into tool reads."""
    (repo / "src" / "app.py").write_text("# WORKTREE_EDIT\nLIMIT = 999\n", encoding="utf-8")
    body = head.read_code_context("src/app.py", 1, 5)["content"]
    assert "WORKTREE_EDIT" not in body
    assert "LIMIT_FEATURE_MARKER" in body


def test_snapshot_ignores_checkout_of_another_branch(repo, head):
    _git(repo, "checkout", "-q", "main")
    body = head.read_code_context("src/app.py", 1, 5)["content"]
    assert "LIMIT_FEATURE_MARKER" in body


def test_payload_reports_the_commit_under_review(head):
    payload = head.read_code_context("src/app.py", 1, 2)
    assert payload["ref"] == "feature"


# ---- readable scope: tracked src/**/*.py only ------------------------------

def test_reads_tracked_python_under_src(head):
    payload = head.read_code_context("src/config.py", 1, 4)
    assert payload["ok"] is True
    assert "DISCOUNT_RATE" in payload["content"]
    assert "    1 | " in payload["content"]


def test_untracked_file_is_refused(repo, head):
    (repo / "src" / "untracked.py").write_text("X = 1\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        head.read_code_context("src/untracked.py", 1, 1)
    assert exc.value.code == "not_found"


@pytest.mark.parametrize("path", ["tests/test_app.py", "docs/notes.py", ".env",
                                  "src/../tests/test_app.py", "../outside.py",
                                  "/etc/passwd", "C:/Windows/x.py", "src/notes.txt",
                                  "README.md"])
def test_paths_outside_the_allowed_scope_are_refused(head, path):
    with pytest.raises(ToolError) as exc:
        head.read_code_context(path, 1, 2)
    assert exc.value.code in {"denied_path", "unsupported_type", "not_found"}


def test_symlink_entry_is_refused(repo):
    """A committed symlink (git mode 120000) must never be followed."""
    proc = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
                          input="LIMIT = 1\n", capture_output=True, text=True, check=True)
    sha = proc.stdout.strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{sha},src/link.py")
    _git(repo, "commit", "-q", "-m", "add symlink")

    updated = TrustedCodebase.from_git(repo, "HEAD")
    assert updated.snapshot.is_symlink("src/link.py") is True
    with pytest.raises(ToolError) as exc:
        updated.read_code_context("src/link.py", 1, 1)
    assert exc.value.code == "denied_path"


# ---- ranges ---------------------------------------------------------------

def test_read_is_capped_at_max_lines(head):
    with pytest.raises(ToolError) as exc:
        head.read_code_context("src/app.py", 1, MAX_LINES_PER_READ + 1)
    assert exc.value.code == "bad_range"


def test_read_beyond_eof_is_refused(head):
    with pytest.raises(ToolError) as exc:
        head.read_code_context("src/app.py", 999, 1000)
    assert exc.value.code == "bad_range"


@pytest.mark.parametrize("start,end", [(0, 2), (5, 3)])
def test_bad_ranges_refused(head, start, end):
    with pytest.raises(ToolError) as exc:
        head.read_code_context("src/app.py", start, end)
    assert exc.value.code == "bad_range"


# ---- symbol lookup, including constants -----------------------------------

@pytest.mark.parametrize("symbol,kind", [
    ("apply", "function"),
    ("DISCOUNT_RATE", "constant"),
    ("TAX_RATE", "annotated_constant"),
])
def test_finds_functions_and_module_constants(head, symbol, kind):
    payload = head.find_symbol_definition(symbol)
    assert payload["found"] is True
    assert payload["matches"], symbol
    assert payload["matches"][0]["kind"] == kind
    assert payload["matches"][0]["file_path"].endswith(".py")


def test_symbol_search_stays_inside_src(head):
    payload = head.find_symbol_definition("test_x")
    assert payload["matches"] == []
    assert payload["found"] is False


def test_unknown_symbol_is_a_miss_not_a_hit(head):
    payload = head.find_symbol_definition("no_such_symbol")
    assert payload["ok"] is True
    assert payload["match_count"] == 0
    assert is_effective_hit("find_symbol_definition", payload) is False


def test_read_hit_vs_empty_content(head):
    payload = head.read_code_context("src/app.py", 1, 2)
    assert is_effective_hit("read_code_context", payload) is True
    assert is_effective_hit("read_code_context", {"ok": True, "content": "   \n"}) is False
    assert is_effective_hit("read_code_context", {"ok": False}) is False


@pytest.mark.parametrize("symbol", ["", "   ", "has space", "1abc", "a" * 100, "x;y"])
def test_invalid_symbols_refused(head, symbol):
    with pytest.raises(ToolError) as exc:
        head.find_symbol_definition(symbol)
    assert exc.value.code == "bad_symbol"


def test_invalid_ref_is_refused(repo):
    with pytest.raises(ToolError) as exc:
        GitSnapshot(repo, "--upload-pack=evil")
    assert exc.value.code == "bad_ref"


# ---- output framing -------------------------------------------------------

def test_tool_output_is_labelled_untrusted(head):
    wrapped = wrap_tool_output(head.read_code_context("src/app.py", 1, 2))
    assert wrapped.startswith(TOOL_OUTPUT_PREFIX)
    assert "<<<TOOL_DATA" in wrapped and "TOOL_DATA>>>" in wrapped
