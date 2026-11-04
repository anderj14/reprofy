import pytest

from shop.pricing import tax_for_items


def test_three_dime_items_tax_is_three_cents():
    prices = [0.10, 0.10, 0.10]
    assert tax_for_items(prices, 0.07) == pytest.approx(0.03)
