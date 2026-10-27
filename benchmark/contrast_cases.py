"""Contrast pairs: the same diff is a defect in one repository and correct in another.

REVIEW TARGET (single rule, applied everywhere)
----------------------------------------------
A variant counts as a defect when the change violates a **documented contract**:
type annotations, docstrings, or the behaviour that the surrounding code in the
same repository establishes. Passing tests is necessary but not sufficient — a
green suite does not excuse breaking an annotation or a documented rule.

Consequence for design: because the changed file is identical in both variants,
any contract *inside* that file applies to both. If the diff contradicted it,
variant B could not be clean. So every diff here violates only the **external**
context (a different file), never the changed file's own documentation.

Revision history
----------------
v1  initial 11 pairs. Audit found four pairs whose diff contradicted the changed
    file's own docstring/annotation (P2 dollars-vs-cents in `tax_for`, P3
    `-> list[dict]` returning None, P9 "missing key is a configuration error",
    P11 "an empty list yields 0.0"). Under the rule above those B variants are
    NOT clean, so they were redesigned rather than relabelled. P7's docstring was
    neutralised for the same reason. See docs/v2.2-audit.md.
v2  current. 11 pairs, unrun held-out.

Invariants enforced by benchmark/verify_contrast.py before any model call:
  1. diff(A) == diff(B), same changed-file list
  2. the rendered review prompt is identical for A and B (V1 and V2)
  3. base A and base B pass their tests
  4. PR A fails, PR B passes
  5. the hidden tests never appear in the diff or prompt
"""

from __future__ import annotations

from dataclasses import dataclass

VERSION = "v3"

INIT = ""


@dataclass(frozen=True)
class ContrastPair:
    pair_id: str
    split: str            # dev | heldout
    category: str
    mechanism: str        # M1..M7, see docs/v3-contrast-design.md
    title: str
    diff_file: str
    base_code: str
    pr_code: str
    context_a: dict[str, str]
    context_b: dict[str, str]
    tests_a: str
    tests_b: str
    # keyword-assisted signature: every group needs at least one term present in
    # the finding text. Deliberately narrow; a human still adjudicates.
    defect_signature: tuple[tuple[str, ...], ...]
    a_expected: str
    b_expected: str


def _pair(**kw) -> ContrastPair:
    return ContrastPair(**kw)


# ---------------------------------------------------------------------------
# P1 · M1 · callee accepts an unknown argument and ignores it
# ---------------------------------------------------------------------------

P1_BASE = '''"""Order pricing."""
from src.rates import apply_rate


def calculate(total: float) -> float:
    """Return the discounted total."""
    return apply_rate(total)
'''

P1 = _pair(
    pair_id="P1", split="dev", category="call_contract", mechanism="M1",
    title="extra keyword argument: missing on the callee vs accepted and ignored",
    diff_file="src/pricing.py", base_code=P1_BASE,
    pr_code=P1_BASE.replace("return apply_rate(total)", "return apply_rate(total, strict=True)"),
    context_a={"src/rates.py": '''"""Rate application."""


def apply_rate(total: float) -> float:
    return total * 0.9
'''},
    context_b={"src/rates.py": '''"""Rate application."""


def apply_rate(total: float, **options) -> float:
    """Apply the standard rate. Unknown options are accepted and ignored."""
    return total * 0.9
'''},
    tests_a='''from src.pricing import calculate


def test_discount_applies():
    assert calculate(100) == 90.0


def test_zero_total():
    assert calculate(0) == 0.0
''',
    tests_b='''from src.pricing import calculate


def test_discount_applies():
    assert calculate(100) == 90.0


def test_zero_total():
    assert calculate(0) == 0.0
''',
    defect_signature=(("strict",),
                      ("keyword", "argument", "parameter", "signature", "typeerror", "unexpected")),
    a_expected="TypeError: apply_rate() got an unexpected keyword argument",
    b_expected="same value for every input",
)

# ---------------------------------------------------------------------------
# P2 · M2 · the conversion the diff introduces is an identity in B
# ---------------------------------------------------------------------------

P2_BASE = '''"""Sales tax."""
from src.money import convert, to_cents


def tax_for(total: float) -> float:
    """Return the tax for an amount, using this module's money unit."""
    return convert(total)
'''

