"""MCP server exposing the two read-only review tools over stdio.

Design constraints (deliberate):

* The repository and the **commit SHA** are bound when the process starts, from
  the command line. A model cannot change them: neither is a tool argument, and
  there is no tool that accepts a path, a ref, or a URL.
* File access goes through the existing `TrustedCodebase` / `GitSnapshot`, so the
  path allowlist, output caps, file-size limit, symlink refusal and read-only
  behaviour are the same code that the in-process agent uses. No second
  implementation of filesystem access exists.
* stdout carries MCP protocol frames only. All logging goes to stderr; a stray
  `print()` here would corrupt the stream.

Run:
    python -m pr_guardian.mcp_server --repo <path> --ref <commit>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .agent_tools import (
    FIND_SYMBOL_DEFINITION,
    MAX_LINES_PER_READ,
    MAX_SYMBOL_RESULTS,
    READ_CODE_CONTEXT,
    ToolError,
    TrustedCodebase,
)

LOGGER = logging.getLogger("pr_guardian.mcp")

SERVER_NAME = "pr-guardian-readonly"

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": READ_CODE_CONTEXT,
        "description": (
            "Read a numbered range of lines from a Python file tracked in the reviewed "
            "commit. Only files under src/ ending in .py are readable. "
            f"At most {MAX_LINES_PER_READ} lines per call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Repository-relative path under src/, e.g. src/orders.py",
                },
                "start_line": {"type": "integer", "description": "First line (1-based)"},
                "end_line": {"type": "integer", "description": "Last line, inclusive"},
            },
            "required": ["file_path", "start_line", "end_line"],
            "additionalProperties": False,
        },
    },
    {
        "name": FIND_SYMBOL_DEFINITION,
        "description": (
            "Find where a function, async function, class or module-level constant is "
            "defined in the reviewed commit. At most "
            f"{MAX_SYMBOL_RESULTS} matches are returned."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Exact symbol name"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
]


def _text_result(payload: dict, *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        is_error=is_error,
    )


def _dispatch(codebase: TrustedCodebase, name: str, arguments: dict) -> types.CallToolResult:
    """Run one tool call. Errors become structured, explicit results."""
    if name == READ_CODE_CONTEXT:
        try:
            payload = codebase.read_code_context(
                arguments.get("file_path"),
                arguments.get("start_line"),
                arguments.get("end_line"),
            )
        except ToolError as exc:
            return _text_result(exc.as_payload(), is_error=True)
        return _text_result(payload)

    if name == FIND_SYMBOL_DEFINITION:
        try:
            payload = codebase.find_symbol_definition(arguments.get("symbol"))
        except ToolError as exc:
            return _text_result(exc.as_payload(), is_error=True)
        return _text_result(payload)

    # No tool can change the repository, the commit, or reach another directory.
    return _text_result(
        {"ok": False, "error": {"code": "unknown_tool", "message": f"unknown tool: {name!r}"}},
        is_error=True,
    )


def build_server(codebase: TrustedCodebase) -> Server:
    """Create the MCP server bound to one immutable snapshot."""

    async def list_tools(_ctx, _params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=spec["name"],
                    description=spec["description"],
                    input_schema=spec["input_schema"],
                )
                for spec in TOOL_DEFINITIONS
            ]
        )

    async def call_tool(_ctx, params) -> types.CallToolResult:
        name = getattr(params, "name", "") or ""
        arguments = getattr(params, "arguments", None) or {}
        if not isinstance(arguments, dict):
            return _text_result(
                {"ok": False,
                 "error": {"code": "bad_arguments", "message": "arguments must be an object"}},
                is_error=True,
            )
        LOGGER.info("tool call name=%s args=%s", name, list(arguments)[:6])
        return _dispatch(codebase, name, arguments)

    return Server(
        name=SERVER_NAME,
        version="0.1.0",
        instructions=(
            "Read-only code context for one repository at one commit. "
            "The repository and commit are fixed when this server starts; "
            "no tool can change them or read outside src/**/*.py."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve(codebase: TrustedCodebase) -> None:
    server = build_server(codebase)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream,
                         server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pr-guardian-mcp")
    parser.add_argument("--repo", required=True, help="trusted local repository path")
    parser.add_argument("--ref", required=True, help="commit/ref to pin the snapshot to")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    # stdout is the protocol channel; every diagnostic must go to stderr.
    logging.basicConfig(stream=sys.stderr, level=args.log_level.upper(),
                        format="%(levelname)s %(name)s %(message)s")

    try:
        codebase = TrustedCodebase.from_git(args.repo, args.ref)
    except ToolError as exc:
        print(f"pr-guardian-mcp: cannot bind snapshot: {exc.code}: {exc.message}",
              file=sys.stderr)
        return 2

    LOGGER.info("serving %s at %s", args.repo, codebase.snapshot.sha)
    try:
        anyio.run(serve, codebase)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
