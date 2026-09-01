"""FastAPI @Controller: admin dashboard, notes, reports, weekend, drink board, loyalty.

Each @app.get / @app.post / @app.delete is a request mapping. Jinja templates
are the view; HTMX swaps fragments in place (not an Angular SPA).

Drink board: last N minutes (default 15), newest first (ordered_at desc).
Tap a ticket → DELETE /board/tickets/{id} (drink is made, it drops off).
Demo drink is POST /board/demo-tick. Poll: GET /board/tickets every 10s.
"""

from __future__ import annotations

import json
import os
import secrets
import random
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, Header, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db import get_db, init_db
from app.models import DrinkModifier, DrinkTicket, LoyaltyMember, MerchItem, Note, SalesDaily, SalesSummary
from app import loyalty as loyalty_svc
from app import weekend as weekend_svc

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"
CHICAGO = ZoneInfo("America/Chicago")

# Kitchen demo drinks — names and modifiers only. No Square keys.
DEMO_DRINKS = [
    (
        "Fruit Tea",
        [
            {"group": "Flavor", "value": "Strawberry"},
            {"group": "Boba", "value": "Strawberry Bursting"},
        ],
        "Walk-in",
    ),
    (
        "Viet Coffee",
        [
            {"group": "Sweet", "value": "25%"},
            {"group": "Milk", "value": "Oat milk"},
        ],
        "Counter",
    ),
    (
        "Milk Tea",
        [
            {"group": "Flavor", "value": "Taro"},
            {"group": "Boba", "value": "Tapioca Boba"},
        ],
        "Mobile",
    ),
    (
        "Matcha",
        [{"group": "Matcha", "value": "Strawberry Matcha"}],
        "Walk-in",
    ),
    (
        "Biscoff",
        [{"group": "Milk", "value": "Oat milk"}],
        "Counter",
    ),
    (
        "Fruit Tea",
        [
            {"group": "Flavor", "value": "Mango"},
            {"group": "Boba", "value": "Mango Bursting"},
            {"group": "Ice", "value": "Less Ice"},
        ],
        "Table 2",
    ),
    (
        "Viet Coffee",
        [
            {"group": "Sweet", "value": "50%"},
            {"group": "Sauce", "value": "Salted Caramel"},
            {"group": "Extra Shot", "value": "Double Shot"},
        ],
        "#118",
    ),
    (
        "Milk Tea",
        [
            {"group": "Flavor", "value": "Brown Sugar"},
            {"group": "Boba", "value": "Tapioca Boba"},
            {"group": "Sweet", "value": "Normal"},
        ],
        "Mobile",
    ),
]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    from app.db import SessionLocal
    from scripts.load_weekend import load_if_empty

    with SessionLocal() as db:
        load_if_empty(db)
        db.commit()
    yield