P2 = _pair(
    pair_id="P2", split="dev", category="units", mechanism="M2",
    title="unit conversion wrapped around a call: x100 vs identity",
    diff_file="src/tax.py", base_code=P2_BASE,
    pr_code=P2_BASE.replace("return convert(total)", "return convert(to_cents(total))"),
    context_a={"src/money.py": '''"""Money helpers for a dollar-denominated service."""


def to_cents(dollars: float) -> int:
    """Convert dollars to integer cents."""
    return round(dollars * 100)


def convert(dollars: float) -> float:
    """Return 20% tax for an amount in dollars."""
    return round(dollars * 0.2, 2)
'''},
    context_b={"src/money.py": '''"""Money helpers. Amounts in this service are integer cents already."""


def to_cents(amount: int) -> int:
    """Amounts are already cents; this is an identity conversion."""
    return amount


def convert(cents: int) -> float:
    """Return 20% tax for an amount in cents."""
    return round(cents / 100 * 0.2, 2)
'''},
    tests_a='''from src.tax import tax_for


def test_tax_on_hundred_dollars():
    assert tax_for(100) == 20.0


def test_tax_on_fifty_dollars():
    assert tax_for(50) == 10.0
''',
    tests_b='''from src.tax import tax_for


def test_tax_on_hundred_cents():
    assert tax_for(100) == 0.2


def test_tax_on_zero():
    assert tax_for(0) == 0.0
''',
    defect_signature=(("cent", "100x", "hundred", "scale"),
                      ("to_cents", "convert", "unit", "dollar")),
    a_expected="tax is 100x too large",
    b_expected="identical value (identity conversion)",
)

# ---------------------------------------------------------------------------
# P3 · M3 · the caller does not depend on the input list being untouched
# ---------------------------------------------------------------------------

P3_BASE = '''"""Cart helpers."""


def with_item(items: list[str], item: str) -> list[str]:
    """Add the item and return the resulting list."""
    result = list(items)
    result.append(item)
    return result
'''

P3 = _pair(
    pair_id="P3", split="dev", category="state_update", mechanism="M3",
    title="defensive copy removed: caller reads the original list vs snapshots it",
    diff_file="src/cart.py", base_code=P3_BASE,
    pr_code=P3_BASE.replace("    result = list(items)", "    result = items"),
    context_a={"src/receipt.py": '''"""Receipt rendering."""
from src.cart import with_item


def render(items: list[str]) -> dict:
    updated = with_item(items, "apple")
    return {"original": len(items), "updated": len(updated)}
'''},
    context_b={"src/receipt.py": '''"""Receipt rendering."""
from src.cart import with_item


def render(items: list[str]) -> dict:
    snapshot = len(items)
    updated = with_item(items, "apple")
    return {"original": snapshot, "updated": len(updated)}
'''},
    tests_a='''from src.receipt import render


def test_original_cart_is_unchanged():
    assert render([]) == {"original": 0, "updated": 1}


def test_existing_item_is_preserved():
    assert render(["pear"]) == {"original": 1, "updated": 2}
''',
    tests_b='''from src.receipt import render


def test_original_count_is_reported():
    assert render([]) == {"original": 0, "updated": 1}


def test_existing_item_is_preserved():
    assert render(["pear"]) == {"original": 1, "updated": 2}
''',
    defect_signature=(("mutat", "in place", "in-place", "alias", "same list",
                       "side effect", "original list"),
                      ("item", "list", "cart", "caller")),
    a_expected="the caller's list is mutated, so 'original' reports 1",
    b_expected="the caller snapshots the count first",
)

# ---------------------------------------------------------------------------
# P4 · M4 · an earlier guard already enforces what the diff tightens
# ---------------------------------------------------------------------------

P4_BASE = '''"""Quantity limits."""
from src.validate import precheck


def accept(qty: int, limit: int) -> bool:
    """Return True when the quantity is within the limit."""
    precheck(qty, limit)
    if qty <= limit:
        return True
    return False
'''

