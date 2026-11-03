# Reprofy demo shop

A small, synthetic Python shop created during the Meta Global AI Developer Hackathon to demonstrate Reprofy. It has no real customers, orders, or client data. Its original baseline tests covered ordinary behavior; approved agent fixes add regression tests as the example evolves.

## Run the baseline suite

From this directory:

```bash
python -m pytest
```

## Seeded bug reports

These are natural-language reports a shop owner could submit to Reprofy. They describe the original seeded behavior. Coupon validation, pagination, case-insensitive email uniqueness, and cancellation/stock have been fixed through reviewed Reprofy proposals, with generated regression tests under `tests/repro/`. Only tax rounding remains seeded. Engine and provider evaluation use a separate frozen buggy fixture, so they do not depend on this example remaining broken.

1. **Coupon total (fixed):** “I used a promo meant to cover the whole cart and checkout showed a negative amount after tax.” The original implementation accepted percentages above 100; it now rejects them with ValueError.
2. **Pagination (fixed):** “On the last page of products I see one item that was already on the page before.” The approved patch removed the final-page index adjustment.
3. **Tax rounding:** “I bought three $0.10 items with 8.25% tax. Our receipt rounds each line's tax, but checkout is one cent lower.”
4. **Duplicate account (fixed):** “I already signed up with `ana@example.test`, but `ANA@example.test` let me create another account.” Registration now compares and stores a lowercase uniqueness key while preserving the normalized return value.
5. **Canceled order / stock (fixed, Spanish):** “Cancelé un pedido de las últimas unidades y después el producto siguió apareciendo como agotado.” The approved patch restores the reserved quantity exactly once; the generated regression test cancels the same order twice to protect against duplicate restoration.

## Fixture limits

**DECIDED (November 2):** coupon percentages above 100 must be rejected with `ValueError`. Exactly 100 is valid. The agent-generated and human-approved correction now enforces this rule. Capping an oversized discount and returning zero would not satisfy the requirement. Negative percentages and other input-validation policies have not been decided here.

The shop is an in-memory demonstration, not a production commerce system. Prices use floats to make the rounding report concrete. Generated tests now guard the applied corrections. To repeat a solved case, use a clean copy of the frozen buggy fixture instead of changing the live corrected example.
