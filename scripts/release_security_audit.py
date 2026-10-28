"""Read-only security and dependency audit for the Release 1.0 plan."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"PR_GUARDIAN_API_KEY\s*=\s*['\"]?[A-Za-z0-9]"),
    re.compile(r"api_key\s*=\s*['\"]sk-"),
]
HOST_PATTERNS = [
    re.compile(r"C:\\\\Users\\\\[A-Za-z]"),
    re.compile(r"/Users/[A-Za-z]"),
    re.compile(r"OneDrive"),
]


def git(*args: str) -> str:
    proc = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                          capture_output=True, check=False)
    return proc.stdout.decode("utf-8", "replace")


def tracked_files() -> list[str]:
    return [line for line in git("ls-files").splitlines() if line]


def scan_secrets() -> list[str]:
    hits = []
    for rel in tracked_files():
        path = Path(rel)
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{rel}: {pattern.pattern}")
    return hits


def scan_host_paths() -> list[str]:
    hits = []
    for rel in tracked_files():
        if not rel.startswith(("docs/", "app/", "src/")):
            continue
        path = Path(rel)
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in HOST_PATTERNS:
            for match in pattern.finditer(text):
                hits.append(f"{rel}: {match.group(0)[:40]}")
    return hits


def scan_history() -> list[str]:
    """Secret literals in any branch tip or in the added lines of any commit."""
    hits = []
    for line in git("log", "--all", "-p", "--", "*.py", "*.json", "*.yml",
                    "*.yaml", "*.md").splitlines():
        if (line.startswith("+") and "sk-" in line and len(line) > 8
                and re.search(r"sk-[A-Za-z0-9]{16,}", line)):
            hits.append(line[:120])
    return hits[:10]


def scan_export_leaks() -> list[str]:
    hits = []
    for path in sorted(Path("docs").glob("*.json")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for pattern in HOST_PATTERNS:
            if pattern.search(text):
                hits.append(str(path))
                break
    return hits


def dependencies() -> dict:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    deps = re.findall(r'^\s*"([^"]+)"', text, re.MULTILINE)
    return {"declared": [d for d in deps if not d.startswith(("pr-guardian", "="))][:40]}


def transport_and_gates() -> dict:
    mcp = git("grep", "-n", "stdio", "--", "src")
    public = git("grep", "-n", "public_mode", "--", "src")
    return {
        "mcp_stdio_lines": [line for line in mcp.splitlines() if line][:5],
        "public_mode_lines": [line for line in public.splitlines() if line][:8],
    }


def allowlist() -> list[str]:
    return [line for line in git("grep", "-n", "src/", "--",
                                 "src/pr_guardian/agent_tools.py").splitlines()][:5]


def main() -> int:
    report = {
        "secret_hits": scan_secrets(),
        "host_path_hits": scan_host_paths()[:20],
        "history_secret_hits": scan_history(),
        "export_leak_files": scan_export_leaks(),
        "allowlist_lines": allowlist(),
        "transport": transport_and_gates(),
        "dependencies": dependencies(),
    }
    Path("docs/release-1.0").mkdir(parents=True, exist_ok=True)
    Path("docs/release-1.0/security-inventory.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