P4 = _pair(
    pair_id="P4", split="dev", category="boundary", mechanism="M4",
    title="inclusive becomes exclusive: only guard vs a guard that already rejects the boundary",
    diff_file="src/limits.py", base_code=P4_BASE,
    pr_code=P4_BASE.replace("if qty <= limit:", "if qty < limit:"),
    context_a={"src/validate.py": '''"""Pre-checks."""


def precheck(qty: int, limit: int) -> None:
    """Reject negative quantities only."""
    if qty < 0:
        raise ValueError("qty must not be negative")
'''},
    context_b={"src/validate.py": '''"""Pre-checks."""


def precheck(qty: int, limit: int) -> None:
    """Reject negative quantities and quantities at or above the limit."""
    if qty < 0:
        raise ValueError("qty must not be negative")
    if qty >= limit:
        raise ValueError("qty must be below the limit")
'''},
    tests_a='''import pytest

from src.limits import accept


def test_boundary_quantity_is_accepted():
    assert accept(10, 10) is True


def test_below_limit_is_accepted():
    assert accept(9, 10) is True


def test_negative_is_rejected():
    with pytest.raises(ValueError):
        accept(-1, 10)
''',
    tests_b='''import pytest

from src.limits import accept
from src.validate import precheck


def test_boundary_quantity_is_rejected_by_the_guard():
    with pytest.raises(ValueError):
        precheck(10, 10)


def test_below_limit_is_accepted():
    assert accept(9, 10) is True


def test_negative_is_rejected():
    with pytest.raises(ValueError):
        accept(-1, 10)
''',
    defect_signature=(("boundary", "equal", "at the limit", "off-by-one", "exclusive"),
                      ("limit", "qty", "accept", ">=", "<=")),
    a_expected="quantity exactly at the limit is wrongly rejected",
    b_expected="the boundary never reaches the changed branch",
)

# ---------------------------------------------------------------------------
# P5 · M5 · the caller catches the new exception type as well
# ---------------------------------------------------------------------------

P5_BASE = '''"""Stock checks."""


def reserve(available: int, requested: int) -> int:
    """Return remaining stock after reserving."""
    if requested > available:
        raise ValueError("insufficient stock")
    return available - requested
'''

P5 = _pair(
    pair_id="P5", split="dev", category="exception_handling", mechanism="M5",
    title="exception type changes: handler misses it vs handler covers both",
    diff_file="src/stock.py", base_code=P5_BASE,
    pr_code=P5_BASE.replace('raise ValueError("insufficient stock")',
                            'raise LookupError("insufficient stock")'),
    context_a={"src/api.py": '''"""Request handling."""
from src.stock import reserve


def handle(available: int, requested: int) -> dict:
    try:
        left = reserve(available, requested)
    except ValueError:
        return {"status": 409}
    return {"status": 200, "left": left}
'''},
    context_b={"src/api.py": '''"""Request handling."""
from src.stock import reserve


def handle(available: int, requested: int) -> dict:
    try:
        left = reserve(available, requested)
    except (ValueError, LookupError):
        return {"status": 409}
    return {"status": 200, "left": left}
'''},
    tests_a='''from src.api import handle


def test_insufficient_stock_maps_to_conflict():
    assert handle(1, 2) == {"status": 409}


def test_sufficient_stock_succeeds():
    assert handle(5, 2) == {"status": 200, "left": 3}
''',
    tests_b='''from src.api import handle


def test_insufficient_stock_maps_to_conflict():
    assert handle(1, 2) == {"status": 409}


def test_sufficient_stock_succeeds():
    assert handle(5, 2) == {"status": 200, "left": 3}
''',
    defect_signature=(("lookuperror", "exception type", "error type"),
                      ("valueerror", "catch", "handler", "reserve", "insufficient")),
    a_expected="LookupError escapes the ValueError handler",
    b_expected="same 409 response",
)

# ---------------------------------------------------------------------------
# P6 · M6 · every caller passes the argument explicitly
# ---------------------------------------------------------------------------

P6_BASE = '''"""User filters."""


def active(limit: int | None = None) -> list[str]:
    """Return active usernames. `limit=None` means no limit."""
    users = ["a", "b", "c"]
    if limit is None:
        return users
    return users[:limit]
'''

P6 = _pair(
    pair_id="P6", split="heldout", category="default_arguments", mechanism="M6",
    title="default changes from None to 0: implicit caller vs explicit caller",
    diff_file="src/filters.py", base_code=P6_BASE,
    pr_code=P6_BASE.replace("def active(limit: int | None = None) -> list[str]:",
                            "def active(limit: int | None = 0) -> list[str]:"),
    context_a={"src/service.py": '''"""Service layer."""
from src.filters import active


def list_active() -> list[str]:
    return active()
'''},
    context_b={"src/service.py": '''"""Service layer."""
from src.filters import active

# This deployment shows no users until a quota is configured.
SERVICE_LIMIT = 0


def list_active() -> list[str]:
    return active(limit=SERVICE_LIMIT)
'''},
    tests_a='''from src.service import list_active


def test_all_active_users_listed():
    assert list_active() == ["a", "b", "c"]
''',
    tests_b='''from src.service import list_active


def test_quota_zero_lists_nothing():
    assert list_active() == []
''',
    defect_signature=(("default",), ("limit", "none", "zero", "implicit")),
    a_expected="unlimited becomes zero results",
    b_expected="default never used",
)

