"""Command-line entry point: `pr-guardian review --repo PATH --base main --head feature`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .git_reader import GitReaderError
from .pipeline import run_review
from .report import to_json, to_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pr-guardian")
    sub = parser.add_subparsers(dest="command", required=True)
    rv = sub.add_parser("review", help="Review a git diff between two refs")
    rv.add_argument("--repo", type=Path, required=True)
    rv.add_argument("--base", required=True)
    rv.add_argument("--head", required=True)
    rv.add_argument("--format", choices=["md", "json"], default="md")
    rv.add_argument("--no-llm", action="store_true", help="Skip the LLM step")
    rv.add_argument(
        "--run-tests", action="store_true",
        help="Run pytest on head. ONLY for trusted, locally-authored repositories.",
    )
    args = parser.parse_args(argv)

    try:
        report = run_review(
            args.repo.resolve(), args.base, args.head,
            use_llm=not args.no_llm, run_tests=args.run_tests,
        )
    except GitReaderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    # Windows consoles default to a legacy codepage; force UTF-8 so Chinese/symbol text prints correctly.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(to_json(report) if args.format == "json" else to_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
