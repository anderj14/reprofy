import pytest

from shop.pricing import coupon_discount


def test_save20_coupon_gives_twenty_percent_discount():
    assert coupon_discount(100.0, "SAVE20") == pytest.approx(20.0)
