# Irondale QR drink order — Glenn deploy

Public beta, Irondale storefront only. Phone web app on the existing `bakery-drinks` Cloud Run service. No custom domain, no Cloud SQL, no Trussville. Ready SMS is env-gated Twilio on this same service (Square cannot text Orders API / payment-link pickups).

Customer path: scan QR → `/order` → drinks + modifiers → Square hosted Checkout → `/order/status` (Paid / Making / Ready). Closing the tab still leaves the ticket on the public drink board (Square payment → existing getorders live pull).

## Catalog approach (Irondale QR)

**Live Square Catalog** is the source of truth for `/order`. `GET /order/api/menu` calls Catalog `SearchCatalogItems` (Drink category ids + Irondale `enabled_location_ids`), then `BatchRetrieveCatalogObjects` for each item’s modifier lists and images. Irondale drinks are Square `FOOD_AND_BEV` items — `map_catalog_items` keeps that product type (plus `REGULAR` / `RETAIL_ITEM` / `BEVERAGE`). Pastry stays out via the Drink-category rule.

Every POS option on that drink is returned (milk, sweet, sauce, flavor, matcha option, boba, add-ons, …) — not the old six-drink ad-hoc list in `app/order.py`. Pastry (Biscoff Roll) is excluded by the same Drink-category rule as the board (`app/drinks.py`). Shop 2 / Trussville items that are not present at Irondale are excluded.

Checkout `CreatePaymentLink` line items send **catalog variation `catalog_object_id`** and **modifier `catalog_object_id`s** (not fake ad-hoc names). Square prices and names the order like POS, so getorders / `is_drink` still put paid drinks on the board.

Laptop with **no** `SQUARE_ACCESS_TOKEN` still serves the old demo six-drink list so `/order` can be clicked without Square. Cloud Run `bakery-drinks` has a token and never uses that demo list.

Replace SVG placeholders with photos by dropping `static/order/drinks/<stem>.jpg` (or `.png` / `.webp`) next to the svg. Catalog item images (https Square CDN URLs) win when present. Stems used as fallback: `viet-iced-coffee`, `hot-coffee`, `biscoff-coffee`, `matcha-latte`, `milk-tea`, `fruit-tea`. `Dockerfile.drinks` currently dockerignores `*.png` at the repo root — keep photos as `.jpg`/`.webp` or add `!static/order/**/*.png` if you must use png.

## Env vars (drinks Cloud Run)

Same secret pattern as ingest. **Never put a Square token in client JS, the image, or git.**

| Name | Required | What |
| --- | --- | --- |
| `SQUARE_ACCESS_TOKEN` | yes for live pay | Secret Manager. Already used by `POST /internal/ingest`. |
| `SQUARE_LOCATION_ID_IRONDALE` | recommended | Irondale location id. |
| `LOCATION_ID` or `SQUARE_LOCATION_ID` | fallback | Used if the Irondale-specific var is empty. |
| `SQUARE_API_BASE` | no | Default `https://connect.squareup.com`. |
| `CATALOG_CACHE_SECONDS` | no | How long the drinks service caches Catalog (default `90`). Set `0` to fetch every menu load. |
| `ORDER_PUBLIC_URL` | recommended | Public origin of this Cloud Run service, no trailing slash, e.g. `https://bakery-drinks-xxxxx-ue.a.run.app`. Used as Square `redirect_url` after pay. |
| `GETORDERS_URL` | already set | Board live pull. Unchanged. |
| `INGEST_KEY` | already set | Ingest hook only. Not used by `/order`. |
| `BAKERY_SERVICE` | `drinks` | Baked in `Dockerfile.drinks`. |
| `TWILIO_ACCOUNT_SID` | for ready SMS | Secret Manager. Twilio Account SID. |
| `TWILIO_AUTH_TOKEN` | for ready SMS | Secret Manager. Never git, never `--set-env-vars`. |
| `TWILIO_FROM_NUMBER` | for ready SMS | E.164 from-number, e.g. `+12055550100`. |
| `TWILIO_API_BASE` | no | Default `https://api.twilio.com`. Tests only. |

If all location vars are empty, code falls back to Irondale `L4CK6YWGT5XQX` (same default as laptop ingest). Shop 2 / Trussville is out of scope — do not point these vars at another location.

### Ready SMS (Twilio)

Square **does not** send pickup-ready texts for CreatePaymentLink / Orders API tickets. Passing the phone as fulfillment `recipient.phone_number` or Checkout `pre_populated_data` does not trigger Square Online / Restaurants order-ready SMS. That is why Ronald’s live beta order got no text.

When all three Twilio vars are set on `bakery-drinks`, `/order` shows “Phone for a ready text (optional)”. One short SMS fires the first time that Square order is **Ready** (fulfillment `PREPARED` / `COMPLETED`):

- Kitchen tap-to-clear on `/board` (`UpdateOrder` → `PREPARED`)
- Customer `/order/status` poll after POS marks pickup ready

Dedup: in-process set plus Square order metadata `qr_ready_sms=sent`. Status polls every 4s will not re-text.

If any Twilio var is missing, the phone field is hidden and the Making copy is only “We'll have it at pickup.” — no promised text.

Twilio is **not** billed to the bakery GCP project (stays off the ~$15/mo Cloud Run cap). Low-volume US SMS is typically a number (~$1.15/mo) plus ~$0.008/text. Use a verified US number (toll-free or 10DLC). No new Cloud Run service.

```bash
# after secrets exist in Secret Manager
gcloud run services update bakery-drinks \
  --region REGION \
  --update-secrets TWILIO_ACCOUNT_SID=twilio-account-sid:latest,TWILIO_AUTH_TOKEN=twilio-auth-token:latest,TWILIO_FROM_NUMBER=twilio-from-number:latest
```

