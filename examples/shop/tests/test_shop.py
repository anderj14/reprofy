import pytest

from shop.catalog import PRODUCTS, list_products
from shop.inventory import Inventory
from shop.orders import OrderService
from shop.pricing import checkout_total, tax_for, tax_for_items
from shop.users import EmailAlreadyRegistered, UserRegistry


def test_regular_checkout_with_coupon_and_tax():
    assert checkout_total(100.0, coupon_percent=10, tax_rate=0.05) == pytest.approx(94.5)


def test_zero_discount_checkout():
    assert checkout_total(20.0) == 20.0


def test_tax_for_whole_currency_amount():
    assert tax_for(100.0, 0.05) == 5.0


def test_item_tax_for_regular_order():
    assert tax_for_items([10.0, 20.0], 0.05) == 1.5


def test_first_product_page_has_expected_items():
    assert [p.sku for p in list_products(page=1, page_size=3)] == ["MUG-01", "TEE-01", "BAG-01"]


def test_page_outside_catalog_is_empty():
    assert list_products(page=5, page_size=3) == []


def test_registration_and_exact_duplicate_rejection():
    registry = UserRegistry()
    assert registry.register("ana@example.test") == "ana@example.test"
    with pytest.raises(EmailAlreadyRegistered):
        registry.register("ana@example.test")


def test_order_creation_reduces_stock():
    inventory = Inventory({"MUG-01": 4})
    orders = OrderService(inventory)
    order = orders.create("ORD-1", "MUG-01", 2)
    assert order.status == "created"
    assert inventory.available("MUG-01") == 2


def test_catalog_has_synthetic_demo_products():
    assert len(PRODUCTS) == 7
