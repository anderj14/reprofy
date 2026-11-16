from fastapi.testclient import TestClient
from shop.catalog import PRODUCTS
from demo_app import app

def test_magico150_coupon_rejected_at_checkout():
    pen = next((p for p in PRODUCTS if abs(p.price - 10.0) < 0.001), PRODUCTS[0])
    client = TestClient(app)
    client.post("/cart/add", data={"sku": pen.sku})
    client.post("/cart/coupon", data={"code": "MAGICO150"})
    resp = client.post("/checkout/complete")
    assert resp.status_code in (400, 422)
    assert "coupon" in resp.text.lower() or "discount" in resp.text.lower()
