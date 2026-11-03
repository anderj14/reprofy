# Reprofy demo shop

A small, synthetic Python shop created during the Meta Global AI Developer Hackathon to demonstrate Reprofy. It has no real customers, orders, or client data. Its intentionally seeded defects are inputs for the agent demo; the baseline test suite covers ordinary behavior and passes while leaving those report scenarios uncovered.

## Run the baseline suite

From this directory:

```bash
python -m pytest
```

## Seeded bug reports

These are natural-language reports a shop owner could submit to Reprofy. They describe the original seeded behavior. The coupon issue has now been fixed through an approved Reprofy apply; its generated regression test is under tests/repro/. The other scenarios remain seeded for subsequent demonstrations. Engine tests use a separate frozen buggy fixture, so they do not depend on this example remaining broken.

1. **Coupon total:** “I used a promo meant to cover the whole cart and checkout showed a negative amount after tax.” The demo can reproduce this with a misconfigured discount percentage above 100, which the current calculation accepts.
2. **Pagination:** “On the last page of products I see one item that was already on the page before.”
3. **Tax rounding:** “I bought three $0.10 items with 8.25% tax. Our receipt rounds each line's tax, but checkout is one cent lower.”
4. **Duplicate account:** “I already signed up with `ana@example.test`, but `ANA@example.test` let me create another account.”
5. **Canceled order / stock (Spanish):** “Cancelé un pedido de las últimas unidades y después el producto siguió apareciendo como agotado.”

The fifth report crosses the order lifecycle and inventory behavior. Reprofy may fail to reproduce or fix it within its retry budget; that is an intentional part of the demo, not a claim that the agent always succeeds.

## Fixture limits

**DECIDED (November 2):** coupon percentages above 100 must be rejected with `ValueError`. Exactly 100 is valid. The seeded implementation currently lacks this validation; the agent must generate and verify its repair. Capping an oversized discount and returning zero would not satisfy this requirement. Negative percentages and other input-validation policies have not been decided here.

The shop is an in-memory demonstration, not a production commerce system. Prices use floats to make the rounding report concrete. The tests intentionally do not assert the seeded bug scenarios, so the baseline suite remains green until Reprofy writes a reproduction test.
