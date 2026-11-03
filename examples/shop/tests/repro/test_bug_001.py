from shop.catalog import PRODUCTS, list_products


def test_pagination_visits_each_product_exactly_once():
    page_size = 3
    seen = []
    page = 1
    while True:
        batch = list_products(page=page, page_size=page_size)
        if not batch:
            break
        seen.extend(p.sku for p in batch)
        page += 1
        assert page <= 10
    assert seen == [p.sku for p in PRODUCTS]
