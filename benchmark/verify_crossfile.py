"""Verify cross-file case labels against executable tests.

For every case:
  base revision + hidden tests  -> tests must PASS
  PR revision   + hidden tests  -> bug cases must FAIL, clean cases must PASS

Run: python -m benchmark.verify_crossfile
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from benchmark.crossfile_cases import CrossFileCase, build_cases, files_for


def _write_repo(root: Path, files: dict[str, str], test_code: str) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_case.py").write_text(test_code, encoding="utf-8")


def _run_tests(root: Path) -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=root, capture_output=True, text=True, timeout=120, check=False,
    )
    return proc.returncode == 0


def check_case(case: CrossFileCase) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_repo(root, files_for(case, apply_pr=False), case.test_code)
        base_ok = _run_tests(root)

        root_pr = Path(tmp) / "pr"
        _write_repo(root_pr, files_for(case, apply_pr=True), case.test_code)
        pr_ok = _run_tests(root_pr)

    expected_pr_fails = case.has_bug
    label_ok = base_ok and (pr_ok != expected_pr_fails)
    detail = f"base={'pass' if base_ok else 'FAIL'} pr={'pass' if pr_ok else 'fail'}"
    return label_ok, detail


def main() -> int:
    cases = build_cases()
    bad = []
    for case in cases:
        ok, detail = check_case(case)
        print(f"{case.case_id:8} {case.split:8} has_bug={case.has_bug!s:5} {detail} "
              f"{'OK' if ok else 'MISMATCH'}")
        if not ok:
            bad.append(case.case_id)
    bugs = sum(c.has_bug for c in cases)
    print(f"\ncases={len(cases)} bugs={bugs} clean={len(cases) - bugs} "
          f"dev={sum(c.split == 'dev' for c in cases)} "
          f"heldout={sum(c.split == 'heldout' for c in cases)}")
    print(f"mismatches={len(bad)} {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
