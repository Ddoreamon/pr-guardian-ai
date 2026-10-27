"""Security and behaviour tests for the read-only agent tools.

The point of these tests is the *refusals*: every path the model could invent is
either readable Python inside the repo or a recorded error.
"""

import sys

import pytest

from pr_guardian.agent_tools import (
    MAX_LINES_PER_READ,
    TOOL_OUTPUT_PREFIX,
    ToolError,
    TrustedCodebase,
    wrap_tool_output,
)


@pytest.fixture
def codebase(tmp_path) -> TrustedCodebase:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "src" / "orders.py").write_text(
        "\n".join(f"line_{i} = {i}" for i in range(1, 61)) + "\n", encoding="utf-8"
    )
    (root / "src" / "helpers.py").write_text(
        "def calculate_discount(total):\n    return total * 0.1\n\n\n"
        "class OrderBook:\n    pass\n\n\n"
        "async def fetch_price():\n    return 1\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_orders.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (root / "docs" / "secret.py").write_text("ANSWER = 42\n", encoding="utf-8")
    (root / ".env").write_text("PR_GUARDIAN_API_KEY=sk-should-never-be-read\n", encoding="utf-8")
    (root / "src" / "notes.txt").write_text("hello\n", encoding="utf-8")
    return TrustedCodebase(root)


# ---- happy path ------------------------------------------------------------

def test_reads_numbered_range(codebase):
    out = codebase.read_code_context("src/orders.py", 2, 4)
    assert out["ok"] is True
    assert out["start_line"] == 2 and out["end_line"] == 4
    assert "line_2 = 2" in out["content"]
    # 1-based line numbers appear in the output
    assert "    2 | " in out["content"] or "2 | line_2" in out["content"]


def test_read_is_capped_at_max_lines(codebase):
    with pytest.raises(ToolError) as exc:
        codebase.read_code_context("src/orders.py", 1, MAX_LINES_PER_READ + 1)
    assert exc.value.code == "bad_range"


def test_read_clamps_end_to_file_length(codebase):
    # 60-line file; ask for 58..70 (within the 40-line cap) -> clamped to 60
    out = codebase.read_code_context("src/orders.py", 58, 70)
    assert out["end_line"] == 60


def test_read_beyond_eof_is_rejected(codebase):
    with pytest.raises(ToolError) as exc:
        codebase.read_code_context("src/orders.py", 999, 1000)
    assert exc.value.code == "bad_range"


@pytest.mark.parametrize("start,end", [(0, 2), (5, 3)])
def test_bad_ranges_rejected(codebase, start, end):
    with pytest.raises(ToolError) as exc:
        codebase.read_code_context("src/orders.py", start, end)
    assert exc.value.code == "bad_range"


def test_non_integer_range_rejected(codebase):
    with pytest.raises(ToolError):
        codebase.read_code_context("src/orders.py", "one", 2)


def test_finds_function_class_and_async(codebase):
    for symbol, kind in (("calculate_discount", "function"),
                         ("OrderBook", "class"),
                         ("fetch_price", "async_function")):
        out = codebase.find_symbol_definition(symbol)
        assert out["matches"], symbol
        assert out["matches"][0]["kind"] == kind
        assert out["matches"][0]["line"] >= 1


def test_symbol_not_found_returns_empty_list(codebase):
    out = codebase.find_symbol_definition("no_such_symbol")
    assert out["ok"] is True and out["matches"] == []


def test_symbol_results_capped_at_three(codebase):
    # four files declaring the same name
    for i in range(4):
        (codebase.root / "src" / f"dup{i}.py").write_text(
            "def duplicated():\n    return 1\n", encoding="utf-8"
        )
    out = codebase.find_symbol_definition("duplicated")
    assert len(out["matches"]) <= 3


@pytest.mark.parametrize("symbol", ["", "   ", "has space", "1abc", "a" * 100, "x;y"])
def test_invalid_symbols_rejected(codebase, symbol):
    with pytest.raises(ToolError) as exc:
        codebase.find_symbol_definition(symbol)
    assert exc.value.code == "bad_symbol"


# ---- refusals: traversal, credentials, ground truth ------------------------

@pytest.mark.parametrize("path", [
    "../outside.py",
    "src/../../outside.py",
    "..\\outside.py",
    "/etc/passwd",
    "C:/Windows/system32/config.py",
    "src/orders.py\x00.py",
])
def test_path_traversal_and_absolute_paths_refused(codebase, path):
    with pytest.raises(ToolError) as exc:
        codebase.read_code_context(path, 1, 2)
    assert exc.value.code in {"denied_path", "bad_path"}


@pytest.mark.parametrize("path", [".env", "tests/test_orders.py", "docs/secret.py"])
def test_credentials_tests_and_docs_refused(codebase, path):
    """Ground-truth tests and credentials must be unreachable through a tool."""
    with pytest.raises(ToolError) as exc:
        codebase.read_code_context(path, 1, 2)
    assert exc.value.code == "denied_path"


def test_non_python_file_refused(codebase):
    with pytest.raises(ToolError) as exc:
        codebase.read_code_context("src/notes.txt", 1, 1)
    assert exc.value.code == "unsupported_type"


def test_missing_file_refused(codebase):
    with pytest.raises(ToolError) as exc:
        codebase.read_code_context("src/nope.py", 1, 1)
    assert exc.value.code == "not_found"


def test_directory_refused(codebase):
    with pytest.raises(ToolError) as exc:
        codebase.read_code_context("src", 1, 1)
    assert exc.value.code == "unsupported_type"


def test_symlink_escape_refused(codebase, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = 'x'\n", encoding="utf-8")
    link = codebase.root / "src" / "escape.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip(f"symlinks unavailable on this platform ({sys.platform})")
    with pytest.raises(ToolError) as exc:
        codebase.read_code_context("src/escape.py", 1, 1)
    assert exc.value.code == "denied_path"


def test_symbol_search_never_reports_tests_or_docs(codebase):
    (codebase.root / "tests" / "leak.py").write_text("def leaky():\n    pass\n", encoding="utf-8")
    (codebase.root / "docs" / "leak2.py").write_text("def leaky():\n    pass\n", encoding="utf-8")
    out = codebase.find_symbol_definition("leaky")
    assert out["matches"] == []


# ---- untrusted-output framing ---------------------------------------------

def test_tool_output_is_labelled_untrusted(codebase):
    payload = codebase.read_code_context("src/orders.py", 1, 2)
    wrapped = wrap_tool_output(payload)
    assert wrapped.startswith(TOOL_OUTPUT_PREFIX)
    assert "<<<TOOL_DATA" in wrapped and "TOOL_DATA>>>" in wrapped


def test_error_payload_shape():
    err = ToolError("denied_path", "nope")
    assert err.as_payload() == {"ok": False, "error": {"code": "denied_path", "message": "nope"}}
