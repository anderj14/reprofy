"""Tiny public-facing report form for the Reprofy demo shop."""

from html import escape
import json
import os
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx

from shop.catalog import PRODUCTS, list_products
from shop.pricing import coupon_discount, tax_for_items


app = FastAPI(title="Reprofy Demo Shop", docs_url=None, redoc_url=None)

SHELL = """<style>
body{margin:0;font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#20241f;background:#f5f5ef}.top{padding:18px max(6vw,24px);background:white;border-bottom:1px solid #e4e5de;display:flex;justify-content:space-between;align-items:center}.brand{font-weight:750;letter-spacing:.03em;color:#20241f;text-decoration:none}.nav{display:flex;gap:22px}.nav a{color:#596255;text-decoration:none;font-size:14px}.nav a:hover{color:#315e3b}.wrap{max-width:1040px;margin:48px auto;padding:0 24px}h1{font-size:40px;letter-spacing:-.04em;margin:0 0 12px}.muted{color:#697065;line-height:1.6}.products{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:28px 0}.product,.card{background:#fff;border:1px solid #e1e3db;border-radius:14px;padding:20px}.emoji{font-size:30px}.product h3{margin:14px 0 5px}.price{font-weight:700}.button,button{display:inline-block;border:0;border-radius:8px;background:#315e3b;color:white;padding:11px 15px;font:inherit;font-weight:700;text-decoration:none;cursor:pointer}.button:hover,button:hover{background:#244b2e}.card{max-width:520px;box-shadow:0 12px 32px #1c28100a}.card h2{margin:0 0 7px;font-size:21px}.card p{color:#6b7167;font-size:14px;line-height:1.5}label{display:block;font-size:13px;font-weight:650;margin:16px 0 6px}input,textarea{width:100%;font:inherit;border:1px solid #d8dbd1;border-radius:8px;padding:11px;background:#fff}textarea{min-height:112px;resize:vertical}.thanks{padding:12px;background:#eff8ef;border-radius:8px;color:#24512d!important}.hint{font-size:12px!important}.pager{display:flex;gap:10px;align-items:center;margin-bottom:28px}.result{background:#fff9e9;border:1px solid #ead9a8;padding:16px;border-radius:10px;margin-top:16px}.trap{position:absolute;left:-10000px}@media(max-width:700px){.products{grid-template-columns:repeat(2,1fr)}.nav{gap:12px}h1{font-size:34px}}
</style><header class="top"><a class="brand" href="/">NORTHSTAR SUPPLY</a><nav class="nav"><a href="/">Home</a><a href="/catalog">Catalog</a><a href="/cart">Cart</a><a href="/report">Report an issue</a></nav></header>"""


def _cart(request: Request) -> dict[str, int]:
    try:
        value = json.loads(request.cookies.get("reprofy_cart", "{}"))
        valid_skus = {product.sku for product in PRODUCTS}
        return {sku: min(20, max(0, int(qty))) for sku, qty in value.items() if sku in valid_skus}
    except (ValueError, TypeError, AttributeError):
        return {}


def _cart_cookie(response, items: dict[str, int]) -> None:
    response.set_cookie("reprofy_cart", json.dumps(items), max_age=604800, httponly=True, samesite="lax")


def _cart_summary(request: Request) -> tuple[str, str]:
    items = _cart(request)
    products = {product.sku: product for product in PRODUCTS}
    subtotal = sum(products[sku].price * quantity for sku, quantity in items.items())
    code = request.cookies.get("reprofy_coupon", "")
    rate = 0.10 if code in {"SAVE20", "WELCOME10"} else 0.0
    discount = coupon_discount(subtotal, code)
    prices = [products[sku].price * (1 - rate) for sku, quantity in items.items() for _ in range(quantity)]
    tax = tax_for_items(prices, 0.0825)
    total = max(0.0, subtotal - discount) + tax
    rows = "".join(f'<div class="row"><span>{escape(products[sku].name)} × {quantity}</span><strong>${products[sku].price * quantity:.2f}</strong><form method="post" action="/cart/remove"><input type="hidden" name="sku" value="{escape(sku)}"><button>Remove</button></form></div>' for sku, quantity in items.items())
    totals = f'<div class="totals"><div><span>Subtotal</span><span>${subtotal:.2f}</span></div><div><span>Discount {f"({escape(code)})" if code else ""}</span><span>−${discount:.2f}</span></div><div><span>Estimated tax</span><span>${tax:.2f}</span></div><div><strong>Total</strong><strong>${total:.2f}</strong></div></div>'
    return rows, totals


