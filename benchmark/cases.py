"""Original benchmark PR cases for PR Guardian.

Each case is a change to one small Python module. The base revision is correct and
its test file encodes the specification. A PR is the base with ONE edit applied.

Ground truth per case:
  has_bug      True if the PR introduces a real, test-demonstrable defect.
  bug_keyword  Phrase used by the deterministic matcher in evaluate.py.
  bug_file     Module path the defect lives in (src/<module>.py).
  bug_line     Line number of the defect in the PR file (only if has_bug).
  dev_split    True -> development set (may be used to tune prompts).
               False -> held-out test set (must NOT be used for prompt tuning).

Every case is original work written for this project.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    title: str
    module: str
    base_code: str
    pr_code: str
    test_code: str
    has_bug: bool
    bug_keyword: str
    dev_split: bool


def _apply(base: str, old: str, new: str) -> str:
    """Apply exactly one replacement. Fails loudly if the anchor is missing or
    the edit does not change the code, so bad cases cannot be generated silently."""
    count = base.count(old)
    if count != 1:
        raise ValueError(f"anchor must occur exactly once, found {count}: {old!r}")
    pr = base.replace(old, new, 1)
    if pr == base:
        raise ValueError(f"edit did not change code: {old!r}")
    return pr


def _line_of(code: str, needle: str) -> int:
    for n, line in enumerate(code.splitlines(), start=1):
        if needle in line:
            return n
    raise ValueError(f"needle not found: {needle!r}")


# ---------------------------------------------------------------------------
# Module definitions: base code + test file (the specification)
# ---------------------------------------------------------------------------

M_DISCOUNT = {
    "module": "discount",
    "base": '''"""Order discount rules."""


def calculate_discount(order_total: float) -> float:
    """Orders of 100 or more get 10% off."""
    if order_total >= 100:
        return order_total * 0.10
    return 0.0
''',
    "tests": '''from src.discount import calculate_discount


def test_exact_threshold_discounted():
    assert calculate_discount(100) == 10.0


def test_below_threshold_not_discounted():
    assert calculate_discount(99.99) == 0.0


def test_large_order():
    assert calculate_discount(200) == 20.0
''',
}

M_USERS = {
    "module": "users",
    "base": '''"""User lookup helpers."""


def find_email(user: dict | None) -> str:
    """Return the user's normalised email, or '' when user or email is missing."""
    if user is None:
        return ""
    email = user.get("email")
    if not email:
        return ""
    return email.strip().lower()
''',
    "tests": '''from src.users import find_email


def test_none_user():
    assert find_email(None) == ""


def test_missing_email():
    assert find_email({"name": "a"}) == ""


def test_normalises_email():
    assert find_email({"email": " A@X.COM "}) == "a@x.com"
''',
}

M_CART = {
    "module": "cart",
    "base": '''"""Shopping cart totals."""


def cart_total(items: list[dict]) -> float:
    """Sum price * quantity; ignore items with quantity <= 0."""
    total = 0.0
    for item in items:
        qty = item.get("qty", 0)
        if qty <= 0:
            continue
        total += item["price"] * qty
    return round(total, 2)
''',
    "tests": '''from src.cart import cart_total


def test_basic_total():
    assert cart_total([{"price": 2.5, "qty": 2}]) == 5.0


def test_zero_quantity_ignored():
    assert cart_total([{"price": 9.0, "qty": 0}]) == 0.0


def test_negative_quantity_ignored():
    assert cart_total([{"price": 9.0, "qty": -1}]) == 0.0


def test_empty_cart():
    assert cart_total([]) == 0.0
''',
}

M_INVENTORY = {
    "module": "inventory",
    "base": '''"""Inventory stock management."""


class OutOfStock(Exception):
    pass