Do not put Twilio tokens in the image or git.

### Token scopes

CreatePaymentLink needs **ORDERS_READ, ORDERS_WRITE, PAYMENTS_WRITE**.

Live menu needs **ITEMS_READ** (Catalog `SearchCatalogItems` / `BatchRetrieve`). If the existing ingest token is payments-read only, `/order/api/menu` returns an empty menu with `catalog_error` until Glenn adds **ITEMS_READ** (or a separate drinks-order secret wired to the same `SQUARE_ACCESS_TOKEN` env name). No new Cloud Run env name is required.

If the ingest token is payments-read only, minting checkout links will 502 until Glenn adds the Orders/Payments write scopes.

Ready status: kitchen tap-to-clear on `/board` also tries `UpdateOrder` fulfillment → `PREPARED` when a token is present. The phone poll then shows **Ready** / “Head to the pickup counter” / **Go to pickup**. If Square rejects the update, the board still hides the ticket locally; the phone stays on Making until fulfillment is prepared in Square POS.

Optional phone (only when Twilio is configured) is still stored on the Square pickup recipient so the drinks service can text on Ready. Line-item `note` is the customer name only — pickup “to go” / “for here” stays on fulfillment `pickup_details.note`, not duplicated onto every drink.

## Deploy Cloud Run (`bakery-drinks`)

Same image as today. Scale-to-zero is fine (`min-instances 0`). No extra CPU/memory for this path.

```bash
# from repo root, after this branch is merged or from the PR commit
gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT/bakery/bakery-drinks
gcloud run deploy bakery-drinks \
  --image REGION-docker.pkg.dev/PROJECT/bakery/bakery-drinks \
  --region REGION \
  --min-instances 0 \
  --set-env-vars BAKERY_SERVICE=drinks,ORDER_PUBLIC_URL=https://bakery-drinks-xxxxx-ue.a.run.app,SQUARE_LOCATION_ID_IRONDALE=L4CK6YWGT5XQX
```

Keep `SQUARE_ACCESS_TOKEN` and `INGEST_KEY` as Secret Manager mounts (do not `--set-env-vars` the token). Redeploy is enough; no new service.

Confirm:

- `GET https://<drinks-url>/health` → `{"ok": true, "service": "drinks"}`
- `GET https://<drinks-url>/order` → Sunshine's drinks menu
- `GET https://<drinks-url>/order/api/menu` → `source=square` and **non-empty** `drinks` (expect ~9 Irondale drinks with full modifier lists; empty `drinks` with no `catalog_error` was the `FOOD_AND_BEV` filter bug)
- `GET https://<drinks-url>/loyalty` → 404

## Print a QR for the Irondale table tent

Beta URL (no custom domain required):

```
https://<bakery-drinks-url>/order
```

`/qr` redirects to the same page.

Print options:

1. Open `https://<bakery-drinks-url>/order/tent` on a laptop → Print. Matches the “Scan to order drinks / Pay on your phone · no app” tent.
2. Any QR generator (phone camera must open the **https Cloud Run `/order` URL**, not sunshinebakeshop.com until DNS exists).

## Laptop / local

`./scripts/run.sh` then http://127.0.0.1:8000/order

If `SQUARE_ACCESS_TOKEN` is unset on laptop, checkout is a **demo** that skips Square and opens Making/Ready (`oid=demo` / `oid=demo-ready`). Cloud Run `BAKERY_SERVICE=drinks` without a token does **not** demo-charge; pay returns 503.

## Manual test checklist

1. Open `/order` on a phone (or desktop at phone width). Menu lists Coffee + Tea (and More if Catalog has other Drink items); not a photo dump. Drinks and every modifier come from Square Catalog, not a hardcoded list.
2. Tap a drink. The **options / confirm screen always opens** before the cart — every Square modifier list for that drink (milk, sweet, sauce, flavor, matcha option, boba, …), plus qty. If Catalog has **zero** modifiers, the same step still shows the drink **name**, price, qty, and **Add to order**. There is no menu `+` that skips into the cart. Sticky **Review order** only after they confirm.
3. Sticky **Review order · N drinks**. Name + to-go (or for here) on the same review screen. Optional phone only if Twilio is configured on the drinks service.
4. **Pay now** → server `POST /order/api/checkout` → Square hosted Checkout (Apple Pay / Google Pay / card). Confirm the browser never receives `SQUARE_ACCESS_TOKEN`.
5. After pay, Square redirects to `/order/status`. Labels **Paid** and **Making** (not color-only). Copy is “We're making it” / “We'll text when it's at pickup” if a phone was given **and** Twilio is configured. Otherwise “We'll have it at pickup.”
6. Kitchen `/board` shows the new Square order (getorders, same as POS drinks). No new ticket database.
7. Tap the ticket off the board (or mark pickup ready in Square). Phone status becomes **Ready**, “Head to the pickup counter”, button **Go to pickup** — not “grab it” or “name on the cup”. Ready hero is the Sunshine's Bakery logo (`/static/order/logo.svg`), not a drink / coffee placeholder.
8. If a phone was entered and Twilio is set: one SMS (“your drink is ready. Head to the pickup counter.”). Close the customer tab after pay: ticket still on the board; kitchen tap still sends the text.
9. Square drink line notes are the pickup **name only**.

## Out of scope (do not add here)

Trussville / shop 2, iPad kiosk hardware, custom domain sunshinebakeshop.com, bakery-desk redesign. Do not add a second Cloud Run service for SMS.
