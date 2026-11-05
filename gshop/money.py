"""Decimal money helpers (audit P2-9).

All monetary arithmetic happens in Decimal. Floats exist only at the
SQLite REAL boundary: sqlite3.register_adapter converts Decimal to float
on the way into the database, so the schema stays unchanged.
"""
from __future__ import annotations

import sqlite3
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

D = Decimal
ZERO = Decimal("0")
CENT = Decimal("0.01")


def to_dec(value, default=None):
    """Coerce float/int/str/None/Decimal to Decimal.

    Floats go through repr() so 0.29 becomes Decimal("0.29") rather than the
    full binary expansion 0.289999999999999980015985556747182272374629974.
    """
    if isinstance(value, Decimal):
        return value
    if value is None:
        return ZERO if default is None else default
    try:
        if isinstance(value, float):
            return Decimal(repr(value))
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return ZERO if default is None else default


def dec_str(d):
    """Decimal to canonical string: fixed point, trailing zeros stripped."""
    s = format(to_dec(d).normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def q2(d):
    """Quantize to 2 decimal places with HALF_UP (money rounding)."""
    return to_dec(d).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_usdt(amount):
    """Render as 12.34 USDT."""
    return f"{q2(amount):.2f} USDT"


def parse_user_amount(text):
    """Parse a user supplied amount. Returns None when invalid.

    Rejects empty, non numeric, non finite (inf/nan) and non positive input.
    """
    if text is None:
        return None
    raw = str(text).strip().replace(",", "")
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value <= ZERO:
        return None
    return value


def points_from_usdt(amount, rate):
    """USDT to points, floored. Exact: 0.29 * 100 == 29."""
    r = to_dec(rate, Decimal("1"))
    if r <= ZERO:
        r = Decimal("1")
    return int((to_dec(amount) * r).to_integral_value(rounding=ROUND_DOWN))


def usdt_from_points(points, rate):
    """points to USDT at 2dp HALF_UP (round() alone does bankers rounding)."""
    r = to_dec(rate, Decimal("1"))
    if r <= ZERO:
        r = Decimal("1")
    return q2(to_dec(points) / r)


# SQLite boundary: schema stays REAL, Decimal is exact in Python and
# converted on the way in. Importing gshop.money is enough to arm this.
sqlite3.register_adapter(Decimal, lambda d: float(d))

__all__ = [
    "CENT",
    "D",
    "ZERO",
    "dec_str",
    "fmt_usdt",
    "parse_user_amount",
    "points_from_usdt",
    "q2",
    "to_dec",
    "usdt_from_points",
]
