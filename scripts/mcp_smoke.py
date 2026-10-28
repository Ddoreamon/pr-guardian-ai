"""Real DeepSeek -> MCP -> Finding smoke test on purpose-built dev cases.

These cases are **not** part of the frozen benchmark. They exist only to prove the
end-to-end path: the model returns tool_calls, the MCP client sends them over stdio
to the MCP server, the server reads a pinned commit, results go back with the
matching tool_call_id, the model returns structured findings, and the existing
validator processes them.

Case A (bug, cross-file): the caller passes a keyword the callee does not accept.
Case B (clean, cross-file): the caller passes a keyword the callee does accept.

Run:
    python scripts/mcp_smoke.py --out docs/mcp-smoke
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pr_guardian import pipeline
from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.mcp_client import MCPToolBackend
from pr_guardian.settings import Settings

BUG_BRANCH = "dev/express-shipping"
CLEAN_BRANCH = "dev/currency-arg"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


FILES = {
    "src/__init__.py": "",
    "src/shipping.py": '''"""Shipping estimates."""


def estimate(weight_kg: float) -> float:
    """Return the shipping cost in dollars for a parcel weight."""
    if weight_kg <= 0:
        raise ValueError("weight must be positive")
    return round(4.5 + weight_kg * 1.25, 2)
''',
    "src/money.py": '''"""Money formatting."""


def format_money(amount: float, currency: str = "USD") -> str:
    """Format an amount with its currency code."""
    return f"{amount:.2f} {currency}"
''',
    "src/checkout.py": '''"""Checkout totals."""

from .money import format_money
from .shipping import estimate


def total(weight_kg: float, net: float, rate: float) -> float:
    """Return the order total including tax and shipping."""
    tax = net * rate
    return round(net + tax + estimate(weight_kg), 2)


def describe(weight_kg: float, net: float) -> str:
    """Return a one-line description of the order."""
    return format_money(round(net + estimate(weight_kg), 2))
''',
    "tests/test_checkout.py": '''from src.checkout import describe, total


def test_total_includes_tax_and_shipping():
    assert total(1.0, 10.0, 0.1) == 16.75


def test_describe_uses_default_currency():
    assert describe(1.0, 10.0) == "15.75 USD"
''',
}


def build_dev_repo(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "dev@example.com")
    _git(root, "config", "user.name", "dev")
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base checkout")

    # Case A: caller passes a keyword the callee rejects -> TypeError at runtime
    _git(root, "checkout", "-q", "-b", BUG_BRANCH)
    bug = (root / "src" / "checkout.py").read_text(encoding="utf-8")
    bug = bug.replace(
        "return round(net + tax + estimate(weight_kg), 2)",
        "return round(net + tax + estimate(weight_kg, express=True), 2)")
    (root / "src" / "checkout.py").write_text(bug, encoding="utf-8")
    _git(root, "commit", "-qam", "checkout: request express shipping")

    # Case B: caller passes a keyword the callee already supports -> no defect
    _git(root, "checkout", "-q", "-b", CLEAN_BRANCH, "main")
    clean = (root / "src" / "checkout.py").read_text(encoding="utf-8")
    clean = clean.replace('format_money(round(net + estimate(weight_kg), 2))',
                          'format_money(round(net + estimate(weight_kg), 2), currency="USD")')
    (root / "src" / "checkout.py").write_text(clean, encoding="utf-8")
    _git(root, "commit", "-qam", "checkout: pass the currency explicitly")

    _git(root, "checkout", "-q", "main")
    return root


def direct_comparison(repo: Path, ref: str) -> dict:
    """Same snapshot and arguments: compare MCP payloads with in-process ones."""
    sha = _git(repo, "rev-parse", ref).strip()
    direct = TrustedCodebase.from_git(repo, ref)
    rows = []
    local_latencies = []

    started = time.perf_counter()
    backend = MCPToolBackend(repo, ref)
    init_s = round(time.perf_counter() - started, 4)
    try:
        for path, start, end in (("src/checkout.py", 1, 20), ("src/shipping.py", 1, 8)):
            t0 = time.perf_counter()
            local_payload = direct.read_code_context(path, start, end)
            local_latencies.append(round(time.perf_counter() - t0, 4))
            mcp_payload = backend.read_code_context(path, start, end)
            rows.append({
                "tool": "read_code_context",
                "arguments": {"file_path": path, "start_line": start, "end_line": end},
                "mcp_latency_s": backend.audit[-1].latency_s,
                "identical": mcp_payload == local_payload,
                "ref": mcp_payload.get("ref"),
            })
        for symbol in ("estimate", "format_money"):
            t0 = time.perf_counter()
            local_payload = direct.find_symbol_definition(symbol)
            local_latencies.append(round(time.perf_counter() - t0, 4))
            mcp_payload = backend.find_symbol_definition(symbol)
            rows.append({
                "tool": "find_symbol_definition",
                "arguments": {"symbol": symbol},
                "mcp_latency_s": backend.audit[-1].latency_s,
                "identical": mcp_payload == local_payload,
                "ref": mcp_payload.get("ref"),
            })

        # the pinned commit, not the working tree
        (repo / "src" / "shipping.py").write_text("# worktree edit\nEST = 1\n", encoding="utf-8")
        after_edit = backend.read_code_context("src/shipping.py", 1, 4)
        worktree_ignored = "worktree edit" not in after_edit.get("content", "")
        _git(repo, "checkout", "--", "src/shipping.py")
    finally:
        backend.close()

    time.sleep(0.2)
    leaked_threads = [t.name for t in threading.enumerate() if t.name == "mcp-client"]

    return {
        "pinned_sha": sha,
        "init_s": init_s,
        "calls": rows,
        "all_identical": all(r["identical"] for r in rows),
        "all_refs_match": all(r["ref"] == sha for r in rows),
        "worktree_edit_ignored": worktree_ignored,
        "local_median_s": round(statistics.median(local_latencies), 4),
        "mcp_median_s": round(statistics.median(
            [r["mcp_latency_s"] for r in rows]), 4),
        "session_closed": backend.is_closed,
        "leaked_threads": leaked_threads,
        "audit_summary": backend.audit_summary(),
    }


def _settings(backend: str) -> Settings:
    key = os.environ.get("PR_GUARDIAN_API_KEY", "").strip()
    return Settings(
        api_key=key,
        base_url=os.environ.get("PR_GUARDIAN_BASE_URL", "https://api.deepseek.com"),
        model=os.environ.get("PR_GUARDIAN_MODEL", "deepseek-flash"),
        public_mode=False,
        allow_live_llm=True,
        allow_test_execution=False,
        max_live_llm_calls=50,
        tool_calls_enabled=True,
        max_tool_rounds=3,
        max_tool_calls=4,
        tool_backend=backend,
    )


def run_case(repo: Path, branch: str, case: str, backend: str) -> dict:
    started = time.perf_counter()
    before = {t.name for t in threading.enumerate()}
    report = pipeline.run_review(repo, "main", branch, use_llm=True, run_tests=False,
                                 settings=_settings(backend))
    latency = round(time.perf_counter() - started, 3)
    time.sleep(0.2)
    leaked = [t.name for t in threading.enumerate()
              if t.name == "mcp-client" and t.name not in before]
    return {
        "case": case,
        "branch": branch,
        "backend": backend,
        "snapshot_sha": _git(repo, "rev-parse", branch).strip(),
        "llm_status": report.llm_status,
        "model_name": report.model_name,
        "llm_note": report.llm_note,
        "tool_status": report.tool_status,
        "tool_model_calls": report.tool_model_calls,
        "tool_model_failures": report.tool_model_failures,
        "tool_rounds": report.tool_rounds,
        "prompt_tokens": report.llm_prompt_tokens,
        "completion_tokens": report.llm_completion_tokens,
        "wall_s": latency,
        "tool_calls": [
            {"round": c.round, "call_id": c.call_id, "name": c.name,
             "arguments": c.arguments, "ok": c.ok, "outcome": c.outcome,
             "matched": c.matched, "error_code": c.error_code,
             "result_chars": c.result_chars}
            for c in report.tool_calls
        ],
        "llm_findings": [f.model_dump(mode="json") for f in report.llm_findings],
        "final_findings": [f.model_dump(mode="json") for f in report.final_findings],
        "leaked_threads": leaked,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("docs/mcp-smoke"))
    args = ap.parse_args()

    if not os.environ.get("PR_GUARDIAN_API_KEY", "").strip():
        print("PR_GUARDIAN_API_KEY is not set; refusing to claim a live smoke test.",
              file=sys.stderr)
        return 2

    root = Path(tempfile.mkdtemp()) / "smoke-repo"
    build_dev_repo(root)
    result: dict = {"repo": str(root), "cases": []}

    result["direct_comparison"] = direct_comparison(root, BUG_BRANCH)
    for case, branch in (("bug", BUG_BRANCH), ("clean", CLEAN_BRANCH)):
        for backend in ("mcp", "local"):
            result["cases"].append(run_case(root, branch, case, backend))

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.out / "REPORT.md").write_text(render(result), encoding="utf-8")
    print(json.dumps(summarise(result), indent=2, ensure_ascii=False))
    return 0


def summarise(result: dict) -> dict:
    out = {"direct": result["direct_comparison"], "cases": []}
    for c in result["cases"]:
        out["cases"].append({
            "case": c["case"], "backend": c["backend"], "llm_status": c["llm_status"],
            "tool_status": c["tool_status"], "tool_calls": len(c["tool_calls"]),
            "model_calls": c["tool_model_calls"], "findings": len(c["llm_findings"]),
            "final_findings": len(c["final_findings"]),
            "prompt_tokens": c["prompt_tokens"],
            "completion_tokens": c["completion_tokens"],
            "leaked_threads": c["leaked_threads"],
        })
    return out


def render(result: dict) -> str:
    d = result["direct_comparison"]
    lines = [
        "# DeepSeek -> MCP -> Finding smoke test (development cases)",
        "",
        "Not part of the frozen benchmark. Case A is a cross-file bug (a keyword the",
        "callee rejects); case B is a cross-file clean change (a keyword the callee",
        "accepts). Both go through the real provider and the real MCP stdio session.",
        "",
        "## Direct tool comparison (same snapshot, same arguments)",
        "",
        f"* pinned commit: `{d['pinned_sha']}`",
        f"* MCP session start: {d['init_s']}s",
        f"* per-call median: MCP {d['mcp_median_s']}s vs in-process {d['local_median_s']}s",
        f"* payloads identical: {d['all_identical']} ({sum(r['identical'] for r in d['calls'])}/{len(d['calls'])})",
        f"* every payload ref == pinned SHA: {d['all_refs_match']}",
        f"* worktree edit ignored: {d['worktree_edit_ignored']}",
        f"* session closed: {d['session_closed']}; leftover threads: {d['leaked_threads']}",
        "",
        "| tool | arguments | latency (s) | identical |",
        "|---|---|---|---|",
    ]
    for row in d["calls"]:
        lines.append(f"| {row['tool']} | `{json.dumps(row['arguments'])}` | "
                     f"{row['mcp_latency_s']} | {row['identical']} |")

    lines += ["", "## End-to-end runs", ""]
    for c in result["cases"]:
        lines += [
            f"### {c['case']} / {c['backend']}",
            "",
            f"* branch `{c['branch']}` at `{c['snapshot_sha']}`",
            f"* llm_status: `{c['llm_status']}`; tool_status: `{c['tool_status']}`",
            (f"* model calls: {c['tool_model_calls']} "
             f"(failures {c['tool_model_failures']}), rounds {c['tool_rounds']}"),
            f"* tokens: {c['prompt_tokens']} prompt / {c['completion_tokens']} completion",
            f"* tool calls: {len(c['tool_calls'])}; leaked threads: {c['leaked_threads']}",
            "",
        ]
        if c["tool_calls"]:
            lines += ["| round | tool_call_id | tool | arguments | outcome | matched |",
                      "|---|---|---|---|---|---|"]
            for t in c["tool_calls"]:
                lines.append(f"| {t['round']} | `{t['call_id']}` | {t['name']} | "
                             f"`{json.dumps(t['arguments'])[:80]}` | {t['outcome']} | {t['matched']} |")
            lines.append("")
        lines += ["| finding | file:line | status | source | severity |",
                  "|---|---|---|---|---|"]
        for f in c["final_findings"] or c["llm_findings"]:
            lines.append(f"| {f['summary'][:80]} | {f['file_path']}:{f['start_line']} | "
                         f"{f['verification_status']} | {f['source']} | {f['severity']} |")
        if not (c["final_findings"] or c["llm_findings"]):
            lines.append("| _no findings_ |  |  |  |  |")
        lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
