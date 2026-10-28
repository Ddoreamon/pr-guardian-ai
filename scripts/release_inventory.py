"""Read-only release audit inventory. Never writes to the repository or GitHub.

Prints, for every release branch and PR: head, base, ancestry, commits since base,
touched files, and whether a later branch already contains it.

Run:
    python scripts/release_inventory.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

BRANCHES = {
    "PR #1 Function Calling": "feature/v2-function-calling",
    "PR #2 MCP": "feature/v2-mcp-server",
    "PR #3 Repository RAG": "feature/v3-repo-rag",
    "PR #4 Hybrid Dense": "feature/v3-hybrid-rag",
    "PR #5 Evidence/Completeness": "feature/v4-agent-reliability",
    "PR #6 Budget/Planner": "feature/v4-budget-efficiency",
    "PR #7 Sufficiency": "feature/v4-evidence-sufficiency",
}
BASE = "main"


def git(*args: str, repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=False).stdout.strip()


def is_ancestor(commit: str, ref: str, repo: Path) -> bool:
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor",
                           commit, ref], capture_output=True).returncode == 0


def main() -> int:
    repo = Path.cwd()
    if not (repo / ".git").exists():
        repo = Path(__file__).resolve().parents[1]
    head = git("rev-parse", "HEAD", repo=repo)
    main_sha = git("rev-parse", BASE, repo=repo)
    print(f"repository: {repo}")
    print(f"main: {main_sha}")
    print(f"checked-out HEAD: {head}")
    print()

    rows = []
    for label, branch in BRANCHES.items():
        sha = git("rev-parse", branch, repo=repo)
        base = git("rev-parse", f"{BASE}", repo=repo)
        if not is_ancestor(base, sha, repo):
            base = git("merge-base", BASE, branch, repo=repo)
        commits = git("log", "--oneline", f"{base}..{sha}", repo=repo).splitlines()
        files = git("diff", "--name-only", f"{base}...{sha}", repo=repo).splitlines()
        rows.append({
            "label": label, "branch": branch, "head": sha[:12],
            "commits": len(commits), "files": len(files),
            "in_v45": is_ancestor(sha, BRANCHES["PR #7 Sufficiency"], repo),
            "subjects": [c.split(" ", 1)[1] for c in commits][:6],
        })

    for row in rows:
        print(f"{row['label']} -> {row['branch']} @ {row['head']}")
        print(f"  commits since main: {row['commits']}, files: {row['files']}, "
              f"contained in PR #7 branch: {row['in_v45']}")
        for subject in row["subjects"]:
            print(f"    - {subject}")
        print()

    hybrid = BRANCHES["PR #4 Hybrid Dense"]
    print("hybrid ancestry:",
          "ancestor of PR #7 branch" if is_ancestor(hybrid, BRANCHES["PR #7 Sufficiency"], repo)
          else "NOT in the V4 chain (independent experiment)")
    print("merge-base hybrid/main:", git("merge-base", hybrid, BASE, repo=repo)[:12])
    Path("docs/release-1.0").mkdir(parents=True, exist_ok=True)
    (Path("docs/release-1.0") / "git-inventory.json").write_text(
        json.dumps({"main": main_sha, "rows": rows}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
