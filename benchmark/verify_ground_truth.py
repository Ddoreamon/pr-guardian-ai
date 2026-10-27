"""Verify that every benchmark label agrees with its own test suite.

  has_bug=True  -> the PR's tests MUST fail
  has_bug=False -> the PR's tests MUST pass

Run:  python -m benchmark.verify_ground_truth
Exit code 1 if any label disagrees with the tests.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from benchmark.cases import build_cases


def check_case(case) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "tests").mkdir()
        (root / "src" / "__init__.py").write_text("", encoding="utf-8")
        (root / "src" / f"{case.module}.py").write_text(case.pr_code, encoding="utf-8")
        (root / "tests" / f"test_{case.module}.py").write_text(case.test_code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=root, capture_output=True, text=True, timeout=60, check=False,
        )
        failed = proc.returncode != 0
        return failed == case.has_bug, ("tests fail" if failed else "tests pass")


def main() -> int:
    cases = build_cases()
    mismatches = []
    for case in cases:
        ok, observed = check_case(case)
        status = "OK" if ok else "MISMATCH"
        print(f"{case.case_id:14} has_bug={case.has_bug!s:5} observed={observed:10} {status}")
        if not ok:
            mismatches.append(case.case_id)
    bugs = sum(c.has_bug for c in cases)
    print(f"\ncases={len(cases)} bugs={bugs} clean={len(cases) - bugs} "
          f"dev={sum(c.dev_split for c in cases)} heldout={sum(not c.dev_split for c in cases)}")
    print(f"mismatches={len(mismatches)} {mismatches}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
