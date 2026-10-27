"""Run the Ruff CLI on changed Python files and convert results to findings.

Ruff only sees files that exist in the checked-out head revision. Patches are
never fed to Ruff as if they were complete files.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .models import ChangedFile, FindingSource, ReviewFinding, VerificationStatus

RUFF_TIMEOUT_SECONDS = 60


def run_ruff(repo: Path, files: list[ChangedFile]) -> tuple[list[ReviewFinding], list[str]]:
    """Return (findings, notes). Notes explain any skipped analysis."""
    notes: list[str] = []
    py_files = [f for f in files if f.language == "python" and f.change_type.value != "deleted"]
    if not py_files:
        notes.append("Ruff skipped: no changed Python files.")
        return [], notes

    targets = [str(repo / f.file_path) for f in py_files]
    # Run Ruff through the current interpreter so it matches the installed dependency
    # and does not depend on PATH.
    cmd = [
        sys.executable, "-m", "ruff", "check", "--output-format", "json", "--no-cache",
        "--exit-zero", *targets,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=RUFF_TIMEOUT_SECONDS,
            check=False, cwd=str(repo),
        )
    except FileNotFoundError:
        notes.append("Ruff skipped: ruff executable not found.")
        return [], notes
    except subprocess.TimeoutExpired:
        notes.append("Ruff skipped: timed out.")
        return [], notes

    try:
        diagnostics = json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
    except json.JSONDecodeError:
        notes.append("Ruff skipped: could not parse JSON output.")
        return [], notes

    findings: list[ReviewFinding] = []
    for i, d in enumerate(diagnostics, start=1):
        try:
            rel = Path(d["filename"]).resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            rel = d["filename"]
        line = int(d["location"]["row"])
        end_line = int(d.get("end_location", {}).get("row", line))
        code = d.get("code") or "unknown"
        findings.append(
            ReviewFinding(
                id=f"ruff-{i}",
                file_path=rel,
                start_line=line,
                end_line=max(line, end_line),
                category="static",
                severity="low",
                summary=f"[{code}] {d.get('message', '')}",
                explanation="Reported by Ruff static analysis.",
                evidence=f"ruff {code} at {rel}:{line}",
                suggested_fix=d.get("fix", {}).get("message", "") if d.get("fix") else "",
                source=FindingSource.RUFF,
                verification_status=VerificationStatus.CONFIRMED,
                rule=code,
            )
        )
    return findings, notes
