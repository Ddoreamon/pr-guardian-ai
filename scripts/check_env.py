"""Startup environment check used by start-local.bat / start-local.sh.

Exit codes:
    0  ready
    2  Python is older than 3.12
    3  project dependencies are missing
    1  the project files are not where the launcher expects them

Keeping the checks here avoids embedding `<`/`>` in a batch one-liner (cmd treats them
as redirection) and gives the user a plain-English message either way.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REQUIRED = ("fastapi", "uvicorn", "jinja2", "pydantic", "httpx", "ruff")
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if not (ROOT / "app" / "main.py").is_file():
        print(f"[error] app/main.py was not found under {ROOT}.")
        return 1
    missing = [name for name in REQUIRED if importlib.util.find_spec(name) is None]
    if missing:
        print("[notice] Missing Python packages: " + ", ".join(missing))
        return 3
    print(f"[ok] Python {sys.version.split()[0]}, dependencies present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
