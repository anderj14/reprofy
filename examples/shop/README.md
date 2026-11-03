# Reprofy demo shop

A small, synthetic Python shop created during the Meta Global AI Developer Hackathon to demonstrate Reprofy. It has no real customers, orders, or client data. Its original baseline tests covered ordinary behavior; approved agent fixes add regression tests as the example evolves.

## Run the full local demo with Docker

Docker Compose runs two services: the storefront and a separate Reprofy intake API. The form sends reports to that API using a local bearer token; the API writes to the same project folder mounted for the host CLI. The Meta/Baseten model key stays on the developer's machine and is used by the CLI, never by the browser or demo containers.

From the Reprofy repository root, create a local intake token and start both services:

```bash
cp .env.docker.example .env.docker
openssl rand -hex 32
# Paste the generated value into .env.docker as REPROFY_INGEST_TOKEN
docker compose --env-file .env.docker up --build
```

Open `http://127.0.0.1:8000`, browse the catalog, try the cart and checkout, then submit a report. In another terminal, run `reprofy list --project examples/shop`; the new report is already available to the CLI. To generate a patch, configure the Meta key in the host's `.env` and run `reprofy fix BUG-ID --project examples/shop --env-file .env`. Stop the demo with `docker compose --env-file .env.docker down`.

## Seeded bugs to report separately

The demo has one remaining seeded issue in the configured `shop/` source tree. The `SAVE20` coupon bug was fixed through a reviewed Reprofy proposal and is covered by `tests/repro/test_bug_004.py`:

1. **Tax rounding:** use the catalog’s checkout preview. Report: “The receipt shows $0.03 tax for three $0.10 items, but checkout charges $0.02.” Expected: “Tax should be rounded per item to cents before summing, so both values are $0.03.”

Submit and fix one report at a time. The optional email is kept as report context so the team can follow up; the form warns visitors not to submit passwords or payment details. Checkout does not collect payment details.

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
