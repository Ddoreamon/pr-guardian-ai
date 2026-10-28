"""Task A and B regression tests: deletion-only diffs and shadow replay.

No model calls. Every fixture is a real repository at a pinned commit.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from pr_guardian.agent_tools import READ_CODE_CONTEXT, SEARCH_CODE, TrustedCodebase
from pr_guardian.evidence import EvidenceRegistry
from pr_guardian.git_reader import read_changed_files
from pr_guardian.mcp_client import MCPToolBackend
from pr_guardian.shadow import (
    REPLAY_EXACT,
    REPLAY_MISMATCH,
    REPLAY_SHA_MISMATCH,
    REPLAY_UNVERIFIABLE,
    replay_registry,
)
from pr_guardian.sufficiency import (
    INSUFFICIENT,
    SUFFICIENT,
    UNKNOWN,
    assess,
    classify_diff,
    derive_obligations,
)
from pr_guardian.tool_calling import ToolCallRecord, _execute
from scripts.deletion_cases import CASES as DEL_CASES
from scripts.deletion_cases import build_case as build_del
from scripts.robustness_cases import CASES as ROB_CASES
from scripts.robustness_cases import build_case as build_rob


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture(scope="module")
def del_repos():
    root = Path(tempfile.mkdtemp(prefix="v45-del-"))
    built = {}
    for case in DEL_CASES:
        repo = build_del(root, case)
        branch = "case/" + case["id"].lower()
        head = TrustedCodebase.from_git(repo, branch)
        base = TrustedCodebase.from_git(repo, "main")
        files, _ = read_changed_files(repo, "main", branch)
        built[case["id"]] = {"head": head, "base": base, "files": files,
                             "state": classify_diff(head, base, files)}
    return built


def _verdict(case, registry=None):
    head = case["head"]
    registry = registry or EvidenceRegistry(ref=head.snapshot_sha)
    obligations = derive_obligations(head, case["base"], case["files"])
    return assess(registry, obligations, head.snapshot_sha,
                  set(head.source_files()), diff_state=case["state"])


# ---- Task A ---------------------------------------------------------------

def test_removing_a_guard_is_insufficient(del_repos):
    assert _verdict(del_repos["A1-guard"]).status == INSUFFICIENT


def test_removing_a_return_branch_is_insufficient(del_repos):
    assert _verdict(del_repos["A2-branch"]).status == INSUFFICIENT


def test_removing_an_exception_handler_is_insufficient(del_repos):
    assert _verdict(del_repos["A3-handler"]).status == INSUFFICIENT


def test_comment_only_deletion_creates_no_duty(del_repos):
    case = del_repos["A4-comment"]
    assert case["state"] == "comment_only"
    result = _verdict(case)
    assert result.status == SUFFICIENT
    assert result.diff_state == "comment_only"
    assert result.obligations == []


def test_change_outside_the_index_is_unknown(del_repos):
    result = _verdict(del_repos["A5-module"])
    assert result.status == UNKNOWN
    assert result.unknown_obligations


def test_empty_diff_is_distinct_from_unanalysable(del_repos):
    empty = _verdict(del_repos["A6-empty"])
    unanalysable = _verdict(del_repos["A5-module"])
    assert empty.diff_state == "empty"
    assert unanalysable.diff_state != "empty"
    assert empty.status == SUFFICIENT
    assert unanalysable.status == UNKNOWN
    assert empty.explanation != unanalysable.explanation


def test_deleted_lines_produce_exactly_one_duty(del_repos):
    """A guard removal must not be counted once per deleted line."""
    case = del_repos["A1-guard"]
    obligations = derive_obligations(case["head"], case["base"], case["files"])
    assert len(obligations) == 1


def test_empty_registry_with_obligations_is_never_sufficient(del_repos):
    for name in ("A1-guard", "A2-branch", "A3-handler"):
        assert _verdict(del_repos[name]).status == INSUFFICIENT


def test_guard_removal_is_sufficient_once_the_definition_is_read(del_repos):
    case = del_repos["A1-guard"]
    obligations = derive_obligations(case["head"], case["base"], case["files"])
    span = obligations[0].required_span
    payload = case["head"].read_code_context("src/guard.py", span[0], span[1])
    registry = EvidenceRegistry(ref=case["head"].snapshot_sha)
    registry.record(ToolCallRecord(1, "read_code_context",
                                   {"file_path": "src/guard.py", "start_line": span[0],
                                    "end_line": span[1]}, True, "hit", "", "", 1, 0.0,
                                   0, "c1"), payload)
    assert _verdict(case, registry).status == SUFFICIENT


# ---- Task B: replay -------------------------------------------------------

@pytest.fixture(scope="module")
def rob_repo():
    root = Path(tempfile.mkdtemp(prefix="v45-rob-"))
    case = next(c for c in ROB_CASES if c["id"] == "R4-alias")
    repo = build_rob(root, case)
    return repo, TrustedCodebase.from_git(repo, "case/r4-alias")


@pytest.fixture(scope="module")
def long_repo():
    root = Path(tempfile.mkdtemp(prefix="v45-long-")) / "long-case"
    (root / "src").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "l@example.com")
    _git(root, "config", "user.name", "l")
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    body = "\n".join(f"VALUE_{index} = \"{'x' * 400}\"" for index in range(60))
    (root / "src" / "long.py").write_text(body + "\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _record(codebase, calls) -> list[dict]:
    registry = EvidenceRegistry(ref=codebase.snapshot_sha)
    for index, (name, args) in enumerate(calls):
        if name == READ_CODE_CONTEXT:
            payload = codebase.read_code_context(**args)
        elif name == SEARCH_CODE:
            payload = codebase.search_code(**args)
        else:
            payload = codebase.find_symbol_definition(**args)
        registry.record(ToolCallRecord(1, name, args, True, "hit", "", "", 1, 0.0, 0,
                                       f"c{index}"), payload)
    return registry.summary()


def test_replay_reproduces_a_complete_read(rob_repo):
    _repo, head = rob_repo
    sources = _record(head, [(READ_CODE_CONTEXT,
                              {"file_path": "src/fmt.py", "start_line": 1,
                               "end_line": 6})])
    registry, status, notes = replay_registry(head, sources)
    assert status == REPLAY_EXACT
    assert notes == []
    assert len(registry.entries) == 1
    assert registry.entries[0].complete_lines == [1, 2, 3, 4, 5, 6]


def test_replay_reproduces_a_truncated_read(long_repo):
    head = TrustedCodebase.from_git(long_repo, "main")
    sources = _record(head, [(READ_CODE_CONTEXT,
                              {"file_path": "src/long.py", "start_line": 1,
                               "end_line": 40})])
    assert sources[0]["truncated"] is True
    registry, status, _notes = replay_registry(head, sources)
    assert status == REPLAY_EXACT
    # the final physical line was cut, so it must not be claimed as complete
    assert registry.entries[0].complete_lines == sources[0]["complete_lines_list"]
    assert len(registry.entries[0].complete_lines) < 40


def test_replay_reproduces_search_candidates_without_making_them_citable(rob_repo):
    _repo, head = rob_repo
    sources = _record(head, [(SEARCH_CODE, {"query": "render", "top_k": 3})])
    registry, status, _notes = replay_registry(head, sources)
    assert status == REPLAY_EXACT
    assert {entry.kind for entry in registry.entries} == {"candidate"}
    assert all(not entry.citable for entry in registry.entries)


def test_mcp_and_local_evidence_replay_identically(rob_repo):
    repo, head = rob_repo
    calls = [(READ_CODE_CONTEXT, {"file_path": "src/fmt.py", "start_line": 1,
                                  "end_line": 6}),
             (SEARCH_CODE, {"query": "render", "top_k": 3})]
    local_sources = _record(head, calls)
    backend = MCPToolBackend(repo, "case/r4-alias")
    try:
        mcp_sources = []
        for index, (name, args) in enumerate(calls):
            record, payload = _execute(name, args, backend, 1)
            mcp_sources.append((record, payload))
    finally:
        backend.close()
    # the MCP payloads are recorded through the same registry, then replayed
    registry = EvidenceRegistry(ref=head.snapshot_sha)
    for record, payload in mcp_sources:
        registry.record(record, payload)
    mcp_summary = registry.summary()
    assert [entry["snippet_sha256"] for entry in mcp_summary] == [
        entry["snippet_sha256"] for entry in local_sources]
    _mcp_registry, mcp_status, _ = replay_registry(head, mcp_summary)
    _local_registry, local_status, _ = replay_registry(head, local_sources)
    assert mcp_status == local_status == REPLAY_EXACT


def test_evidence_from_another_commit_is_refused(rob_repo):
    _repo, head = rob_repo
    sources = _record(head, [(READ_CODE_CONTEXT,
                              {"file_path": "src/fmt.py", "start_line": 1,
                               "end_line": 6})])
    tampered = [dict(sources[0], ref="f" * 40)]
    _registry, status, notes = replay_registry(head, tampered)
    assert status == REPLAY_SHA_MISMATCH
    assert notes


def test_missing_source_is_refused(rob_repo):
    _repo, head = rob_repo
    sources = _record(head, [(READ_CODE_CONTEXT,
                              {"file_path": "src/fmt.py", "start_line": 1,
                               "end_line": 6})])
    tampered = [dict(sources[0], file_path="src/does_not_exist.py")]
    _registry, status, _notes = replay_registry(head, tampered)
    assert status in {REPLAY_MISMATCH, "source_missing"}


def test_corrupted_complete_lines_are_refused(rob_repo):
    _repo, head = rob_repo
    sources = _record(head, [(READ_CODE_CONTEXT,
                              {"file_path": "src/fmt.py", "start_line": 1,
                               "end_line": 6})])
    tampered = [dict(sources[0], complete_lines_list=[1, 2, 3, 4, 5, 6, 7])]
    _registry, status, _notes = replay_registry(head, tampered)
    assert status == REPLAY_MISMATCH


def test_old_report_without_line_list_is_unverifiable(rob_repo):
    _repo, head = rob_repo
    sources = _record(head, [(READ_CODE_CONTEXT,
                              {"file_path": "src/fmt.py", "start_line": 1,
                               "end_line": 6})])
    old = [{key: value for key, value in sources[0].items()
            if key not in {"complete_lines_list", "arguments"}}]
    registry, status, _notes = replay_registry(head, old)
    assert status == REPLAY_UNVERIFIABLE
    assert registry.entries == []


def test_unverifiable_replay_never_reports_sufficient(rob_repo):
    from pr_guardian.shadow import assess_report

    repo, head = rob_repo
    sources = _record(head, [(READ_CODE_CONTEXT,
                              {"file_path": "src/fmt.py", "start_line": 1,
                               "end_line": 6})])
    old = [{key: value for key, value in sources[0].items()
            if key not in {"complete_lines_list", "arguments"}}]
    report = {"snapshot_sha": head.snapshot_sha, "evidence_sources": old}
    base = TrustedCodebase.from_git(repo, "main")
    files, _ = read_changed_files(repo, "main", "case/r4-alias")
    payload = assess_report(report, head, base, files,
                            obligations=derive_obligations(head, base, files),
                            classifier=classify_diff)
    assert payload["replay_status"] == REPLAY_UNVERIFIABLE
    assert payload["sufficiency"]["status"] == UNKNOWN


def test_report_persistence_has_no_secrets(rob_repo):
    _repo, head = rob_repo
    sources = _record(head, [(READ_CODE_CONTEXT,
                              {"file_path": "src/fmt.py", "start_line": 1,
                               "end_line": 6})])
    blob = json.dumps(sources)
    assert "sk-" not in blob
    assert "C:\\\\" not in blob
    assert sources[0]["arguments"]["file_path"] == "src/fmt.py"
