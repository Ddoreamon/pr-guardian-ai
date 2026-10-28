"""Produce a real MCP trace and compare its cost with the in-process tools.

Prints every MCP round trip (tool, arguments, latency, result size), checks that
the payloads are identical to the direct backend, and reports the overhead.

Run:
    python scripts/mcp_trace.py --repo <path> --ref <commit>
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pr_guardian.agent_tools import TrustedCodebase
from pr_guardian.mcp_client import MCPToolBackend

READS = [
    ("src/discount.py", 1, 20),
    ("src/discount.py", 1, 10),
]
SYMBOLS = ["calculate_discount", "DISCOUNT_RATE"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--ref", required=True)
    args = ap.parse_args()

    direct = TrustedCodebase.from_git(args.repo, args.ref)
    print(f"snapshot: {args.repo} @ {direct.snapshot.sha}")

    rows: list[dict] = []
    local_latencies: list[float] = []
    started = time.perf_counter()
    with MCPToolBackend(args.repo, args.ref) as backend:
        session_start_s = round(time.perf_counter() - started, 4)
        print(f"mcp session start: {session_start_s}s")
        for path, start, end in READS:
            t0 = time.perf_counter()
            local_payload = direct.read_code_context(path, start, end)
            local_latencies.append(round(time.perf_counter() - t0, 4))

            mcp_payload = backend.read_code_context(path, start, end)
            latency = backend.audit[-1].latency_s
            rows.append({
                "tool": "read_code_context",
                "arguments": {"file_path": path, "start_line": start, "end_line": end},
                "mcp_latency_s": latency,
                "result_chars": len(json.dumps(mcp_payload)),
                "identical_to_local": mcp_payload == local_payload,
            })
        for symbol in SYMBOLS:
            t0 = time.perf_counter()
            local_payload = direct.find_symbol_definition(symbol)
            local_latencies.append(round(time.perf_counter() - t0, 4))

            mcp_payload = backend.find_symbol_definition(symbol)
            latency = backend.audit[-1].latency_s
            rows.append({
                "tool": "find_symbol_definition",
                "arguments": {"symbol": symbol},
                "mcp_latency_s": latency,
                "result_chars": len(json.dumps(mcp_payload)),
                "identical_to_local": mcp_payload == local_payload,
            })

    print("\n=== MCP round trips ===")
    print(f"{'tool':22} {'arguments':52} {'latency(s)':>10} {'chars':>6} {'== local':>8}")
    for row in rows:
        print(f"{row['tool']:22} {json.dumps(row['arguments'])[:52]:52} "
              f"{row['mcp_latency_s']:>10} {row['result_chars']:>6} "
              f"{row['identical_to_local']!s:>8}")

    mcp_latencies = [r["mcp_latency_s"] for r in rows]
    print(f"\nmcp session start (once per review): {session_start_s}s")
    print("\n=== overhead ===")
    print(f"calls: {len(rows)}  identical to local: {sum(r['identical_to_local'] for r in rows)}/{len(rows)}")
    print(f"local  median: {statistics.median(local_latencies):.4f}s  "
          f"mean: {statistics.mean(local_latencies):.4f}s")
    print(f"mcp    median: {statistics.median(mcp_latencies):.4f}s  "
          f"mean: {statistics.mean(mcp_latencies):.4f}s")
    print(f"ratio (mcp/local median): "
          f"{statistics.median(mcp_latencies) / statistics.median(local_latencies):.2f}x")
    return 0 if all(r["identical_to_local"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