# ---------------------------------------------------------------------------
# P7 · M1 · the callee ignores the retry count (transport retries internally)
# ---------------------------------------------------------------------------

P7_BASE = '''"""Fetch helper."""
from src.client import get


def fetch(url: str) -> str:
    """Fetch the URL."""
    return get(url)
'''

P7 = _pair(
    pair_id="P7", split="heldout", category="reliability", mechanism="M1",
    title="retry count tightened: honoured by the client vs ignored",
    diff_file="src/retry.py", base_code=P7_BASE,
    pr_code=P7_BASE.replace("return get(url)", "return get(url, retries=1)"),
    context_a={"src/client.py": '''"""HTTP client honouring a retry count."""

ATTEMPTS = {"n": 0}


def get(url: str, retries: int = 3) -> str:
    """Retry up to `retries` times; succeed on the second attempt."""
    for _ in range(retries):
        ATTEMPTS["n"] += 1
        if ATTEMPTS["n"] >= 2:
            return "ok"
    raise RuntimeError("transient failure")
'''},
    context_b={"src/client.py": '''"""HTTP client: the transport retries internally, so the count is unused."""


def get(url: str, retries: int = 3) -> str:
    """Fetch once; the transport retries internally and always returns."""
    return "ok"
'''},
    tests_a='''import src.client as client
from src.retry import fetch


def test_transient_failure_is_retried():
    client.ATTEMPTS["n"] = 0
    assert fetch("https://example.test") == "ok"
''',
    tests_b='''from src.retry import fetch


def test_fetch_returns_payload():
    assert fetch("https://example.test") == "ok"
''',
    defect_signature=(("retry", "retries", "attempt"),
                      ("transient", "one attempt", "no retry", "gives up")),
    a_expected="RuntimeError: no retry left",
    b_expected="same payload",
)

# ---------------------------------------------------------------------------
# P8 · M7 · the caller guarantees integer quantities
# ---------------------------------------------------------------------------

P8_BASE = '''"""Billing units."""
import math


def billable(qty: float) -> int:
    """Return the number of billable units for a quantity."""
    return math.ceil(qty)
'''

P8 = _pair(
    pair_id="P8", split="heldout", category="rounding", mechanism="M7",
    title="ceil becomes floor: fractional callers vs integer-only callers",
    diff_file="src/units.py", base_code=P8_BASE,
    pr_code=P8_BASE.replace("return math.ceil(qty)", "return math.floor(qty)"),
    context_a={"src/orders.py": '''"""Order lines that may be fractional (e.g. 1.5 hours)."""
from src.units import billable


def line_units(qty: float) -> int:
    return billable(qty)
'''},
    context_b={"src/orders.py": '''"""Order lines: quantities are whole units by construction."""
from src.units import billable


def line_units(qty: int) -> int:
    return billable(int(qty))
'''},
    tests_a='''from src.orders import line_units


def test_fractional_quantity_rounds_up():
    assert line_units(1.5) == 2


def test_whole_quantity_is_unchanged():
    assert line_units(2) == 2
''',
    tests_b='''from src.orders import line_units


def test_whole_quantity_is_unchanged():
    assert line_units(2) == 2


def test_zero_quantity():
    assert line_units(0) == 0
''',
    defect_signature=(("ceil", "floor"), ("round", "unit", "billable", "fraction")),
    a_expected="1.5 bills as 1 unit instead of 2",
    b_expected="integers: ceil equals floor",
)

# ---------------------------------------------------------------------------
# P9 · M6 · the configuration always supplies the key
# ---------------------------------------------------------------------------

P9_BASE = '''"""Pricing from configuration."""


def rate_for(cfg: dict) -> float:
    """Return the configured rate."""
    return cfg["rate"]
'''

