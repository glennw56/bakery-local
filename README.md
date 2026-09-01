# Bakery Local

## What this is

Local-first shop notebook, reports desk, drink kitchen display, and loyalty member book. FastAPI + Jinja + HTMX + one SQLite file (`data/app.db`, WAL). No cloud, no Postgres.

**Not in git:** Square tokens / API keys, private SSH keys, the live sqlite file, and the loyalty dump (`data/square_loyalty.json`). `data/*` is gitignored except `data/.gitkeep`. Copy `.env.example` to `.env` if you want local HOST/PORT; laptop `.env` holds `SQUARE_ACCESS_TOKEN`, never git.

## Quick start

From the repo root (Python 3.12+):

```bash
./scripts/setup.sh
./scripts/run.sh
```

Open http://127.0.0.1:8000

`setup.sh` creates `.venv`, installs `requirements.txt`, and runs `scripts.seed_demo` only if the db file is missing. `run.sh` starts uvicorn (`HOST=127.0.0.1`, `PORT=8000`, `RELOAD=1` by default). Same thing via `make setup` then `make run`.

`seed_demo` writes reports tables (if empty), always refreshes ~8 drink tickets into the last 15 minutes so `/board` looks alive, loads loyalty, and loads `scripts/sample_weekend.json` into `weekend_days` / `weekend_items` if those tables are empty. If `data/square_loyalty.json` exists it upserts that dump; otherwise it seeds ~40 demo members. Loyalty seed is skipped when `loyalty_members` already has rows. Reports, tickets, and weekend rows are never wiped by a loyalty import. Weekend seed does not smash the demo reports matrix.

htmx 2.x is vendored at `static/htmx.min.js` (offline).

## Screens / tools inventory

| Route | What |
| --- | --- |
| `GET /` | Admin dashboard — stat tiles + links |
| `GET /reports` | Weekend vs weekday, top items, drink × modifier, merch, site landmines |
| `GET /weekend` | Marketing scorecard: this Saturday vs last (tickets, mix, top 10, do-not-feature) |
| `GET /weekend.csv` | Same comparison, UTF-8 CSV (`metric`, `last_sat`, `this_sat`, `delta`) |
| `GET /board` | Kitchen drink display (last 15 min, newest first; tap to clear) |
| `GET /board/tickets` | Ticket-list fragment; HTMX polls every 10s |
| `DELETE /board/tickets/{id}` | Tap-to-clear: drink is made, ticket drops off |
| `POST /board/demo-tick` | Dev-only: insert a random demo drink (no Square) |
| `GET /loyalty` | Member book: search, segment chips, hometown rollup, dense table |
| `GET /loyalty.csv` | Same filters as the list, UTF-8 CSV attachment |
| `GET /loyalty/{id}` | One-member dossier (balance, marketing, points ledger) |
| `GET /notes` | Scratch pad |
| `POST /notes` | Add a note (HTMX inserts a row) |
| `DELETE /notes/{id}` | Remove a note |

## Shop laptop + tablet drink board

1. On the shop laptop, from this folder, bind every interface:

   ```bash
   HOST=0.0.0.0 ./scripts/run.sh
   ```

   Or `make board`. (`RELOAD=0 HOST=0.0.0.0 ./scripts/run.sh` if you do not want `--reload`.)

2. Laptop LAN address: Wi-Fi / Settings, or `ip addr` / `ipconfig`. Example: `192.168.1.20`.
3. On the tablet, open `http://192.168.1.20:8000/board`.
4. Add to Home Screen if you want. Keep the tablet plugged in, brightness up, auto-lock off.

`?minutes=15` is the default window. Newest tickets sit first. Tap a ticket when the drink is done and it drops off. The list refreshes every 10 seconds.


## Live drink ingest

Paid Square drinks land in `drink_tickets` on the shop laptop. POS does not fire `order.created` webhooks — this process lists COMPLETED payments then retrieves each order. Unpaid drinks never appear.

Run `make ingest` or `./.venv/bin/python -m scripts.ingest_drinks --watch` on the laptop WHILE the board is serving. Tablet still only hits `/board`. No webhook. No GCP.

`make ingest-watch` loops every 25s. Tokens stay in laptop `.env`, never git. The HTMX 10s poll still reads local sqlite only.

## Loyalty + CSV + hometown

`GET /loyalty` is the Marketing/Books desk. Filter by name/phone/email, segment (active / lapsed / ready to redeem / never purchased / email / phone-only / local 205 / Alabama / out of state / unknown phone), and sort.

Hometown rollup is NANP **area-code** geography only (205/659 = Birmingham / Irondale local). No street lookup, no reverse-phone API, no skip-tracing.

`GET /loyalty.csv` downloads the current filter as UTF-8 CSV (`area_code`, `metro`, `state` included). `GET /loyalty/{id}` is the dossier.

Square facts modeled here: 1 point per $1 before tax; 100 pts = free Fruit Tea; 200 pts = $10 off entire sale. LoyaltyAccount has phone + points, not email or favorite item — those fields are denormalized onto the member row so the list stays useful when order joins are sparse.

## Recreate from GitHub

Repo is meant to be **private**: https://github.com/glennw56/bakery-local

```bash
git clone https://github.com/glennw56/bakery-local.git
cd bakery-local
./scripts/setup.sh
./scripts/run.sh
```

Then http://127.0.0.1:8000. You get a demo sqlite file, not the live shop db and not the loyalty dump. Copy those onto the laptop separately (USB, scp) — they are not in git. Preferred path on the shop laptop is venv (`setup.sh` / `run.sh`). Docker is optional; see below.

## Backup / restore the sqlite file

```bash
./scripts/backup.sh
```

