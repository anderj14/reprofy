"""Order lifecycle spanning order state and inventory."""

from dataclasses import dataclass

from .inventory import Inventory


@dataclass
class Order:
    order_id: str
    sku: str
    quantity: int
    status: str = "created"


class OrderService:
    def __init__(self, inventory: Inventory) -> None:
        self.inventory = inventory
        self.orders: dict[str, Order] = {}

    def create(self, order_id: str, sku: str, quantity: int) -> Order:
        if order_id in self.orders:
            raise ValueError(f"duplicate order {order_id}")
        self.inventory.take(sku, quantity)
        order = Order(order_id, sku, quantity)
        self.orders[order_id] = order
        return order

    def cancel(self, order_id: str) -> Order:
        order = self.orders[order_id]
        if order.status == "cancelled":
            return order
        order.status = "cancelled"
        self.inventory.put_back(order.sku, order.quantity)
        return order