P9 = _pair(
    pair_id="P9", split="heldout", category="config_handling", mechanism="M6",
    title="direct indexing replaced by a default: error becomes silent zero",
    diff_file="src/pricing.py", base_code=P9_BASE,
    pr_code=P9_BASE.replace('return cfg["rate"]', 'return cfg.get("rate", 0.0)'),
    context_a={"src/config.py": '''"""Configuration assembly."""


def build(rate: float | None = None) -> dict:
    """Build the settings dict.

    An unset rate is a configuration error: it must stay absent so callers fail
    loudly instead of silently pricing everything at zero.
    """
    cfg: dict = {}
    if rate is not None:
        cfg["rate"] = rate
    return cfg
'''},
    context_b={"src/config.py": '''"""Configuration assembly."""


def build(rate: float | None = None) -> dict:
    """Build the settings dict, always including a rate."""
    cfg: dict = {"rate": 0.2}
    if rate is not None:
        cfg["rate"] = rate
    return cfg
'''},
    tests_a='''import pytest

from src.config import build
from src.pricing import rate_for


def test_configured_rate_is_used():
    assert rate_for(build(0.2)) == 0.2


def test_missing_rate_is_a_configuration_error():
    with pytest.raises(KeyError):
        rate_for(build())
''',
    tests_b='''from src.config import build
from src.pricing import rate_for


def test_configured_rate_is_used():
    assert rate_for(build(0.2)) == 0.2


def test_default_rate_is_present():
    assert rate_for(build()) == 0.2
''',
    defect_signature=(("missing", "absent", "unset"),
                      ("key", "rate", "config", "default", "zero")),
    a_expected="missing rate silently becomes 0.0 instead of raising",
    b_expected="key always present",
)

# ---------------------------------------------------------------------------
# P10 · M7 · every ingested URL is already https
# ---------------------------------------------------------------------------

P10_BASE = '''"""URL checks."""


def is_fetchable(url: str) -> bool:
    """Return True for URLs this service may fetch."""
    return url.startswith("http")
'''

P10 = _pair(
    pair_id="P10", split="heldout", category="input_validation", mechanism="M7",
    title="scheme check narrowed to https: plain-http feed vs https-only feed",
    diff_file="src/urls.py", base_code=P10_BASE,
    pr_code=P10_BASE.replace('return url.startswith("http")',
                             'return url.startswith("https")'),
    context_a={"src/ingest.py": '''"""Ingest feed entries (the feed mixes schemes)."""
from src.urls import is_fetchable


def accepted(urls: list[str]) -> list[str]:
    return [u for u in urls if is_fetchable(u)]
'''},
    context_b={"src/ingest.py": '''"""Ingest feed entries (the feed is https-only by contract)."""
from src.urls import is_fetchable


def accepted(urls: list[str]) -> list[str]:
    for url in urls:
        if not url.startswith("https://"):
            raise ValueError("feed must be https-only")
    return [u for u in urls if is_fetchable(u)]
'''},
    tests_a='''from src.ingest import accepted


def test_http_url_is_accepted():
    assert accepted(["http://a.example/x"]) == ["http://a.example/x"]


def test_https_url_is_accepted():
    assert accepted(["https://a.example/x"]) == ["https://a.example/x"]
''',
    tests_b='''import pytest

from src.ingest import accepted


def test_https_url_is_accepted():
    assert accepted(["https://a.example/x"]) == ["https://a.example/x"]


def test_plain_http_is_rejected_by_the_feed_contract():
    with pytest.raises(ValueError):
        accepted(["http://a.example/x"])
''',
    defect_signature=(("http", "https", "scheme"), ("url", "fetchable", "reject", "drop")),
    a_expected="a plain-http feed entry is dropped",
    b_expected="all inputs already https",
)

# ---------------------------------------------------------------------------
# P11 · M7 · the caller never passes an empty list
# ---------------------------------------------------------------------------

P11_BASE = '''"""Statistics."""


def average(items: list[float]) -> float:
    """Return the arithmetic mean."""
    if not items:
        return 0.0
    return sum(items) / len(items)
'''

