import pytest

from shop.pricing import checkout_total


def test_coupon_percent_over_100_raises_value_error():
    with pytest.raises(ValueError):
        checkout_total(100.0, coupon_percent=150, tax_rate=0.05)