def reserve(stock: dict[str, int], sku: str, qty: int) -> dict[str, int]:
    """Reserve qty units of sku and return a NEW stock dict. Never mutate input."""
    if qty <= 0:
        raise ValueError("qty must be positive")
    available = stock.get(sku, 0)
    if available < qty:
        raise OutOfStock(sku)
    new = dict(stock)
    new[sku] = available - qty
    return new
''',
    "tests": '''import pytest

from src.inventory import OutOfStock, reserve


def test_reserve_reduces_stock():
    assert reserve({"a": 5}, "a", 2) == {"a": 3}


def test_reserve_insufficient_raises():
    with pytest.raises(OutOfStock):
        reserve({"a": 1}, "a", 2)


def test_reserve_exact_stock_allowed():
    assert reserve({"a": 2}, "a", 2) == {"a": 0}


def test_reserve_nonpositive_raises():
    with pytest.raises(ValueError):
        reserve({"a": 1}, "a", 0)


def test_reserve_does_not_mutate_input():
    s = {"a": 5}
    reserve(s, "a", 1)
    assert s == {"a": 5}
''',
}

M_PAGINATION = {
    "module": "pagination",
    "base": '''"""Pagination helpers."""


def page_slice(items: list, page: int, size: int) -> list:
    """Return items for a 1-based page. page < 1 or size < 1 raises ValueError."""
    if page < 1 or size < 1:
        raise ValueError("invalid page or size")
    start = (page - 1) * size
    return items[start:start + size]
''',
    "tests": '''import pytest

from src.pagination import page_slice


def test_first_page():
    assert page_slice([1, 2, 3, 4], 1, 2) == [1, 2]


def test_last_partial_page():
    assert page_slice([1, 2, 3], 2, 2) == [3]


def test_invalid_page_raises():
    with pytest.raises(ValueError):
        page_slice([1], 0, 2)


def test_invalid_size_raises():
    with pytest.raises(ValueError):
        page_slice([1], 1, 0)
''',
}

M_COUPON = {
    "module": "coupon",
    "base": '''"""Coupon validation."""
from datetime import date


def coupon_valid(expires: date, today: date, used: bool) -> bool:
    """A coupon is valid if unused and expires on or after today."""
    if used:
        return False
    return expires >= today
''',
    "tests": '''from datetime import date

from src.coupon import coupon_valid


def test_valid_on_expiry_day():
    assert coupon_valid(date(2026, 1, 10), date(2026, 1, 10), False) is True


def test_expired():
    assert coupon_valid(date(2026, 1, 9), date(2026, 1, 10), False) is False


def test_used_is_invalid():
    assert coupon_valid(date(2030, 1, 1), date(2026, 1, 1), True) is False
''',
}

M_PARSING = {
    "module": "parsing",
    "base": '''"""Config value parsing."""


def parse_port(value: str) -> int:
    """Parse a TCP port in 1..65535. Raises ValueError otherwise."""
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range: {port}")
    return port
''',
    "tests": '''import pytest

from src.parsing import parse_port


def test_valid_port():
    assert parse_port("8080") == 8080


def test_bad_text_raises():
    with pytest.raises(ValueError):
        parse_port("abc")


def test_out_of_range_raises():
    with pytest.raises(ValueError):
        parse_port("70000")


def test_upper_bound_valid():
    assert parse_port("65535") == 65535


def test_zero_port_rejected():
    with pytest.raises(ValueError):
        parse_port("0")
''',
}

M_RETRY = {
    "module": "retry",
    "base": '''"""Retry helper."""


