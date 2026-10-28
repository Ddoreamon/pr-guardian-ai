"""Real DeepSeek -> search_code -> read_code_context -> Finding, over MCP.

Development case only (never the frozen benchmark). The corpus is the retrieval
evaluation corpus plus a checkout module; the bug is a unit mismatch that can only
be judged by reading the callee in another file.

Run:
    python scripts/rag_smoke.py --out docs/rag-smoke
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pr_guardian import pipeline
from pr_guardian.mcp_client import MCPToolBackend
from pr_guardian.settings import Settings
from scripts.rag_eval_v2 import build_corpus

BUG_BRANCH = "dev/credit-cents"
CLEAN_BRANCH = "dev/credit-units"
GOLD_SYMBOL = "apply_credit"
GOLD_FILE = "src/shop/invoices.py"

CHECKOUT_MAIN = '''"""Checkout."""

from .invoices import apply_credit, mark_paid


def finalise(order):
    """Finalise an order and apply any credit."""
    apply_credit(order["invoice"], order["amount"])
    return mark_paid(order["invoice"])
'''

CHECKOUT_BUG = '''"""Checkout."""

from .invoices import apply_credit, mark_paid


def finalise(order):
    """Finalise an order and apply any credit."""
    apply_credit(order["invoice"], order["cents"])
    return mark_paid(order["invoice"])
'''

CHECKOUT_CLEAN = '''"""Checkout."""

from .invoices import apply_credit, mark_paid


def finalise(order):
    """Finalise an order and apply any credit."""
    apply_credit(order["invoice"], order["amount"], note="web")
    return mark_paid(order["invoice"])
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


def build_case(root: Path) -> Path:
    build_corpus(root)
    invoices = (root / GOLD_FILE).read_text(encoding="utf-8")
    invoices = invoices.replace(
        '"""Apply a credit."""',
        '"""Apply a credit given in whole currency units (not cents)."""')
    invoices = invoices.replace(
        'def apply_credit(invoice, amount):',
        'def apply_credit(invoice, amount, note=""):')
    (root / GOLD_FILE).write_text(invoices, encoding="utf-8")
    (root / "src" / "shop" / "checkout.py").write_text(CHECKOUT_MAIN, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "checkout")

    _git(root, "checkout", "-q", "-b", BUG_BRANCH)
    (root / "src" / "shop" / "checkout.py").write_text(CHECKOUT_BUG, encoding="utf-8")
    _git(root, "commit", "-qam", "checkout: apply credit in cents")

    _git(root, "checkout", "-q", "-b", CLEAN_BRANCH, "main")
    (root / "src" / "shop" / "checkout.py").write_text(CHECKOUT_CLEAN, encoding="utf-8")
    _git(root, "commit", "-qam", "checkout: pass a note")
    _git(root, "checkout", "-q", "main")
    return root


class RecordingBackend:
    """Delegates to the real MCP backend and records what each tool returned."""

    def __init__(self, inner: MCPToolBackend) -> None:
        self.inner = inner
        self.calls: list[dict] = []

    def _note(self, tool: str, payload: dict) -> dict:
        self.calls.append({
            "tool": tool,
            "ok": payload.get("ok"),
            "ref": payload.get("ref"),
            "count": payload.get("count", payload.get("match_count")),
            "files": [h.get("file_path") for h in payload.get("hits", [])][:5],
            "symbols": [m.get("file_path") for m in payload.get("matches", [])][:3],
            "retrieval": payload.get("retrieval"),
        })
        return payload

    def read_code_context(self, file_path, start_line, end_line):
        return self._note("read_code_context",
                          self.inner.read_code_context(file_path, start_line, end_line))

    def find_symbol_definition(self, symbol):
        return self._note("find_symbol_definition",
                          self.inner.find_symbol_definition(symbol))

    def search_code(self, query, top_k=5):
        return self._note("search_code", self.inner.search_code(query, top_k))


def settings_for(backend: str, mode: str = "bm25") -> Settings:
    return Settings(
        api_key=os.environ.get("PR_GUARDIAN_API_KEY", "").strip(),
        base_url=os.environ.get("PR_GUARDIAN_BASE_URL", "https://api.deepseek.com"),
        model=os.environ.get("PR_GUARDIAN_MODEL", "deepseek-flash"),
        public_mode=False, allow_live_llm=True, allow_test_execution=False,
        max_live_llm_calls=50, tool_calls_enabled=True,
        max_tool_rounds=3, max_tool_calls=4, tool_backend=backend,
        retrieval_mode=mode,
    )


def parity(repo: Path, ref: str) -> dict:
    """Same snapshot, same query: Local and MCP must agree byte for byte."""
    from pr_guardian.agent_tools import TrustedCodebase

    direct = TrustedCodebase.from_git(repo, ref)
    query, top_k = "credit invoice units", 5
    started = time.perf_counter()
    local = direct.search_code(query, top_k)
    local_s = round(time.perf_counter() - started, 4)
    with MCPToolBackend(repo, ref) as backend:
        started = time.perf_counter()
        remote = backend.search_code(query, top_k)
        remote_s = round(time.perf_counter() - started, 4)
        latency = backend.audit[-1].latency_s
    return {
        "query": query, "top_k": top_k,
        "identical": local == remote,
        "ref": local.get("ref"),
        "local_latency_s": local_s, "mcp_latency_s": remote_s,
        "mcp_rpc_latency_s": latency,
        "top_files": [h["file_path"] for h in local.get("hits", [])],
        "gold_in_top_k": any(h["file_path"] == GOLD_FILE for h in local.get("hits", [])),
    }


def run_case(repo: Path, branch: str, case: str, mode: str = "bm25") -> dict:
    recorder: dict = {}

    def factory(cfg, repo_path, head):
        backend = MCPToolBackend(repo_path, head)
        recorder["inner"] = backend
        recorder["proxy"] = RecordingBackend(backend)
        return recorder["proxy"], False

    original = pipeline._tool_backend
    pipeline._tool_backend = factory
    started = time.perf_counter()
    try:
        report = pipeline.run_review(repo, "main", branch, use_llm=True, run_tests=False,
                                     settings=settings_for("mcp", mode))
    finally:
        pipeline._tool_backend = original
        recorder["inner"].close()

    wall = round(time.perf_counter() - started, 3)
    time.sleep(0.2)
    proxy: RecordingBackend = recorder["proxy"]
    used_search = [c for c in proxy.calls if c["tool"] == "search_code"]
    return {
        "case": case,
        "branch": branch,
        "snapshot_sha": _git(repo, "rev-parse", branch).strip(),
        "llm_status": report.llm_status,
        "tool_status": report.tool_status,
        "model_calls": report.tool_model_calls,
        "model_failures": report.tool_model_failures,
        "rounds": report.tool_rounds,
        "prompt_tokens": report.llm_prompt_tokens,
        "completion_tokens": report.llm_completion_tokens,
        "wall_s": wall,
        "used_search": bool(used_search),
        "search_calls": len(used_search),
        "search_found_gold": any(GOLD_FILE in (c.get("files") or []) for c in used_search),
        "tool_calls": [
            {"round": c.round, "call_id": c.call_id, "name": c.name,
             "arguments": c.arguments, "outcome": c.outcome, "matched": c.matched}
            for c in report.tool_calls
        ],
        "tool_payloads": proxy.calls,
        "findings": [f.model_dump(mode="json") for f in report.final_findings],
        "llm_findings": [f.model_dump(mode="json") for f in report.llm_findings],
        "session_closed": recorder["inner"].is_closed,
        "leaked_threads": [t.name for t in threading.enumerate() if t.name == "mcp-client"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("docs/rag-smoke"))
    ap.add_argument("--mode", default="bm25", choices=["bm25", "dense", "hybrid"])
    args = ap.parse_args()
    if not os.environ.get("PR_GUARDIAN_API_KEY", "").strip():
        print("PR_GUARDIAN_API_KEY is not set: Not Evaluated", file=sys.stderr)
        return 2

    root = Path(tempfile.mkdtemp()) / "case"
    build_case(root)
    payload = {
        "corpus_label": "<rag-dev-case>",
        "gold": {"symbol": GOLD_SYMBOL, "file": GOLD_FILE},
        "parity": parity(root, BUG_BRANCH),
        "retrieval_mode": args.mode,
        "cases": [run_case(root, BUG_BRANCH, "bug", args.mode),
                  run_case(root, CLEAN_BRANCH, "clean", args.mode)],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.out / "REPORT.md").write_text(render(payload), encoding="utf-8")
    print(json.dumps({
        "parity": {k: payload["parity"][k] for k in ("identical", "gold_in_top_k", "ref")},
        "cases": [{"case": c["case"], "status": c["llm_status"], "calls": len(c["tool_calls"]),
                   "used_search": c["used_search"], "search_found_gold": c["search_found_gold"],
                   "findings": len(c["findings"]), "tokens": [c["prompt_tokens"], c["completion_tokens"]],
                   "closed": c["session_closed"], "leaked": c["leaked_threads"]}
                  for c in payload["cases"]],
    }, indent=2, ensure_ascii=False))
    return 0


def render(payload: dict) -> str:
    p = payload["parity"]
    lines = [
        "# DeepSeek -> search_code -> read_code_context -> Finding (dev case)",
        "",
        "Not the frozen benchmark. The bug is a unit mismatch that can only be judged",
        "by reading a callee in another file.",
        "",
        f"* gold: `{payload['gold']['symbol']}` in `{payload['gold']['file']}`",
        "",
        "## Local vs MCP parity (same snapshot, same query)",
        "",
        f"* query: `{p['query']}`, top_k {p['top_k']}",
        f"* payloads identical: {p['identical']}",
        f"* ref: `{p['ref']}`",
        f"* gold in top-k: {p['gold_in_top_k']}",
        f"* top files: {p['top_files']}",
        (f"* latency: local {p['local_latency_s']}s, mcp {p['mcp_latency_s']}s "
        f"(rpc {p['mcp_rpc_latency_s']}s)"),
        "",
        "## Runs",
        "",
    ]
    for c in payload["cases"]:
        lines += [
            f"### {c['case']}",
            "",
            f"* branch `{c['branch']}` at `{c['snapshot_sha']}`",
            f"* llm_status `{c['llm_status']}`, tool_status `{c['tool_status']}`",
            f"* model calls {c['model_calls']} (failures {c['model_failures']}), rounds {c['rounds']}",
            f"* tokens {c['prompt_tokens']} / {c['completion_tokens']}, wall {c['wall_s']}s",
            (f"* used search_code: {c['used_search']} ({c['search_calls']} calls); "
            f"search hit the gold file: {c['search_found_gold']}"),
            f"* session closed: {c['session_closed']}; leaked threads: {c['leaked_threads']}",
            "",
            "| round | tool_call_id | tool | arguments | outcome | matched |",
            "|---|---|---|---|---|---|",
        ]
        for t in c["tool_calls"]:
            lines.append(f"| {t['round']} | `{t['call_id']}` | {t['name']} | "
                         f"`{json.dumps(t['arguments'])[:70]}` | {t['outcome']} | {t['matched']} |")
        lines += ["", "| tool result | ref | count | first files |", "|---|---|---|---|"]
        for r in c["tool_payloads"]:
            lines.append(f"| {r['tool']} | `{str(r['ref'])[:12]}` | {r['count']} | "
                         f"{', '.join((r['files'] or r['symbols']) or ['-'])} |")
        lines += ["", "| finding | file:line | status | severity |", "|---|---|---|---|"]
        for f in c["findings"] or c["llm_findings"]:
            lines.append(f"| {f['summary'][:90]} | {f['file_path']}:{f['start_line']} | "
                         f"{f['verification_status']} | {f['severity']} |")
        if not (c["findings"] or c["llm_findings"]):
            lines.append("| _no findings_ |  |  |  |")
        lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
