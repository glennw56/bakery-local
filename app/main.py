"""FastAPI app: admin dashboard, notes, reports, drink board, loyalty."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db import get_db, init_db
from app.models import DrinkModifier, DrinkTicket, LoyaltyMember, MerchItem, Note, SalesDaily, SalesSummary
from app import loyalty as loyalty_svc

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
    yield


app = FastAPI(title="Bakery Local", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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
    db: Session = Depends(get_db),
):
    now = loyalty_svc.utc_now()
    rows = loyalty_svc.list_members(db, q=q, segment=segment, sort=sort, direction=dir, now=now)
    views = [loyalty_svc.member_view(m, now) for m in rows]
    stats = loyalty_svc.compute_stats(rows, now)
    hometown = loyalty_svc.hometown_rollup(rows)
    regions = loyalty_svc.region_counts(rows)
    csv_qs = loyalty_svc.filter_qs(q=q, segment=segment, sort=sort, dir=dir)
    return templates.TemplateResponse(
        request,
        "loyalty/list.html",
        {
            "q": q,
            "segment": segment or "all",
            "sort": sort or "points",
            "dir": dir or "desc",
            "members": views,
            "stats": stats,
            "hometown": hometown,
            "regions": regions,
            "program": loyalty_svc.PROGRAM,
            "chips": loyalty_svc.SEGMENT_CHIPS,
            "sorts": loyalty_svc.SORTS,
            "csv_qs": csv_qs,
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