app = FastAPI(title="Bakery Local", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def bakery_service() -> str:
    raw = (os.environ.get("BAKERY_SERVICE") or "laptop").strip().lower()
    if raw in ("drinks", "desk", "laptop"):
        return raw
    return "laptop"


@app.middleware("http")
async def service_gate(request: Request, call_next):
    """drinks never serves loyalty/phones. desk is not the tablet."""
    service = bakery_service()
    path = request.url.path
    if service == "drinks":
        allowed = (
            path == "/"
            or path == "/health"
            or path.startswith("/board")
            or path.startswith("/internal/ingest")
            or path.startswith("/static")
        )
        if not allowed:
            return Response("Not found.", status_code=404)
    elif service == "desk":
        blocked = path.startswith("/board") or path.startswith("/internal/ingest")
        if blocked:
            return Response("Not found.", status_code=404)
    return await call_next(request)


@app.get("/health")
def health():
    return {"ok": True, "service": bakery_service()}


@app.post("/internal/ingest")
def internal_ingest(
    request: Request,
    x_ingest_key: str | None = Header(default=None, alias="X-Ingest-Key"),
):
    """Cloud Scheduler / laptop hook. Never public without INGEST_KEY."""
    if bakery_service() == "desk":
        return Response("Not found.", status_code=404)
    expected = (os.environ.get("INGEST_KEY") or "").strip()
    got = (x_ingest_key or "").strip()
    if not expected or not got or not secrets.compare_digest(got, expected):
        return Response("Unauthorized.", status_code=401)
    token = (os.environ.get("SQUARE_ACCESS_TOKEN") or "").strip()
    if not token:
        return Response("Square token not set.", status_code=503)
    from scripts.ingest_drinks import DEFAULT_API_BASE, DEFAULT_LOCATION_ID, ingest_once

    location_id = (os.environ.get("SQUARE_LOCATION_ID") or "").strip() or DEFAULT_LOCATION_ID
    base_url = (os.environ.get("SQUARE_API_BASE") or DEFAULT_API_BASE).rstrip("/")
    inserted, skipped = ingest_once(
        token=token,
        location_id=location_id,
        minutes=20,
        base_url=base_url,
    )
    return {"inserted": inserted, "skipped": skipped}



def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def chicago_clock(dt: datetime) -> str:
    """Wall clock in America/Chicago, e.g. 2:07 PM."""
    local = _as_utc(dt).astimezone(CHICAGO)
    return local.strftime("%I:%M %p").lstrip("0")


def parse_modifiers(raw: str) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [{"group": str(k), "value": str(v)} for k, v in data.items() if v]
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if isinstance(item, str) and item.strip():
            out.append({"group": "", "value": item.strip()})
        elif isinstance(item, dict):
            value = item.get("value") or item.get("modifier") or ""
            group = item.get("group") or item.get("modifier_group") or ""
            if value:
                out.append({"group": str(group), "value": str(value)})
    return out


def ticket_views(rows: list[DrinkTicket]) -> list[dict]:
    now = datetime.now(timezone.utc)
    views = []
    for row in rows:
        ordered = _as_utc(row.ordered_at)
        age = max(0, int((now - ordered).total_seconds() // 60))
        views.append(
            {
                "id": row.id,
                "drink_name": row.drink_name,
                "qty": row.qty,
                "ticket_name": row.ticket_name,
                "source": row.source,
                "modifiers": parse_modifiers(row.modifiers_json),
                "clock": chicago_clock(row.ordered_at),
                "age_min": age,
            }
        )
    return views


def clamp_minutes(minutes: int) -> int:
    return max(1, min(minutes, 180))


def tickets_in_window(db: Session, minutes: int) -> list[DrinkTicket]:
    # Newest first. Tap-to-clear deletes the row (see board_ticket_done).
    cutoff = _utc_now() - timedelta(minutes=minutes)
    return db.scalars(
        select(DrinkTicket)
        .where(DrinkTicket.ordered_at >= cutoff)
        .order_by(DrinkTicket.ordered_at.desc(), DrinkTicket.id.desc())
    ).all()


def dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


templates.env.filters["dollars"] = lambda cents: dollars(int(cents or 0))
templates.env.filters["chicago_clock"] = chicago_clock
templates.env.filters["chicago_stamp"] = loyalty_svc.chicago_stamp
templates.env.filters["chicago_date"] = loyalty_svc.chicago_date


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    if bakery_service() == "drinks":
        return RedirectResponse("/board", status_code=302)
    ticket_count = db.scalar(select(func.count(DrinkTicket.id))) or 0
    drink_count = db.scalar(select(func.coalesce(func.sum(DrinkTicket.qty), 0))) or 0
    top_row = db.execute(
        select(DrinkTicket.drink_name, func.sum(DrinkTicket.qty).label("n"))
        .group_by(DrinkTicket.drink_name)
        .order_by(desc("n"))
        .limit(1)
    ).first()
    last_at = db.scalar(select(func.max(DrinkTicket.ordered_at)))
    loyalty_count = db.scalar(select(func.count(LoyaltyMember.id))) or 0
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "ticket_count": ticket_count,
            "drink_count": int(drink_count),
            "top_drink": top_row[0] if top_row else "",
            "last_order": chicago_clock(last_at) if last_at else "",
            "last_order_empty": last_at is None,
            "loyalty_count": int(loyalty_count),
        },
    )


@app.get("/notes", response_class=HTMLResponse)
def notes_list(request: Request, db: Session = Depends(get_db)):
    notes = db.scalars(select(Note).order_by(Note.created_at.desc(), Note.id.desc())).all()
    return templates.TemplateResponse(request, "notes/list.html", {"notes": notes})


@app.post("/notes", response_class=HTMLResponse)
def notes_create(
    request: Request,
    title: str = Form(...),
    body: str = Form(""),
    db: Session = Depends(get_db),
):
    title = title.strip()
    if not title:
        return Response("Title is required.", status_code=400)
    note = Note(title=title, body=body.strip())
    db.add(note)
    db.commit()
    db.refresh(note)
    if _is_htmx(request):
        return templates.TemplateResponse(request, "notes/_row.html", {"note": note})
    return RedirectResponse("/notes", status_code=303)


@app.delete("/notes/{note_id}")
def notes_delete(note_id: int, request: Request, db: Session = Depends(get_db)):
    note = db.get(Note, note_id)
    if note is not None:
        db.delete(note)
        db.commit()
    if _is_htmx(request):
        return Response(status_code=200)
    return RedirectResponse("/notes", status_code=303)


@app.get("/reports", response_class=HTMLResponse)
def reports_index(request: Request, db: Session = Depends(get_db)):
    summaries = db.scalars(select(SalesSummary).order_by(SalesSummary.sold_on.desc())).all()

    weekend_tickets = weekend_cents = weekday_tickets = weekday_cents = 0
    for row in summaries:
        if row.sold_on.weekday() >= 5:
            weekend_tickets += row.tickets
            weekend_cents += row.cents
        else:
            weekday_tickets += row.tickets
            weekday_cents += row.cents

    top_items = db.execute(
        select(
            SalesDaily.item_name,
            func.sum(SalesDaily.qty).label("qty"),
            func.sum(SalesDaily.cents).label("cents"),
        )
        .group_by(SalesDaily.item_name)
        .order_by(desc("qty"))
    ).all()

    modifier_rows = db.scalars(
        select(DrinkModifier).order_by(
            DrinkModifier.drink,
            DrinkModifier.modifier_group,
            DrinkModifier.qty.desc(),
        )
    ).all()
    modifiers_by_drink: dict[str, list[DrinkModifier]] = defaultdict(list)
    for row in modifier_rows:
        modifiers_by_drink[row.drink].append(row)

    merch = db.scalars(select(MerchItem).order_by(MerchItem.id)).all()

    return templates.TemplateResponse(
        request,
        "reports/index.html",
        {
            "summaries": summaries,
            "weekend_tickets": weekend_tickets,
            "weekend_cents": weekend_cents,
            "weekday_tickets": weekday_tickets,
            "weekday_cents": weekday_cents,
            "top_items": top_items,
            "modifiers_by_drink": modifiers_by_drink,
            "merch": merch,
        },
    )


@app.get("/weekend", response_class=HTMLResponse)
def weekend_index(
    request: Request,
    this: str = Query(""),
    db: Session = Depends(get_db),
):
    """Marketing scorecard: this Saturday vs last. No live Square."""
    card = weekend_svc.scorecard(db, this)
    merch = db.scalars(select(MerchItem).order_by(MerchItem.id)).all()
    return templates.TemplateResponse(
        request,
        "weekend/index.html",
        {"card": card, "merch": merch},
    )


@app.get("/weekend.csv")
def weekend_csv(this: str = Query(""), db: Session = Depends(get_db)):
    card = weekend_svc.scorecard(db, this)
    payload = weekend_svc.csv_bytes(card)
    headers = {"Content-Disposition": 'attachment; filename="weekend-scorecard.csv"'}
    return Response(content=payload, media_type="text/csv; charset=utf-8", headers=headers)


@app.get("/board", response_class=HTMLResponse)
def board_page(
    request: Request,
    minutes: int = Query(15),
    db: Session = Depends(get_db),
):
    minutes = clamp_minutes(minutes)
    rows = tickets_in_window(db, minutes)
    return templates.TemplateResponse(
        request,
        "board/index.html",
        {
            "minutes": minutes,
            "tickets": ticket_views(rows),
        },
    )


@app.get("/board/tickets", response_class=HTMLResponse)
def board_tickets(
    request: Request,
    minutes: int = Query(15),
    db: Session = Depends(get_db),
):
    minutes = clamp_minutes(minutes)
    rows = tickets_in_window(db, minutes)
    return templates.TemplateResponse(
        request,
        "board/_tickets.html",
        {
            "minutes": minutes,
            "tickets": ticket_views(rows),
        },
    )


@app.delete("/board/tickets/{ticket_id}", response_class=HTMLResponse)
def board_ticket_done(
    ticket_id: int,
    request: Request,
    minutes: int = Query(15),
    db: Session = Depends(get_db),
):
    """Tap on the board: drink is made, drop the ticket."""
    minutes = clamp_minutes(minutes)
    ticket = db.get(DrinkTicket, ticket_id)
    if ticket is not None:
        db.delete(ticket)
        db.commit()
    if _is_htmx(request):
        rows = tickets_in_window(db, minutes)
        return templates.TemplateResponse(
            request,
            "board/_tickets.html",
            {"minutes": minutes, "tickets": ticket_views(rows)},
        )
    return RedirectResponse(f"/board?minutes={minutes}", status_code=303)


@app.post("/board/demo-tick", response_class=HTMLResponse)
def board_demo_tick(
    request: Request,
    minutes: int = Query(15),
    db: Session = Depends(get_db),
):
    """Dev only: insert a random demo drink so the board can be tested without Square."""
    minutes = clamp_minutes(minutes)
    drink, modifiers, ticket_name = random.choice(DEMO_DRINKS)
    db.add(
        DrinkTicket(
            ordered_at=_utc_now(),
            drink_name=drink,
            modifiers_json=json.dumps(modifiers),
            qty=random.choice([1, 1, 1, 2]),
            ticket_name=ticket_name,
            source="demo-tick",
        )
    )
    db.commit()
    if _is_htmx(request):
        rows = tickets_in_window(db, minutes)
        return templates.TemplateResponse(
            request,
            "board/_tickets.html",
            {"minutes": minutes, "tickets": ticket_views(rows)},
        )
    return RedirectResponse(f"/board?minutes={minutes}", status_code=303)


@app.get("/loyalty", response_class=HTMLResponse)
def loyalty_list(
    request: Request,
    q: str = Query(""),
    segment: str = Query("all"),
    sort: str = Query("points"),
    dir: str = Query("desc"),
    page: int = Query(1),
    db: Session = Depends(get_db),
):
    now = loyalty_svc.utc_now()
    rows = loyalty_svc.list_members(db, q=q, segment=segment, sort=sort, direction=dir, now=now)
    page_rows, pager = loyalty_svc.paginate(rows, page)
    views = [loyalty_svc.member_view(m, now) for m in page_rows]
    stats = loyalty_svc.compute_stats(rows, now)
    hometown = loyalty_svc.hometown_rollup(rows)
    regions = loyalty_svc.region_counts(rows)
    csv_qs = loyalty_svc.filter_qs(q=q, segment=segment, sort=sort, dir=dir)
    page_qs = loyalty_svc.filter_qs(q=q, segment=segment, sort=sort, dir=dir)
    return templates.TemplateResponse(
        request,
        "loyalty/list.html",
        {
            "q": q,
            "segment": segment or "all",
            "sort": sort or "points",
            "dir": dir or "desc",
            "members": views,
            "pager": pager,
            "stats": stats,
            "hometown": hometown,
            "regions": regions,
            "program": loyalty_svc.PROGRAM,
            "chips": loyalty_svc.SEGMENT_CHIPS,
            "sorts": loyalty_svc.SORTS,
            "csv_qs": csv_qs,
            "page_qs": page_qs,
        },
    )


@app.get("/loyalty.csv")
def loyalty_csv(
    q: str = Query(""),
    segment: str = Query("all"),
    sort: str = Query("points"),
    dir: str = Query("desc"),
    db: Session = Depends(get_db),
):
    now = loyalty_svc.utc_now()
    rows = loyalty_svc.list_members(db, q=q, segment=segment, sort=sort, direction=dir, now=now)
    payload = loyalty_svc.csv_bytes(rows, now)
    headers = {"Content-Disposition": 'attachment; filename="loyalty-members.csv"'}
    return Response(content=payload, media_type="text/csv; charset=utf-8", headers=headers)


@app.get("/loyalty/{member_id}", response_class=HTMLResponse)
def loyalty_detail(member_id: int, request: Request, db: Session = Depends(get_db)):
    member = db.get(LoyaltyMember, member_id)
    if member is None:
        return Response("Member not found.", status_code=404)
    now = loyalty_svc.utc_now()
    events = loyalty_svc.events_for(db, member_id)
    return templates.TemplateResponse(
        request,
        "loyalty/detail.html",
        {
            "view": loyalty_svc.member_view(member, now),
            "member": member,
            "events": events,
            "program": loyalty_svc.PROGRAM,
        },
    )
