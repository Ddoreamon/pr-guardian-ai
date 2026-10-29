"""Cross-file review cases: the defect is only decidable by reading another file.

Each case changes exactly ONE file, but the contract that makes it wrong lives in
a different file (a config constant, a shared validator, a caller's docstring).
A reviewer that only sees the diff cannot know; a reviewer that can read the
repository can.

Splits are fixed here and must not be changed after measuring: `dev` is for
sanity checks, `heldout` is scored once.

Revision history (Dev only; the held-out cases XF-05..XF-10 have never changed):
  dev v1  XF-04 changed number formatting (`{amount:.2f}` -> `{amount:,.2f}`) and
          was labelled clean. That label was wrong: for totals >= 10,000 the
          thousands separator changes the user-visible string, so it is not
          behaviour-preserving. Counterexample:
          tests/test_crossfile.py::test_previous_formatting_change_was_not_clean
  dev v2  XF-04 replaced with a local variable rename, which is identical at
          every amount. Labels re-verified with benchmark/verify_crossfile.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- family: pricing --------------------------------------------------------

CONFIG = '''"""Pricing configuration."""

DISCOUNT_THRESHOLD = 100  # orders at or above this get a discount
DISCOUNT_RATE = 0.10
TAX_RATE = 0.20
'''

VALIDATION = '''"""Shared input validation."""


def is_valid_quantity(qty: int) -> bool:
    """Quantities must be positive integers."""
    return isinstance(qty, int) and not isinstance(qty, bool) and qty > 0
'''

PRICING = '''"""Order pricing rules."""
from src.config import DISCOUNT_RATE, DISCOUNT_THRESHOLD, TAX_RATE
from src.validation import is_valid_quantity


def discount_for(total: float) -> float:
    """Return the discount for an order total.

    Orders of 100 or more receive DISCOUNT_RATE; smaller orders get nothing.
    """
    if total >= DISCOUNT_THRESHOLD:
        return total * DISCOUNT_RATE
    return 0.0


def line_total(price: float, qty: int) -> float:
    """Return price * qty after validating the quantity."""
    if not is_valid_quantity(qty):
        raise ValueError("qty must be a positive integer")
    return round(price * qty, 2)


def tax_for(total: float) -> float:
    """Return sales tax using the configured tax rate."""
    return round(total * TAX_RATE, 2)
'''

REPORTING = '''"""Human-readable reporting."""
from src.pricing import discount_for


def describe_discount(total: float) -> str:
    """Return a one-line summary of the discount amount."""
    amount = discount_for(total)
    return f"Discount: {amount:.2f} on {total:.2f}"
'''

FILES = {
    "src/__init__.py": "",
    "src/config.py": CONFIG,
    "src/validation.py": VALIDATION,
    "src/pricing.py": PRICING,
    "src/reporting.py": REPORTING,
}


@dataclass(frozen=True)
class CrossFileCase:
    case_id: str
    split: str
    category: str
    title: str
    changed_file: str
    content_pr: str
    test_code: str
    has_bug: bool
    expected: str  # one-line ground truth, for the report


def _edit(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"anchor must occur exactly once: {old!r}")
    return text.replace(old, new, 1)


def _case(case_id, split, category, title, changed_file, content_pr, test_code,
          has_bug, expected) -> CrossFileCase:
    return CrossFileCase(case_id, split, category, title, changed_file, content_pr,
                         test_code, has_bug, expected)


def build_cases() -> list[CrossFileCase]:
    cases: list[CrossFileCase] = []

    # ---- dev ------------------------------------------------------------
    cases.append(_case(
        "XF-01", "dev", "cross_file_config",
        "config threshold raised to 101 while pricing.py still promises 100 or more",
        "src/config.py",
        _edit(CONFIG, "DISCOUNT_THRESHOLD = 100", "DISCOUNT_THRESHOLD = 101"),
        '''from src.pricing import discount_for


def test_threshold_order_still_discounted():
    assert discount_for(100) == 10.0
''',
        True,
        "orders of exactly 100 lose the discount (constant vs pricing.py contract)",
    ))
    cases.append(_case(
        "XF-02", "dev", "cross_file_constant_confusion",
        "verification tax uses the discount rate constant instead of TAX_RATE",
        "src/pricing.py",
        _edit(PRICING, "return round(total * TAX_RATE, 2)",
              "return round(total * DISCOUNT_RATE, 2)"),
        '''from src.pricing import tax_for


def test_tax_rate_is_twenty_percent():
    assert tax_for(100) == 20.0
''',
        True,
        "tax is computed with DISCOUNT_RATE (0.10) instead of TAX_RATE (0.20)",
    ))
    cases.append(_case(
        "XF-03", "dev", "cross_file_validation_contract",
        "quantity guard removed although validation.py defines the contract",
        "src/pricing.py",
        _edit(PRICING,
              "    if not is_valid_quantity(qty):\n"
              "        raise ValueError(\"qty must be a positive integer\")\n", ""),
        '''import pytest

from src.pricing import line_total


def test_zero_quantity_rejected():
    with pytest.raises(ValueError):
        line_total(10.0, 0)
''',
        True,
        "line_total no longer rejects a non-positive quantity",
    ))
    cases.append(_case(
        "XF-04", "dev", "clean_local_rename",
        "reporting renames a local variable (no behaviour change)",
        "src/reporting.py",
        _edit(REPORTING,
              "    amount = discount_for(total)\n"
              '    return f"Discount: {amount:.2f} on {total:.2f}"',
              "    discount_amount = discount_for(total)\n"
              '    return f"Discount: {discount_amount:.2f} on {total:.2f}"'),
        '''from src.reporting import describe_discount


def test_description_is_exact():
    assert describe_discount(100) == "Discount: 10.00 on 100.00"


def test_description_large_amount_is_exact():
    assert describe_discount(20000) == "Discount: 2000.00 on 20000.00"
''',
        False,
        "local variable rename; output identical at every amount",
    ))

    # ---- held-out -------------------------------------------------------
    cases.append(_case(
        "XF-05", "heldout", "cross_file_config",
        "TAX_RATE lowered to 2% by a config-only change",
        "src/config.py",
        _edit(CONFIG, "TAX_RATE = 0.20", "TAX_RATE = 0.02"),
        '''from src.pricing import tax_for


def test_tax_rate_unchanged():
    assert tax_for(100) == 20.0
''',
        True,
        "sales tax drops tenfold via a one-line config edit",
    ))
    cases.append(_case(
        "XF-06", "heldout", "cross_file_boundary",
        "pricing threshold becomes exclusive while the docstring says '100 or more'",
        "src/pricing.py",
        _edit(PRICING, "if total >= DISCOUNT_THRESHOLD:", "if total > DISCOUNT_THRESHOLD:"),
        '''from src.pricing import discount_for


def test_boundary_order_discounted():
    assert discount_for(100) == 10.0
''',
        True,
        "the >= boundary became >, contradicting the function's own contract",
    ))
    cases.append(_case(
        "XF-07", "heldout", "cross_file_caller",
        "reporting calls discount_for with a pre-divided total",
        "src/reporting.py",
        _edit(REPORTING, "    amount = discount_for(total)",
              "    amount = discount_for(total / 100)"),
        '''from src.reporting import describe_discount


def test_discount_amount_reported():
    assert "10.00" in describe_discount(100)
''',
        True,
        "reporting scales the total before computing the discount",
    ))
    cases.append(_case(
        "XF-08", "heldout", "cross_file_validation_contract",
        "validation allows zero quantities (qty > 0 became qty >= 0)",
        "src/validation.py",
        _edit(VALIDATION, "return isinstance(qty, int) and not isinstance(qty, bool) and qty > 0",
              "return isinstance(qty, int) and not isinstance(qty, bool) and qty >= 0"),
        '''import pytest

from src.pricing import line_total


def test_zero_quantity_rejected_through_caller():
    with pytest.raises(ValueError):
        line_total(10.0, 0)
''',
        True,
        "the shared validator now accepts 0, so callers silently accept it",
    ))
    cases.append(_case(
        "XF-09", "heldout", "clean_new_constant",
        "config gains an unused constant (no behaviour change)",
        "src/config.py",
        _edit(CONFIG, 'TAX_RATE = 0.20\n', 'TAX_RATE = 0.20\nCURRENCY = "AUD"\n'),
        '''from src.pricing import tax_for


def test_tax_unchanged():
    assert tax_for(100) == 20.0
''',
        False,
        "adds an unused constant; nothing reads it",
    ))
    cases.append(_case(
        "XF-10", "heldout", "clean_docstring",
        "pricing docstring clarified (no behaviour change)",
        "src/pricing.py",
        _edit(PRICING, '    """Return sales tax using the configured tax rate."""',
              '    """Return sales tax using the single configured rate."""'),
        '''from src.pricing import tax_for


def test_tax_unchanged_after_docstring_edit():
    assert tax_for(100) == 20.0
''',
        False,
        "docstring only",
    ))
    return cases


def files_for(case: CrossFileCase, *, apply_pr: bool) -> dict[str, str]:
    """Return the full file map for the base or PR revision."""
    files = dict(FILES)
    if apply_pr:
        files[case.changed_file] = case.content_pr
    return files
