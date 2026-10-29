"""Inventory counts for the synthetic storefront."""


class Inventory:
    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = dict(counts)

    def available(self, sku: str) -> int:
        return self._counts.get(sku, 0)

    def take(self, sku: str, quantity: int) -> None:
        current = self.available(sku)
        if quantity < 1 or current < quantity:
            raise ValueError(f"not enough stock for {sku}")
        self._counts[sku] = current - quantity

    def put_back(self, sku: str, quantity: int) -> None:
        if quantity < 1:
            raise ValueError("quantity must be positive")
        self._counts[sku] = self.available(sku) + quantity