def with_retries(fn, attempts: int = 3):
    """Call fn up to `attempts` times; re-raise the last error if all fail."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last_exc = None
    for _ in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    raise last_exc
''',
    "tests": '''import pytest

from src.retry import with_retries


def test_succeeds_first_try():
    assert with_retries(lambda: 42) == 42


def test_eventual_success():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    assert with_retries(flaky, attempts=3) == "ok"


def test_all_fail_reraises():
    def always():
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        with_retries(always, attempts=2)


def test_zero_attempts_rejected():
    with pytest.raises(ValueError):
        with_retries(lambda: 1, attempts=0)
''',
}

M_RANKING = {
    "module": "ranking",
    "base": '''"""Ranking helpers."""


def top_n(scores: dict[str, int], n: int) -> list[str]:
    """Return the n names with the highest score, ties broken by name ascending."""
    if n <= 0:
        return []
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _ in ordered[:n]]
''',
    "tests": '''from src.ranking import top_n


def test_top_two():
    assert top_n({"a": 3, "b": 5, "c": 1}, 2) == ["b", "a"]


def test_tie_broken_by_name():
    assert top_n({"b": 2, "a": 2}, 1) == ["a"]


def test_zero_n():
    assert top_n({"a": 1}, 0) == []
''',
}


# ---------------------------------------------------------------------------
# Edit specifications: (title, old, new, has_bug, dev_split)
# has_bug=True edits break the spec (verified by the module tests below).
# has_bug=False edits are behaviour-preserving (clean PRs).
# ---------------------------------------------------------------------------

SPECS: list[tuple[dict, str, str, str, str, bool, bool]] = [
    # (module, category, title, old, new, has_bug, dev_split)
    (M_DISCOUNT, "boundary", "threshold excluded: uses > instead of >=",
     "order_total >= 100", "order_total > 100", True, True),
    (M_DISCOUNT, "boundary", "discount rate changed to 1%",
     "order_total * 0.10", "order_total * 0.01", True, True),
    (M_DISCOUNT, "clean", "extract local variable (no behaviour change)",
     "return order_total * 0.10", "discount = order_total * 0.10\n        return discount", False, True),
    (M_DISCOUNT, "boundary", "threshold moved to 101",
     "order_total >= 100", "order_total >= 101", True, False),
    (M_DISCOUNT, "clean", "use explicit float literal (no behaviour change)",
     "return 0.0", "return 0.00", False, False),

    (M_USERS, "null_handling", "None check removed before dereference",
     "    if user is None:\n        return \"\"\n", "", True, True),
    (M_USERS, "null_handling", "missing-email guard removed",
     "    if not email:\n        return \"\"\n", "", True, True),
    (M_USERS, "clean", "casefold instead of lower (equivalent for ASCII)",
     "return email.strip().lower()", "return email.strip().casefold()", False, True),
    (M_USERS, "null_handling", "missing-user guard inverted",
     "if user is None:", "if user is not None:", True, False),

    (M_CART, "clean", "zero-quantity guard tightened (equivalent: zero adds nothing)",
     "if qty <= 0:", "if qty < 0:", False, True),
    (M_CART, "logic_branch", "price added to quantity instead of multiplied",
     "total += item[\"price\"] * qty", "total += item[\"price\"] + qty", True, True),
    (M_CART, "data_filter", "negative quantity no longer filtered",
     "if qty <= 0:", "if qty == 0:", True, False),
    (M_CART, "clean", "round via float() wrapper (no behaviour change)",
     "return round(total, 2)", "return round(float(total), 2)", False, False),

    (M_INVENTORY, "state_update", "mutates caller's stock dict",
     "new = dict(stock)", "new = stock", True, True),
    (M_INVENTORY, "exception_handling", "insufficient-stock check off by one",
     "if available < qty:", "if available <= qty:", True, True),
    (M_INVENTORY, "api_validation", "non-positive qty check weakened",
     "if qty <= 0:\n        raise ValueError", "if qty < 0:\n        raise ValueError", True, False),
    (M_INVENTORY, "clean", "inline stock lookup in guard (no behaviour change)",
     "    available = stock.get(sku, 0)\n    if available < qty:\n        raise OutOfStock(sku)\n    new = dict(stock)\n    new[sku] = available - qty",
     "    if stock.get(sku, 0) < qty:\n        raise OutOfStock(sku)\n    new = dict(stock)\n    new[sku] = stock.get(sku, 0) - qty", False, False),

    (M_PAGINATION, "api_validation", "page < 1 validation removed",
     "if page < 1 or size < 1:", "if size < 1:", True, True),
    (M_PAGINATION, "logic_branch", "off-by-one start index",
     "start = (page - 1) * size", "start = page * size", True, True),
    (M_PAGINATION, "api_validation", "size validation removed",
     "if page < 1 or size < 1:", "if page < 1:", True, False),
    (M_PAGINATION, "clean", "explicit local for slice end (no behaviour change)",
     "return items[start:start + size]", "end = start + size\n    return items[start:end]", False, False),

    (M_COUPON, "boundary", "expiry day treated as expired",
     "return expires >= today", "return expires > today", True, True),
    (M_COUPON, "state_update", "used-coupon check removed",
     "    if used:\n        return False\n", "", True, True),
    (M_COUPON, "boundary", "comparison flipped to <=",
     "return expires >= today", "return expires <= today", True, False),
    (M_COUPON, "clean", "named intermediate (no behaviour change)",
     "return expires >= today", "valid = expires >= today\n    return valid", False, False),

    (M_PARSING, "exception_handling", "parse error swallowed, returns 0",
     "raise ValueError(f\"not a number: {value!r}\") from exc", "return 0", True, True),
    (M_PARSING, "boundary", "upper port bound excluded",
     "if not 1 <= port <= 65535:", "if not 1 <= port < 65535:", True, True),
    (M_PARSING, "boundary", "lower port bound allows 0",
     "if not 1 <= port <= 65535:", "if not 0 <= port <= 65535:", True, False),
    (M_PARSING, "clean", "parenthesised range check (no behaviour change)",
     "if not 1 <= port <= 65535:", "if not (1 <= port <= 65535):", False, False),

    (M_RETRY, "exception_handling", "last error swallowed on final failure",
     "raise last_exc", "return None", True, True),
    (M_RETRY, "boundary", "attempts validation off by one",
     "if attempts < 1:", "if attempts < 0:", True, True),
    (M_RETRY, "exception_handling", "retries only once regardless of attempts",
     "for _ in range(attempts):", "for _ in range(1):", True, False),
    (M_RETRY, "clean", "rename loop variable (no behaviour change)",
     "for _ in range(attempts):", "for _attempt in range(attempts):", False, False),

    (M_RANKING, "logic_branch", "sorted ascending instead of descending",
     "key=lambda kv: (-kv[1], kv[0])", "key=lambda kv: (kv[1], kv[0])", True, True),
    (M_RANKING, "logic_branch", "tie-break removed (non-deterministic order)",
     "key=lambda kv: (-kv[1], kv[0])", "key=lambda kv: -kv[1]", True, True),
    (M_RANKING, "clean", "n <= 0 guard tightened (equivalent: n=0 already returns [])",
     "if n <= 0:", "if n < 0:", False, False),
    (M_RANKING, "clean", "list comprehension rewrite (no behaviour change)",
     "return [name for name, _ in ordered[:n]]", "return [kv[0] for kv in ordered[:n]]", False, False),
]


def build_cases() -> list[Case]:
    """Build every case from SPECS. Raises ValueError if any edit is invalid."""
    out: list[Case] = []
    counters: dict[str, int] = {}
    for mod, category, title, old, new, has_bug, dev in SPECS:
        pr = _apply(mod["base"], old, new)
        counters[mod["module"]] = counters.get(mod["module"], 0) + 1
        case_id = f"{mod['module'].upper()}-{counters[mod['module']]:02d}"
        out.append(Case(
            case_id=case_id,
            category=category,
            title=title,
            module=mod["module"],
            base_code=mod["base"],
            pr_code=pr,
            test_code=mod["tests"],
            has_bug=has_bug,
            bug_keyword=title if has_bug else "",
            dev_split=dev,
        ))
    return out