@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Northstar Supply — Demo shop</title>
<script src="https://unpkg.com/htmx.org@2.0.4"></script>
<style>:root{background:#f5f5ef}*{box-sizing:border-box}</style></head><body>""" + SHELL + """
<main class="wrap"><p class="muted">EVERYDAY GOODS · DEMO STORE</p><h1>Good things for<br>the everyday.</h1><p class="muted">A sample storefront for exploring bugs from a customer's point of view. Browse the catalog, try checkout, and send the team a report.</p><p style="margin-top:28px"><a class="button" href="/catalog">Explore the catalog</a> <a class="button" style="background:#fff;color:#315e3b;border:1px solid #315e3b" href="/report">Report a problem</a></p><section class="products">""" + "".join(f'<article class="product"><span class="emoji">{icon}</span><h3>{p.name}</h3><span class="price">${p.price:.2f}</span></article>' for p, icon in zip(PRODUCTS[:3], ["☕", "👕", "👜"])) + """</section></main></body></html>"""


@app.get("/catalog", response_class=HTMLResponse)
async def catalog(request: Request, page: int = 1) -> str:
    page_size = 3
    products = list_products(page=page, page_size=page_size)
    cards = "".join(f'<article class="product"><span class="emoji">🛍️</span><h3>{p.name}</h3><span class="price">${p.price:.2f}</span><p class="muted">SKU {escape(p.sku)}</p><form method="post" action="/cart/add"><input type="hidden" name="sku" value="{escape(p.sku)}"><button type="submit">Add to cart</button></form></article>' for p in products)
    max_page = (len(PRODUCTS) + page_size - 1) // page_size
    pager = f'<a class="button" href="/catalog?page={page-1}" hx-get="/catalog?page={page-1}" hx-target="#catalog-content" hx-push-url="true">← Previous</a>' if page > 1 else ""
    pager += f'<span class="muted">Page {page} of {max_page}</span>'
    pager += f'<a class="button" href="/catalog?page={page+1}" hx-get="/catalog?page={page+1}" hx-target="#catalog-content" hx-push-url="true">Next →</a>' if page < max_page else ""
    fragment = f'<div class="products">{cards}</div><div class="pager">{pager}</div>'
    if request.headers.get("HX-Request") == "true":
        return fragment
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Catalog — Northstar Supply</title><script src="https://unpkg.com/htmx.org@2.0.4"></script></head><body>{SHELL}<main class="wrap"><p class="muted">THE SHOP</p><h1>Catalog</h1><p class="muted">A few useful things, made for everyday routines.</p><div id="catalog-content">{fragment}</div><section class="card"><h2>Checkout math demo</h2><p>Three items cost $0.10 each. At 8.25% tax, the receipt rounds each line item. Compare that with the current checkout calculation.</p><button hx-get="/checkout-preview" hx-target="#checkout-result">Preview checkout</button><div id="checkout-result"></div></section></main></body></html>'


@app.get("/cart", response_class=HTMLResponse)
async def cart_page(request: Request) -> str:
    rows, totals = _cart_summary(request)
    items = _cart(request)
    if not items:
        content = '<p class="muted">Your cart is empty.</p><a class="button" href="/catalog">Browse catalog</a>'
    else:
        code = request.cookies.get("reprofy_coupon", "")
        content = f'<div>{rows}</div><form method="post" action="/cart/coupon"><label for="coupon">Discount code</label><div class="coupon"><input id="coupon" name="code" value="{escape(code)}" placeholder="Try SAVE20 or WELCOME10"><button>Apply</button></div><p class="hint">Try SAVE20 for 20% off or WELCOME10 for 10% off.</p></form>{totals}<a class="button" href="/checkout">Continue to checkout</a>'
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Your cart — Northstar Supply</title></head><body>{SHELL}<main class="wrap"><p class="muted">YOUR ORDER</p><h1>Shopping cart</h1><section class="card">{content}</section></main></body></html>'


@app.post("/cart/add")
async def cart_add(request: Request):
    fields = parse_qs((await request.body()).decode())
    sku = fields.get("sku", [""])[0]
    if sku not in {product.sku for product in PRODUCTS}:
        return HTMLResponse("Unknown product", status_code=404)
    items = _cart(request)
    items[sku] = min(20, items.get(sku, 0) + 1)
    response = RedirectResponse("/cart", status_code=303)
    _cart_cookie(response, items)
    return response


@app.post("/cart/remove")
async def cart_remove(request: Request):
    fields = parse_qs((await request.body()).decode())
    items = _cart(request)
    items.pop(fields.get("sku", [""])[0], None)
    response = RedirectResponse("/cart", status_code=303)
    _cart_cookie(response, items)
    return response


@app.post("/cart/coupon")
async def cart_coupon(request: Request):
    fields = parse_qs((await request.body()).decode())
    code = fields.get("code", [""])[0].strip().upper()
    response = RedirectResponse("/cart", status_code=303)
    if code in {"SAVE20", "WELCOME10"}:
        response.set_cookie("reprofy_coupon", code, max_age=604800, httponly=True, samesite="lax")
    else:
        response.delete_cookie("reprofy_coupon")
    return response


@app.get("/checkout", response_class=HTMLResponse)
async def checkout_page(request: Request) -> str:
    rows, totals = _cart_summary(request)
    if not _cart(request):
        return '<!doctype html><html><body>' + SHELL + '<main class="wrap"><h1>Your cart is empty</h1><a class="button" href="/catalog">Browse catalog</a></main></body></html>'
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Checkout — Northstar Supply</title></head><body>{SHELL}<main class="wrap"><p class="muted">FINAL STEP</p><h1>Checkout</h1><section class="card"><h2>Order summary</h2>{rows}{totals}<form method="post" action="/checkout/complete"><button>Place demo order</button></form><p class="hint">Demo only. No payment information is collected.</p></section></main></body></html>'


@app.post("/checkout/complete", response_class=HTMLResponse)
async def checkout_complete(request: Request) -> HTMLResponse:
    if not _cart(request):
        return HTMLResponse("Your cart is empty.", status_code=400)
    response = HTMLResponse('<!doctype html><html><body>' + SHELL + '<main class="wrap"><h1>Order placed!</h1><p class="muted">This was a demo checkout. No payment was taken.</p><a class="button" href="/catalog">Continue shopping</a></main></body></html>')
    response.delete_cookie("reprofy_cart")
    response.delete_cookie("reprofy_coupon")
    return response


@app.get("/checkout-preview", response_class=HTMLResponse)
async def checkout_preview() -> str:
    receipt_tax = sum(round(price * 0.0825, 2) for price in [0.10, 0.10, 0.10])
    checkout_tax = tax_for_items([0.10, 0.10, 0.10], 0.0825)
    return f'<div class="result"><strong>Receipt tax:</strong> ${receipt_tax:.2f}<br><strong>Checkout tax:</strong> ${checkout_tax:.2f}<p>Expected: both totals match.</p></div>'


@app.get("/report", response_class=HTMLResponse)
async def report_page() -> str:
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Report an issue — Northstar Supply</title><script src="https://unpkg.com/htmx.org@2.0.4"></script></head><body>''' + SHELL + '''<main class="wrap"><p class="muted">CUSTOMER SUPPORT</p><section class="card"><h1 style="font-size:30px">Something not working?</h1><p>Tell us what happened. The team will review your report.</p><form hx-post="/report" hx-target="#report-result" hx-swap="innerHTML"><label for="report">What went wrong?</label><textarea id="report" name="text" maxlength="16000" required placeholder="Describe what you were trying to do and what happened..."></textarea><label for="expected">What did you expect? <span class="muted">Optional</span></label><input id="expected" name="expected" maxlength="8000" placeholder="What should have happened?"><p class="hint">You can leave this blank. Reprofy will infer the expected behavior from your report and the project, or ask the team if it’s unclear.</p><label for="email">Email <span class="muted">Optional</span></label><input id="email" name="email" type="email" maxlength="254" placeholder="Only if the team needs to follow up"><div class="trap"><label>Leave this empty<input name="website" tabindex="-1" autocomplete="off"></label></div><button type="submit">Send report</button></form><div id="report-result" aria-live="polite"></div><p class="hint">Please don’t include passwords or payment details.</p></section></main></body></html>'''


@app.post("/report", response_class=HTMLResponse)
async def submit_report(request: Request) -> HTMLResponse:
    body = await request.body()
    if len(body) > 40_000:
        return HTMLResponse("<p role='alert'>Report is too large. Please shorten it.</p>", status_code=413)
    fields = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    if fields.get("website", [""])[0].strip():
        return HTMLResponse("<p class='thanks'>Thanks. Your report was received.</p>")
    try:
        payload = {
            "text": fields.get("text", [""])[0],
            "expected": fields.get("expected", [""])[0] or None,
            "context": (f"Customer contact email: {fields['email'][0].strip()}" if fields.get("email", [""])[0].strip() else None),
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            result = await client.post(
                f"{os.environ.get('REPROFY_API_URL', 'http://127.0.0.1:8766').rstrip('/')}/v1/reports",
                json=payload,
                headers={"Authorization": f"Bearer {os.environ.get('REPROFY_API_TOKEN', '')}"},
            )
        result.raise_for_status()
        bug_id = result.json()["id"]
    except (httpx.HTTPError, KeyError, ValueError, UnicodeError):
        return HTMLResponse("<p role='alert'>We couldn’t save that report. Please check the description and try again.</p>", status_code=400)
    return HTMLResponse(f"<p class='thanks'>Thanks — your report is with the team. Reference: <strong>{escape(bug_id)}</strong>.</p>")
