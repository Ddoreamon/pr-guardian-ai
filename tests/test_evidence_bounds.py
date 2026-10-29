"""Strict boundaries: only fully delivered lines are citable, and only for this SHA."""

import subprocess
from pathlib import Path

import pytest

from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.evidence import (
    EVIDENCE_UNVERIFIED,
    EVIDENCE_VERIFIED,
    EvidenceEntry,
    EvidenceRegistry,
    numbered_lines,
    validate_ref,
)
from pr_guardian.models import EvidenceRef
from pr_guardian.tool_calling import ToolCallRecord

LONG = (
    '"""Long module."""\n\n\n'
    "def wide(net):\n"
    '    """Return something."""\n'
    + "".join(f"    step_{i} = net + {i}\n" for i in range(40))
    + "    return step_0\n"
)

ONE_LONG_LINE = '"""One line."""\n\n\ndef heavy(x):\n    return "' + ("a" * 4200) + '"\n'


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("PR_GUARDIAN_CACHE_DIR", str(tmp_path / ".cache"))
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "b@example.com")
    _git(root, "config", "user.name", "b")
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "wide.py").write_text(LONG, encoding="utf-8")
    (root / "src" / "heavy.py").write_text(ONE_LONG_LINE, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _record(name: str, call_id: str = "call_1", args: dict | None = None) -> ToolCallRecord:
    return ToolCallRecord(1, name, args or {}, True, "hit", "", "", 100, 0.01, 0, call_id)


def _ref(codebase, path: str, start: int, end: int, tool: str = "read_code_context"):
    return EvidenceRef(ref=codebase.snapshot_sha, file_path=path, start_line=start,
                       end_line=end, tool_name=tool)


# ---- A. only fully delivered lines are citable -----------------------------

def test_a_complete_read_makes_every_delivered_line_citable(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/wide.py", 1, 10)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_record("read_code_context"), payload)

    entry = registry.entries[0]
    assert entry.kind == "read"
    assert entry.complete_lines == list(range(1, 11))
    assert validate_ref(_ref(codebase, "src/wide.py", 4, 9), registry,
                        codebase.snapshot_sha, set(codebase.source_files()))[0] == EVIDENCE_VERIFIED
    # a line the read never covered is not delivered
    assert validate_ref(_ref(codebase, "src/wide.py", 30, 31), registry,
                        codebase.snapshot_sha, set(codebase.source_files())) == (
        EVIDENCE_UNVERIFIED, "not_delivered_to_model")


def test_long_function_split_across_two_reads_only_covers_what_each_read_saw(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    first = codebase.read_code_context("src/wide.py", 1, 10)
    registry.record(_record("read_code_context", "call_a"), first)
    second = codebase.read_code_context("src/wide.py", 11, 20)
    registry.record(_record("read_code_context", "call_b"), second)

    allowed = set(codebase.source_files())
    assert validate_ref(_ref(codebase, "src/wide.py", 5, 5), registry,
                        codebase.snapshot_sha, allowed)[0] == EVIDENCE_VERIFIED
    assert validate_ref(_ref(codebase, "src/wide.py", 15, 15), registry,
                        codebase.snapshot_sha, allowed)[0] == EVIDENCE_VERIFIED
    # a range straddling both reads was never delivered as a whole
    assert validate_ref(_ref(codebase, "src/wide.py", 9, 12), registry,
                        codebase.snapshot_sha, allowed)[0] == EVIDENCE_UNVERIFIED
    # and a range past the second read was never delivered at all
    assert validate_ref(_ref(codebase, "src/wide.py", 40, 45), registry,
                        codebase.snapshot_sha, allowed)[0] == EVIDENCE_UNVERIFIED


def test_truncated_payload_loses_its_last_line():
    """The final physical line may be cut mid-way, so it is not citable."""
    content = "    1 | a = 1\n    2 | b = 2\n    3 | c ="
    assert numbered_lines(content, truncated=True) == [1, 2]
    assert numbered_lines(content, truncated=False) == [1, 2, 3]


def test_a_single_very_long_line_is_not_citable_when_cut(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/heavy.py", 1, 5)
    # the tool caps the content; the last line is cut
    assert payload.get("truncated") is True
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_record("read_code_context"), payload)
    entry = registry.entries[0]
    assert 5 not in entry.complete_lines
    assert validate_ref(_ref(codebase, "src/heavy.py", 5, 5), registry,
                        codebase.snapshot_sha, set(codebase.source_files()))[0] == \
        EVIDENCE_UNVERIFIED
    # an earlier, fully delivered line still verifies
    assert validate_ref(_ref(codebase, "src/heavy.py", 1, 2), registry,
                        codebase.snapshot_sha, set(codebase.source_files()))[0] == \
        EVIDENCE_VERIFIED


def test_symbol_snippet_only_covers_its_visible_lines(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.find_symbol_definition("wide")
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_record("find_symbol_definition", "call_sym"), payload)

    entry = registry.entries[0]
    assert entry.kind == "symbol"
    assert entry.complete_lines
    first, last = min(entry.complete_lines), max(entry.complete_lines)
    allowed = set(codebase.source_files())
    assert validate_ref(_ref(codebase, "src/wide.py", first, first, "find_symbol_definition"),
                        registry, codebase.snapshot_sha, allowed)[0] == EVIDENCE_VERIFIED
    assert validate_ref(_ref(codebase, "src/wide.py", last + 1, last + 1),
                        registry, codebase.snapshot_sha, allowed)[0] == EVIDENCE_UNVERIFIED


def test_search_hits_are_candidates_not_reads(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.search_code("wide", 5)
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_record("search_code", "call_search"), payload)
    assert registry.entries
    assert all(e.kind == "candidate" for e in registry.entries)
    assert all(not e.citable for e in registry.entries)
    hit = payload["hits"][0]
    assert validate_ref(_ref(codebase, hit["file_path"], hit["start_line"],
                             hit["end_line"], "search_code"), registry,
                        codebase.snapshot_sha, set(codebase.source_files())) == (
        EVIDENCE_UNVERIFIED, "candidate_only")


def test_payload_without_rendered_lines_creates_no_entry(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_record("read_code_context"),
                    {"ok": True, "ref": codebase.snapshot_sha,
                     "file_path": "src/wide.py", "start_line": 1, "end_line": 5})
    assert registry.entries == []


# ---- B. every entry must carry this review's SHA ---------------------------

def test_payload_from_another_sha_is_refused(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/wide.py", 1, 5)
    payload = {**payload, "ref": "f" * 40}
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_record("read_code_context"), payload)
    assert registry.entries == []
    assert registry.rejected[0]["reason"] == "payload_ref_mismatch"
    assert validate_ref(_ref(codebase, "src/wide.py", 1, 2), registry,
                        codebase.snapshot_sha, set(codebase.source_files()))[0] == \
        EVIDENCE_UNVERIFIED


def test_payload_without_a_ref_is_refused(repo):
    codebase = TrustedCodebase.from_git(repo, "main")
    payload = codebase.read_code_context("src/wide.py", 1, 5)
    payload = {k: v for k, v in payload.items() if k != "ref"}
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.record(_record("read_code_context"), payload)
    assert registry.entries == []
    assert registry.rejected[0]["reason"] == "payload_ref_missing"


def test_entry_with_another_sha_never_verifies(repo):
    """Defence in depth: even a hand-built entry is checked."""
    codebase = TrustedCodebase.from_git(repo, "main")
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    registry.entries.append(EvidenceEntry(
        tool_call_id="call_forged", tool_name="read_code_context", ref="f" * 40,
        file_path="src/wide.py", start_line=1, end_line=5, hit=True,
        truncated=False, kind="read", complete_lines=[1, 2, 3, 4, 5]))
    status, reason = validate_ref(_ref(codebase, "src/wide.py", 2, 3), registry,
                                  codebase.snapshot_sha, set(codebase.source_files()))
    assert status == EVIDENCE_UNVERIFIED
    assert reason == "entry_sha_mismatch"
