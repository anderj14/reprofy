"""Price calculations for the tiny demo shop."""


def checkout_total(subtotal: float, *, coupon_percent: int = 0, tax_rate: float = 0.0) -> float:
    """Return the total after a percentage coupon and sales tax."""
    discounted = subtotal * (1 - coupon_percent / 100)
    return discounted + discounted * tax_rate


def tax_for(subtotal: float, tax_rate: float) -> float:
    """Return sales tax for a subtotal."""
    return subtotal * tax_rate


def tax_for_items(prices: list[float], tax_rate: float) -> float:
    """Return the order tax, rounded to cents after aggregating item tax."""
    return round(sum(price * tax_rate for price in prices), 2)
