from shop.inventory import Inventory
from shop.orders import OrderService


def test_cancel_restores_reserved_units_exactly_once():
    inventory = Inventory({"MUG-01": 4})
    orders = OrderService(inventory)
    orders.create("ORD-1", "MUG-01", 2)
    assert inventory.available("MUG-01") == 2
    cancelled = orders.cancel("ORD-1")
    assert cancelled.status == "cancelled"
    assert inventory.available("MUG-01") == 4
    orders.cancel("ORD-1")
    assert inventory.available("MUG-01") == 4
