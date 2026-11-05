"""P2-9 money layer: pytest-native tests for gshop.money and the Decimal paths."""
import os
import tempfile
from decimal import Decimal

import pytest

from gshop.money import (
    ZERO,
    dec_str,
    fmt_usdt,
    parse_user_amount,
    points_from_usdt,
    q2,
    to_dec,
    usdt_from_points,
)


def test_to_dec_from_float_uses_repr():
    assert to_dec(0.29) == Decimal("0.29")
    assert str(to_dec(0.29)) == "0.29"


def test_to_dec_none_and_bad():
    assert to_dec(None) == ZERO
    assert to_dec("abc") == ZERO
    assert to_dec("abc", Decimal("9")) == Decimal("9")


def test_dec_str_strips_trailing_zeros():
    assert dec_str(Decimal("10.437000")) == "10.437"
    assert dec_str(Decimal("10.000")) == "10"
    assert dec_str(ZERO) == "0"


def test_q2_half_up():
    assert q2("2.005") == Decimal("2.01")
    assert q2("2.004") == Decimal("2.00")


def test_fmt_usdt():
    assert fmt_usdt("0.145") == "0.15 USDT"
    assert fmt_usdt(2) == "2.00 USDT"


def test_points_from_usdt_floors():
    assert points_from_usdt("0.29", 100) == 29
    assert points_from_usdt("0.57", 100) == 57
    assert points_from_usdt("0.58", 50) == 29
    assert points_from_usdt("10.29", 100) == 1029


def test_usdt_from_points_half_up():
    assert usdt_from_points(250, 100) == Decimal("2.50")
    assert usdt_from_points(1, 3) == Decimal("0.33")


def test_parse_user_amount():
    assert parse_user_amount(" 10.5 ") == Decimal("10.5")
    assert parse_user_amount("1,234.5") == Decimal("1234.5")
    for bad in (None, "", "   ", "abc", "inf", "nan", "-5", "0"):
        assert parse_user_amount(bad) is None, bad


def test_sqlite_roundtrip_keeps_precision():
    from gshop.db import Database
    from gshop.shop_service import ShopService

    tmp = tempfile.mkdtemp()
    db = Database(os.path.join(tmp, "t.db"))
    svc = ShopService(db, None)
    db.set_setting("points_rate", 100)
    db.set_setting("min_recharge_usdt", "0.01")
    db.set_setting("max_recharge_usdt", "100000")
    made = [svc.create_recharge(10001, a) for a in ("0.29", "0.57", "0.58", "10.29")]
    assert [r.points for r in made] == [29, 57, 58, 1029]
    back = db.list_recharges(10001)
    assert all(isinstance(r.amount, Decimal) for r in back)
    assert sum((r.amount for r in back), ZERO) == Decimal("11.73")


@pytest.mark.parametrize("value", ["0.1", "0.2", "0.3", "1234.56"])
def test_decimal_never_drifts(value):
    d = to_dec(value)
    assert Decimal(str(d)) == Decimal(value)
