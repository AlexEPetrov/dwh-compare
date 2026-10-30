from decimal import Decimal

from dwh_compare.db import stringify


def test_numeric_trailing_zeros_equal():
    assert stringify(Decimal("123.0")) == stringify(Decimal("123.000"))
    assert stringify(Decimal("123.0")) == "123"
    assert stringify(Decimal("0.0")) == "0"
    assert stringify(Decimal("10.50")) == "10.5"
