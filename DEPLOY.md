# Irondale QR drink order — Glenn deploy

Public beta, Irondale storefront only. Phone web app on the existing `bakery-drinks` Cloud Run service. No custom domain, no Cloud SQL, no Twilio, no Trussville.

Customer path: scan QR → `/order` → drinks + modifiers → Square hosted Checkout → `/order/status` (Paid / Making / Ready). Closing the tab still leaves the ticket on the public drink board (Square payment → existing getorders live pull).

## Catalog approach (this beta)

**Server-side curated list** in `app/order.py` (`DRINKS`): the six drinks from the approved mock (Viet iced coffee, hot coffee, Biscoff coffee, matcha latte, milk tea, fruit tea) with milk / sweet / flavor / boba / extra-shot modifiers.

Not a live Square Catalog API sync. Line item **names match POS** (`Vietnamese Coffee`, `Biscoff Coffee`, `Matcha Latte`, `Milk Tea`, `Fruit Tea`, `Hot Coffee`) so `is_drink` and getorders still treat them as drinks. Prices are cents in that file (edit there; they are not secrets).

Replace SVG placeholders with photos by dropping `static/order/drinks/<id>.jpg` (or `.png` / `.webp`) next to the svg. `photo_url()` prefers a real photo. Ids: `viet-iced-coffee`, `hot-coffee`, `biscoff-coffee`, `matcha-latte`, `milk-tea`, `fruit-tea`. `Dockerfile.drinks` currently dockerignores `*.png` at the repo root — keep photos as `.jpg`/`.webp` or add `!static/order/**/*.png` if you must use png.

## Env vars (drinks Cloud Run)

Same secret pattern as ingest. **Never put a Square token in client JS, the image, or git.**

| Name | Required | What |
| --- | --- | --- |
| `SQUARE_ACCESS_TOKEN` | yes for live pay | Secret Manager. Already used by `POST /internal/ingest`. |
| `SQUARE_LOCATION_ID_IRONDALE` | recommended | Irondale location id. |
| `LOCATION_ID` or `SQUARE_LOCATION_ID` | fallback | Used if the Irondale-specific var is empty. |
| `SQUARE_API_BASE` | no | Default `https://connect.squareup.com`. |
| `ORDER_PUBLIC_URL` | recommended | Public origin of this Cloud Run service, no trailing slash, e.g. `https://bakery-drinks-xxxxx-ue.a.run.app`. Used as Square `redirect_url` after pay. |
| `GETORDERS_URL` | already set | Board live pull. Unchanged. |
| `INGEST_KEY` | already set | Ingest hook only. Not used by `/order`. |
| `BAKERY_SERVICE` | `drinks` | Baked in `Dockerfile.drinks`. |

If all location vars are empty, code falls back to Irondale `L4CK6YWGT5XQX` (same default as laptop ingest). Shop 2 / Trussville is out of scope — do not point these vars at another location.

### Token scopes

CreatePaymentLink needs **ORDERS_READ, ORDERS_WRITE, PAYMENTS_WRITE**. If the existing ingest token is payments-read only, minting checkout links will 502 until Glenn adds those scopes (or a separate drinks-order secret wired to the same env name).

Ready status: kitchen tap-to-clear on `/board` also tries `UpdateOrder` fulfillment → `PREPARED` when a token is present. The phone poll then shows **Ready** / “Head to the pickup counter” / **Go to pickup**. If Square rejects the update, the board still hides the ticket locally; the phone stays on Making until fulfillment is prepared in Square POS.

Optional phone on the review screen is passed to Square pickup recipient / prepopulate. Square-native pickup texts only — no Twilio.

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

1. Open `/order` on a phone (or desktop at phone width). Menu lists Coffee + Tea; not a photo dump.
2. Tap Viet iced coffee. Set milk / sweet / add-ons / qty. **Add to order**.
3. Sticky **Review order · N drinks**. Name + to-go (or for here) on the same review screen. Optional phone.
4. **Pay now** → server `POST /order/api/checkout` → Square hosted Checkout (Apple Pay / Google Pay / card). Confirm the browser never receives `SQUARE_ACCESS_TOKEN`.
5. After pay, Square redirects to `/order/status`. Labels **Paid** and **Making** (not color-only). Copy is “We're making it” / “We'll text when it's at pickup” if a phone was given.
6. Kitchen `/board` shows the new Square order (getorders, same as POS drinks). No new ticket database.
7. Tap the ticket off the board (or mark pickup ready in Square). Phone status becomes **Ready**, “Head to the pickup counter”, button **Go to pickup** — not “grab it” or “name on the cup”.
8. Close the customer tab after pay: ticket still on the board.

## Out of scope (do not add here)

Trussville / shop 2, iPad kiosk hardware, Twilio SMS, custom domain sunshinebakeshop.com, bakery-desk redesign.
