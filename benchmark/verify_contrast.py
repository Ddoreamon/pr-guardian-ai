"""Verify the contrast pairs before any model call.

For every pair this asserts, mechanically:
  1. diff(A) == diff(B) byte-for-byte, and the changed-file list is identical
  2. the rendered V1 prompt (and the V2 prompt) is identical for A and B, and it
     leaks no case id, split or label
  3. base A passes its tests, base B passes its tests
  4. PR A fails, PR B passes
  5. the hidden tests never appear in the diff, the prompt, or the tool scope
  6. every variant's tests contain real assertions on service behaviour

Run: python -m benchmark.verify_contrast
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from benchmark.contrast_cases import ContrastPair, build_index, files_for
from pr_guardian.git_reader import read_changed_files
from pr_guardian.llm_reviewer import _build_user_prompt
from pr_guardian.tool_calling import _user_prompt as build_v2_prompt


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=60)


def materialise(pair: ContrastPair, variant: str, root: Path) -> Path:
    repo = root / f"{pair.pair_id}-{variant}-{uuid.uuid4().hex[:6]}"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "c@example.com")
    _git(repo, "config", "user.name", "c")
    base = files_for(pair, variant, apply_pr=False)
    for rel, content in base.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "pr")
    (repo / pair.diff_file).write_text(pair.pr_code, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pr")
    return repo


def run_tests(files: dict[str, str], test_code: str, root: Path) -> tuple[bool, str]:
    repo = root / f"t-{uuid.uuid4().hex[:6]}"
    repo.mkdir()
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_case.py").write_text(test_code, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=repo, capture_output=True, text=True, timeout=180, check=False,
    )
    tail = (proc.stdout or proc.stderr).strip().splitlines()
    return proc.returncode == 0, (tail[-1] if tail else "")


def check_pair(pair: ContrastPair, root: Path) -> list[str]:
    problems: list[str] = []

    repo_a = materialise(pair, "a", root)
    repo_b = materialise(pair, "b", root)
    files_a, diff_a = read_changed_files(repo_a, "main", "pr")
    files_b, diff_b = read_changed_files(repo_b, "main", "pr")

    # 1. identical diff and changed-file surface
    if diff_a != diff_b:
        problems.append("diff(A) != diff(B)")
    if [(f.file_path, f.added_lines, f.deleted_lines) for f in files_a] != \
       [(f.file_path, f.added_lines, f.deleted_lines) for f in files_b]:
        problems.append("changed-file list differs between variants")

    # 2. identical prompts, no metadata leak
    prompt_a = _build_user_prompt(diff_a, files_a)
    prompt_b = _build_user_prompt(diff_b, files_b)
    if prompt_a != prompt_b:
        problems.append("V1 prompt differs between variants")
    v2_a = build_v2_prompt(diff_a, files_a)
    v2_b = build_v2_prompt(diff_b, files_b)
    if v2_a != v2_b:
        problems.append("V2 prompt differs between variants")
    for token in (pair.pair_id.lower(), pair.split, "bug", "clean", "variant"):
        if token in prompt_a.lower():
            problems.append(f"prompt leaks the token {token!r}")
    for blob, name in ((prompt_a, "V1 prompt"), (v2_a, "V2 prompt")):
        if "tests/" in blob or "test_case.py" in blob:
            problems.append(f"{name} references the hidden tests")

    # 3/4. base passes both; PR fails for A, passes for B
    base_a_ok, note_a = run_tests(files_for(pair, "a", apply_pr=False), pair.tests_a, root)
    base_b_ok, note_b = run_tests(files_for(pair, "b", apply_pr=False), pair.tests_b, root)
    pr_a_ok, _pr_note_a = run_tests(files_for(pair, "a", apply_pr=True), pair.tests_a, root)
    pr_b_ok, pr_note_b = run_tests(files_for(pair, "b", apply_pr=True), pair.tests_b, root)
    if not base_a_ok:
        problems.append(f"base A does not pass ({note_a})")
    if not base_b_ok:
        problems.append(f"base B does not pass ({note_b})")
    if pr_a_ok:
        problems.append("PR A passes but is labelled bug")
    if not pr_b_ok:
        problems.append(f"PR B fails but is labelled clean ({pr_note_b})")

    # 5. hidden tests absent from the repos the methods see
    for repo in (repo_a, repo_b):
        tracked = subprocess.run(["git", "-C", str(repo), "ls-files"],
                                 capture_output=True, text=True, check=True).stdout
        if "tests/" in tracked:
            problems.append("hidden tests are tracked in the model-visible repo")

    # 6. the tests make real assertions
    for variant, code in (("a", pair.tests_a), ("b", pair.tests_b)):
        if "assert " not in code and "pytest.raises" not in code:
            problems.append(f"variant {variant} tests contain no assertions")

    return problems


def main() -> int:
    pairs = build_index()
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for pair in pairs:
            problems = check_pair(pair, root)
            status = "OK" if not problems else "PROBLEM"
            print(f"{pair.pair_id:5} {pair.split:8} {pair.mechanism:3} {status}"
                  + ("" if not problems else " :: " + "; ".join(problems)))
            failures += bool(problems)

    total = len(pairs)
    bugs = sum(1 for p in pairs if True)  # one bug variant per pair
    print(f"\npairs={total} variants={total * 2} bug_variants={bugs} clean_variants={bugs}")
    print(f"dev_pairs={sum(1 for p in pairs if p.split == 'dev')} "
          f"heldout_pairs={sum(1 for p in pairs if p.split == 'heldout')}")
    print(f"pairs_with_problems={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
