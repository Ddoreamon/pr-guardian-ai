"""The development case reused by the smoke scripts (not a benchmark).

A cross-file bug (a cents value passed where whole currency units are expected) and
a cross-file clean change (an extra keyword the callee accepts).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

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
        "def apply_credit(invoice, amount):",
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
