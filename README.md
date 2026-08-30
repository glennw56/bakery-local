# Bakery Local

A local-first shop notebook, reports desk, drink kitchen display, and loyalty
member book. One SQLite file on disk, no cloud required, no Square keys in this repo.

SQLite is the whole backend: a single file (`data/app.db`) you can copy, back up, or
delete. WAL mode lets the UI keep reading while a later import writes. There is no
Postgres, no Docker, and no hosted database to provision. GitHub is not required.

## If you know Spring Boot + Angular

Keep this stack — do not rewrite it in Java or Angular. The mapping is small:

| You already know | Here |
| --- | --- |
| `@RestController` / `@Controller` methods | FastAPI routes in `app/main.py` (`@app.get`, `@app.post`, …) |
| JPA `@Entity` + repository | SQLAlchemy 2.x models in `app/models.py`; sessions from `app/db.py` |
| Angular component + HTTP client | Jinja templates render HTML on the server. HTMX (`hx-post`, `hx-delete`, `hx-target`) swaps a fragment in place — like a server-rendered partial, not a SPA component |
| Angular dashboard poll / `setInterval` | Drink board: `hx-get="/board/tickets"` + `hx-trigger="every 10s"` replaces only the ticket list |
| `@Service` under the controller | `app/loyalty.py` (filter/sort/stats/CSV) + `app/area_codes.py` (static NANP lookup) |
| CSV export endpoint | `GET /loyalty.csv` — same filters as the list, UTF-8 attachment |
| H2 file mode / embedded DB | SQLite file `data/app.db` (WAL). Backup: `cp data/app.db` |
| `spring-boot:run` with restart | `PYTHONPATH=. uvicorn app.main:app --reload` |

`templates/notes/_row.html` is the fragment (one note). `templates/board/_tickets.html`
is the fragment (the drink tickets). The list page posts into `#notes-list` and never
full-reloads. The board polls `#ticket-list` the same way. That is the whole “frontend.”

## Screens

- `/` — admin dashboard (stat tiles + links to Reports, Loyalty, Drink board, Notes)
- `/reports` — weekend vs weekday, top items, drink × modifier, merch, site landmines
- `/loyalty` — member book: search, segment chips, hometown rollup, dense table, CSV export
- `/loyalty/{id}` — one-member dossier (balance, marketing, points ledger)
- `/board` — kitchen drink display (last 15 minutes, newest first; tap a ticket to clear it)
- `/notes` — scratch pad

## Run

Python 3.12+ from the repo root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=. .venv/bin/python -m scripts.seed_demo
PYTHONPATH=. .venv/bin/uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

`seed_demo` writes reports tables (if empty), always refreshes ~8 drink tickets
into the last 15 minutes so `/board` looks alive, and loads loyalty: if
`data/square_loyalty.json` exists it upserts that dump; otherwise it seeds ~40 demo
members. Loyalty seed is skipped when `loyalty_members` already has rows. Reports
and tickets are never wiped by a loyalty import.

Tests:

```bash
PYTHONPATH=. .venv/bin/pytest -q
```

htmx 2.x is vendored at `static/htmx.min.js` (offline). CDN equivalent:
`https://unpkg.com/htmx.org@2.0.10/dist/htmx.min.js`

## Drink board on a tablet in the shop

1. On the shop laptop, from this folder, bind every interface so the tablet can reach it:

   ```bash
   PYTHONPATH=. .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

2. Find the laptop’s LAN address (Wi‑Fi / Settings, or `ip addr` / `ipconfig`).
   Example: `192.168.1.20`.
3. On the tablet, open `http://192.168.1.20:8000/board`.
4. Add to Home Screen if you want. Keep the tablet plugged in, brightness up,
   auto-lock off.

`?minutes=15` is the default window. Newest tickets sit first. Tap a ticket when
the drink is done and it drops off the board. The list refreshes every 10 seconds.

Dev-only: the **Demo drink** button (or `POST /board/demo-tick`) inserts a random
demo drink so you can test the UI without Square.


## Loyalty member book

`GET /loyalty` is the Marketing/Books desk. Filter by name/phone/email, segment
(active / lapsed / ready to redeem / never purchased / email / phone-only / local
205 / Alabama / out of state / unknown phone), and sort. Hometown rollup is NANP
area-code geography only (205/659 = Birmingham / Irondale local). No reverse-phone
API, no skip-tracing.

`GET /loyalty.csv` downloads the current filter as UTF-8 CSV (area_code, metro, state
included). `GET /loyalty/{id}` is the dossier.

Square facts modeled here: 1 point per $1 before tax; 100 pts = free Fruit Tea;
200 pts = $10 off entire sale. LoyaltyAccount has phone + points, not email or
favorite item — those fields are denormalized onto the member row so the list stays
useful when order joins are sparse.

Import a dump (no tokens; file on disk only):

```bash
PYTHONPATH=. .venv/bin/python -m scripts.import_loyalty data/square_loyalty.json
```

Shape: `{ "program": { "id", "accrual", "rewards" }, "members": [ { "loyalty_id",
"customer_id", "phone", "points", "lifetime_points", "enrolled_at", "area_code",
"area_metro", "area_state", "area_region", "events": [...] } ] }`. Names/email are
optional. Safe to re-run (upsert by `square_loyalty_id`).

## Drop a JSON file of tickets

Until a live Square pull exists, save tickets to a JSON file and import:

```bash
PYTHONPATH=. .venv/bin/python -m scripts.import_drinks_json scripts/sample_tickets.json
```

Shape (object with `tickets`, or a bare array). `ordered_at` is ISO-8601; a missing
timezone is treated as America/Chicago. `modifiers` can be a list of
`{group, value}`, a dict, or a list of strings.

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

A later Square import can land in the same `drink_tickets` / `sales_*` tables.
Do not put Square API tokens in this repo.

## Add a page

1. Copy a template under `templates/` (extend `base.html`).
2. Add a route in `app/main.py` that returns `templates.TemplateResponse(...)`.
3. Link it from the header or dashboard. For in-page updates, `hx-get` the route
   and set `hx-target` on the element you want to replace.

## Backup

```bash
cp data/app.db data/app.db.bak
```

`data/` contents are gitignored except `.gitkeep`.
