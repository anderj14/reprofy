# Reprofy demo shop

A small, synthetic Python shop created during the Meta Global AI Developer Hackathon to demonstrate Reprofy. It has no real customers, orders, or client data. Its intentionally seeded defects are inputs for the agent demo; the baseline test suite covers ordinary behavior and passes while leaving those report scenarios uncovered.

## Run the baseline suite

From this directory:

```bash
python -m pytest
```

## Seeded bug reports

These are natural-language reports a shop owner could submit to Reprofy. The app currently contains the corresponding behavior so Reprofy can attempt to generate a failing reproduction and verified patch.

1. **Coupon total:** “I used a promo meant to cover the whole cart and checkout showed a negative amount after tax.” The demo can reproduce this with a misconfigured discount percentage above 100, which the current calculation accepts.
2. **Pagination:** “On the last page of products I see one item that was already on the page before.”
3. **Tax rounding:** “I bought three $0.10 items with 8.25% tax. Our receipt rounds each line's tax, but checkout is one cent lower.”
4. **Duplicate account:** “I already signed up with `ana@example.test`, but `ANA@example.test` let me create another account.”
5. **Canceled order / stock (Spanish):** “Cancelé un pedido de las últimas unidades y después el producto siguió apareciendo como agotado.”

The fifth report crosses the order lifecycle and inventory behavior. Reprofy may fail to reproduce or fix it within its retry budget; that is an intentional part of the demo, not a claim that the agent always succeeds.

## Fixture limits

The shop is an in-memory demonstration, not a production commerce system. Prices use floats to make the rounding report concrete. The tests intentionally do not assert the seeded bug scenarios, so the baseline suite remains green until Reprofy writes a reproduction test.
