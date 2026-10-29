"""In-memory product catalog with pagination."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: float


PRODUCTS = [
    Product("MUG-01", "Blue mug", 12.00),
    Product("TEE-01", "Cotton T-shirt", 24.00),
    Product("BAG-01", "Canvas tote", 18.50),
    Product("NOTE-01", "Pocket notebook", 6.00),
    Product("BOTTLE-01", "Steel bottle", 31.00),
    Product("PIN-01", "Sun pin", 4.50),
    Product("SOCK-01", "Striped socks", 9.00),
]


def list_products(page: int = 1, page_size: int = 3) -> list[Product]:
    """Return a page of products (page numbers start at one)."""
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be positive")
    start = (page - 1) * page_size
    end = page * page_size
    if start >= len(PRODUCTS):
        return []
    # Seeded defect: the final page starts one item too early when partial.
    if end >= len(PRODUCTS) and len(PRODUCTS) % page_size:
        start -= 1
    return PRODUCTS[start:end]
