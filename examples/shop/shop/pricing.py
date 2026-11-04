"""Price calculations for the tiny demo shop."""


def checkout_total(subtotal: float, *, coupon_percent: int = 0, tax_rate: float = 0.0) -> float:
    """Return the total after a percentage coupon and sales tax."""
    if coupon_percent > 100:
        raise ValueError("coupon_percent must not exceed 100")
    discounted = subtotal * (1 - coupon_percent / 100)
    return discounted + discounted * tax_rate


def tax_for(subtotal: float, tax_rate: float) -> float:
    """Return sales tax for a subtotal."""
    return subtotal * tax_rate


def tax_for_items(prices: list[float], tax_rate: float) -> float:
    """Return the order tax, rounded to cents per item then summed."""
    return round(sum(round(price * tax_rate, 2) for price in prices), 2)


def coupon_discount(subtotal: float, code: str) -> float:
    """Return the discount for a demo coupon code.

    SAVE20 gives a 20% discount. WELCOME10 correctly returns 10%.
    """
    rates = {"SAVE20": 0.20, "WELCOME10": 0.10}
    return round(subtotal * rates.get(code.upper(), 0.0), 2)