Copies `data/app.db` (or `$BAKERY_DB`) to `data/backups/app-YYYYMMDD-HHMM.db` (UTC stamp). `data/backups/` is gitignored via `data/*`.

Restore: stop uvicorn, then replace the live file:

```bash
cp data/backups/app-YYYYMMDD-HHMM.db data/app.db
```

If WAL files exist (`data/app.db-wal`, `data/app.db-shm`), stop the app first so the copy is consistent. Same idea as copying an H2 file db.

## Import loyalty JSON / drink tickets JSON

No tokens. Files on disk only. Do not commit `data/square_loyalty.json`.

Loyalty dump:

```bash
PYTHONPATH=. .venv/bin/python -m scripts.import_loyalty data/square_loyalty.json
```

Shape: `{ "program": { "id", "accrual", "rewards" }, "members": [ { "loyalty_id", "customer_id", "phone", "points", "lifetime_points", "enrolled_at", "area_code", "area_metro", "area_state", "area_region", "events": [...] } ] }`. Names/email are optional. Safe to re-run (upsert by `square_loyalty_id`).

Drink tickets JSON (fallback if ingest is not running):

```bash
PYTHONPATH=. .venv/bin/python -m scripts.import_drinks_json scripts/sample_tickets.json
```

Shape (object with `tickets`, or a bare array). `ordered_at` is ISO-8601; a missing timezone is treated as America/Chicago. `modifiers` can be a list of `{group, value}`, a dict, or a list of strings.

```json
{
  "tickets": [
    {
      "ordered_at": "2026-08-29T14:12:00-05:00",
      "drink_name": "Fruit Tea",
      "modifiers": [
        {"group": "Flavor", "value": "Strawberry"},
        {"group": "Boba", "value": "Strawberry Bursting"}
      ],
      "qty": 1,
      "ticket_name": "Walk-in",
      "source": "import"
    }
  ]
}
```

Live paid drinks land in the same `drink_tickets` table via `make ingest` on the shop laptop (see above). JSON import is the offline fallback. Do not put Square API tokens in this repo.

## Spring Boot + Angular map

Keep this stack — do not rewrite it in Java or Angular. The mapping is small:

| You already know | Here |
| --- | --- |
| `@RestController` / `@Controller` methods | FastAPI routes in `app/main.py` (`@app.get`, `@app.post`, …) |
| JPA `@Entity` + repository | SQLAlchemy 2.x models in `app/models.py`; sessions from `app/db.py` |
| `spring.datasource.url` (H2 file) | `BAKERY_DB` / `data/app.db` (WAL). See `app/db.py` |
| `@Service` under the controller | `app/loyalty.py` (filter/sort/stats/CSV) + `app/weekend.py` (Saturday vs Saturday) + `app/area_codes.py` (static NANP lookup) |
| Angular component + HTTP client | Jinja templates render HTML on the server. HTMX (`hx-post`, `hx-delete`, `hx-target`) swaps a fragment in place — like a server-rendered partial, not a SPA component |
| Angular dashboard poll / `setInterval` | Drink board: `hx-get="/board/tickets"` + `hx-trigger="every 10s"` replaces only the ticket list |
| CSV export endpoint | `GET /loyalty.csv` and `GET /weekend.csv` — UTF-8 attachments |
| H2 file mode / embedded DB | SQLite file `data/app.db` (WAL). Backup: `./scripts/backup.sh` |
| `spring-boot:run` with restart | `./scripts/run.sh` (`RELOAD=1` → uvicorn `--reload`) |
| `mvn test` | `./scripts/test.sh` or `make test` |
| Optional Docker image | `Dockerfile` + `docker-compose.yml` — not required on the shop laptop |

`templates/notes/_row.html` is the fragment (one note). `templates/board/_tickets.html` is the fragment (the drink tickets). The list page posts into `#notes-list` and never full-reloads. The board polls `#ticket-list` the same way. That is the whole “frontend.”

## Tests

```bash
./scripts/test.sh
```

Or `make test`, or `PYTHONPATH=. .venv/bin/pytest -q`. Uses a throwaway sqlite file (`BAKERY_DB` tempfile); does not touch `data/app.db`.

## Add a page

1. Copy a template under `templates/` (extend `base.html`).
2. Add a route in `app/main.py` that returns `templates.TemplateResponse(...)`.
3. Link it from the header or dashboard. For in-page updates, `hx-get` the route and set `hx-target` on the element you want to replace.


## Cloud Run split (Glenn deploys)

Two services from this repo. Shop Tech does not `gcloud` or open billing.

| Service | Image | Public | Routes |
| --- | --- | --- | --- |
| `bakery-drinks` | `Dockerfile.drinks` | private first, allUsers only after `/board` and `/health` work | `/board`, `/health`, `POST /internal/ingest` |
| `bakery-desk` | `Dockerfile.desk` | never allUsers | `/`, `/reports`, `/weekend`, `/loyalty`, `/notes` |

`BAKERY_SERVICE=drinks` 404s `/loyalty` (real phones). Public drinks reads `GETORDERS_URL` (existing getorders Cloud Run), not a new Square token. Laptop ingest is unchanged when `GETORDERS_URL` is unset. `INGEST_KEY` and `SQUARE_ACCESS_TOKEN` come from Secret Manager, not git. Header `X-Ingest-Key`. Sqlite on Cloud Run min 0 is ephemeral; laptop still sqlite.

Laptop default is unchanged (`BAKERY_SERVICE` unset or `laptop`).

## Optional Docker

Not required. Preferred on the shop laptop is venv. If you want clone-and-up:

```bash
docker compose up --build
```

`./data` is mounted so `app.db` survives container rebuilds. First start seeds demo data if the db file is missing. Then http://127.0.0.1:8000. Image is `python:3.12-slim`, non-root, listens on `0.0.0.0:8000`.
