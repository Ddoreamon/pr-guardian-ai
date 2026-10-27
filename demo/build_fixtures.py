"""Build the demo repository used by the tests and the web demo.

Kept as a thin CLI wrapper so `python demo/build_fixtures.py <dir>` still works;
the canonical fixture content lives in pr_guardian.demo_fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running this file directly from a checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pr_guardian.demo_fixtures import BRANCHES, build

__all__ = ["BRANCHES", "build"]

if __name__ == "__main__":
    build(Path(sys.argv[1]))
    print(f"demo repo built at {sys.argv[1]}")