P11 = _pair(
    pair_id="P11", split="heldout", category="empty_input", mechanism="M7",
    title="empty-guard narrowed to None: empty series caller vs guarded caller",
    diff_file="src/stats.py", base_code=P11_BASE,
    pr_code=P11_BASE.replace("if not items:", "if items is None:"),
    context_a={"src/report.py": '''"""Report building."""
from src.stats import average


def summary(values: list[float]) -> dict:
    """Summarise; an empty series reports an average of 0.0."""
    return {"avg": average(values)}
'''},
    context_b={"src/report.py": '''"""Report building."""
from src.stats import average


def summary(values: list[float]) -> dict:
    """Summarise; an empty series is rejected before averaging."""
    if not values:
        raise ValueError("values must not be empty")
    return {"avg": average(values)}
'''},
    tests_a='''from src.report import summary


def test_empty_report_is_zero():
    assert summary([]) == {"avg": 0.0}


def test_single_value():
    assert summary([2.0]) == {"avg": 2.0}
''',
    tests_b='''import pytest

from src.report import summary


def test_single_value():
    assert summary([2.0]) == {"avg": 2.0}


def test_empty_values_are_rejected_before_averaging():
    with pytest.raises(ValueError):
        summary([])
''',
    defect_signature=(("empty", "zero division", "zerodivision", "divide"),
                      ("list", "items", "average", "mean", "guard")),
    a_expected="ZeroDivisionError on an empty report",
    b_expected="empty list never reaches the function",
)


PAIRS: tuple[ContrastPair, ...] = (P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11)

PAIRS_BY_ID = {p.pair_id: p for p in PAIRS}

VARIANTS = ("a", "b")
LABELS = {"a": "bug", "b": "clean"}

# Classification of the CLEAN variant, frozen before the held-out run.
#
#   unambiguous       the diff is a no-op for the changed function itself, on
#                     every input: the callee ignores the new argument (M1), the
#                     conversion is the identity (M2), or the changed branch is
#                     unreachable behind an earlier guard (M4). Any finding here
#                     is genuinely unfounded.
#
#   context_dependent the diff does change the changed function's own observable
#                     behaviour for some direct input (input mutation, exception
#                     type, default value, rounding, accepted scheme, empty-input
#                     handling). The surrounding code makes it harmless in this
#                     repository, but a reviewer who reports that change is
#                     describing something real. Such a finding is scored
#                     CONTESTED, never FP, and the pair is excluded from pair
#                     consistency.
CLEAN_CLASS: dict[str, str] = {
    "P1": "unambiguous",
    "P2": "unambiguous",
    "P3": "context_dependent",
    "P4": "unambiguous",
    "P5": "context_dependent",
    "P6": "context_dependent",
    "P7": "unambiguous",
    "P8": "context_dependent",
    "P9": "context_dependent",
    "P10": "context_dependent",
    "P11": "context_dependent",
}

UNCONTROVERSIAL = "unambiguous"
CONTESTED = "context_dependent"


def clean_class_of(pair_id: str) -> str:
    return CLEAN_CLASS[pair_id]


def files_for(pair: ContrastPair, variant: str, *, apply_pr: bool) -> dict[str, str]:
    """Full file map for one variant, base or PR revision."""
    context = pair.context_a if variant == "a" else pair.context_b
    files = {"src/__init__.py": INIT, **context}
    files[pair.diff_file] = pair.pr_code if apply_pr else pair.base_code
    return files


def tests_for(pair: ContrastPair, variant: str) -> str:
    return pair.tests_a if variant == "a" else pair.tests_b


def build_index() -> list[ContrastPair]:
    """Return the frozen pair set, validating its structural invariants."""
    ids = [p.pair_id for p in PAIRS]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate pair ids")
    for pair in PAIRS:
        if pair.pair_id not in CLEAN_CLASS:
            raise ValueError(f"{pair.pair_id}: missing clean-variant classification")
        if pair.split not in {"dev", "heldout"}:
            raise ValueError(f"{pair.pair_id}: bad split {pair.split!r}")
        if pair.diff_file not in files_for(pair, "a", apply_pr=False):
            raise ValueError(f"{pair.pair_id}: diff_file missing from variant A")
        if pair.diff_file not in files_for(pair, "b", apply_pr=False):
            raise ValueError(f"{pair.pair_id}: diff_file missing from variant B")
        if pair.base_code == pair.pr_code:
            raise ValueError(f"{pair.pair_id}: PR does not change the file")
        if pair.diff_file in pair.context_a or pair.diff_file in pair.context_b:
            raise ValueError(f"{pair.pair_id}: context overrides the changed file")
        if len(pair.defect_signature) < 2:
            raise ValueError(f"{pair.pair_id}: signature needs at least two groups")
        for variant in VARIANTS:
            if "def test_" not in tests_for(pair, variant):
                raise ValueError(f"{pair.pair_id}: variant {variant} has no tests")
    return list(PAIRS)
