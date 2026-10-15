"""58mm drink tickets for Star PassPRNT on the TSP100IV.

PassPRNT is Star's free iOS/Android app. The board opens
starpassprnt://v1/print/nopreview with one drink's HTML, then PassPRNT
returns to the back URL and the next drink prints. Each job cuts once
(cut=partial). PassPRNT's cut applies after the job, and HTML is one
raster, so separate jobs are what actually cuts between drinks.
Nothing here talks to Square.

Paper: TSP100IV with the 58mm guide prints 50.8mm (406 dots at 203 dpi).
Star's size code 2w7 is 51mm only on the TSP100IV SK. On a TSP100IV it is
not a listed width, and an invalid size falls back to 576 dots (72mm).
size=406 is the dot width Star lists for this printer's 58mm paper.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TestPrintJob

# Printable width on TSP100IV 58mm paper. The roll is 58mm; the head prints 50.8mm.
PRINT_WIDTH_MM = 50.8
PAPER_WIDTH_MM = 58
PASS_SIZE = "406"
PASS_CUT = "partial"
PASS_POPUP = "disable"
PASS_SCHEME = "starpassprnt://v1/print/nopreview"


def _modifiers(line) -> list[dict]:
    try:
        raw_mods = json.loads(line.modifiers_json or "[]")
    except json.JSONDecodeError:
        raw_mods = []
    modifiers = []
    if not isinstance(raw_mods, list):
        return modifiers
    for mod in raw_mods:
        if not isinstance(mod, dict):
            continue
        value = str(mod.get("value") or "").strip()
        if not value:
            continue
        modifiers.append({"group": str(mod.get("group") or "").strip(), "value": value})
    return modifiers


def ticket_for_line(order, line, clock: str, index: int, total: int) -> dict:
    """Freeze one drink. Later clears do not change this ticket."""
    name = (order.ticket_name or "").strip() or "Walk-in"
    return {
        "order_id": int(order.id),
        "line_id": int(line.id),
        "ticket_name": name,
        "clock": clock,
        "drink_index": int(index),
        "drink_total": int(total),
        "drink_name": line.drink_name,
        "qty": int(line.qty),
        "modifiers": _modifiers(line),
    }


def render_ticket_html(ticket: dict) -> str:
    """One short 58mm slip. Test print and real drinks use this same document.

    The slip is the order name, the drink, and that drink's modifiers.
    No price, no "Drink 1 of 3" count, no clock, no images. One width on the
    body keeps the TSP100IV's 50.8mm print area. Anything more makes the
    starpassprnt:// URL long enough that Android Chrome will not open it.
    """
    order_name = html.escape(str(ticket.get("ticket_name") or "Walk-in"))
    qty = int(ticket.get("qty") or 1)
    drink = html.escape(str(ticket.get("drink_name") or ""))
    if qty > 1:
        drink += f" x{qty}"
    mods = []
    for mod in ticket.get("modifiers") or []:
        group = str(mod.get("group") or "").strip()
        value = str(mod.get("value") or "").strip()
        label = f"{group} {value}".strip() if group else value
        if label:
            mods.append(f"<p>{html.escape(label)}</p>")
    return (
        "<!DOCTYPE html><html><head>"
        '<meta charset="utf-8">'
        '<meta name="format-detection" content="telephone=no">'
        f'</head><body style="width:{PRINT_WIDTH_MM}mm">'
        f"<p>{order_name}</p>"
        f"<h1>{drink}</h1>"
        f"{''.join(mods)}"
        "</body></html>"
    )


def sample_ticket() -> dict:
    """A short slip used only by the Test print button. Not a real order."""
    return {
        "order_id": "Test",
        "ticket_name": "Test print",
        "clock": "Sample",
        "drink_index": 1,
        "drink_total": 1,
        "drink_name": "Sample",
        "qty": 1,
        "modifiers": [],
    }


def passprnt_url(ticket_html: str, back: str) -> str:
    """Build the URL the tablet opens. back is where PassPRNT returns."""
    query = "&".join(
        (
            f"html={quote(ticket_html, safe='')}",
            f"size={quote(PASS_SIZE, safe='')}",
            f"cut={quote(PASS_CUT, safe='')}",
            f"popup={quote(PASS_POPUP, safe='')}",
            f"back={quote(back, safe='')}",
        )
    )
    return f"{PASS_SCHEME}?{query}"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enqueue_print_jobs(db: Session, order, clock: str) -> list[TestPrintJob]:
    """Queue one ticket per drink line that does not already have one.

    Calling this again after a new line is added prints only that line.
    Lines already queued, claimed, or printed are left alone.
    """
    lines = sorted(order.lines, key=lambda item: item.id)
    already = set(
        db.scalars(
            select(TestPrintJob.line_id).where(TestPrintJob.order_id == int(order.id))
        ).all()
    )
    total = len(lines)
    created: list[TestPrintJob] = []
    now = _now()
    for index, line in enumerate(lines, start=1):
        if int(line.id) in already:
            continue
        job = TestPrintJob(
            line_id=int(line.id),
            order_id=int(order.id),
            status="pending",
            ticket_json=json.dumps(ticket_for_line(order, line, clock, index, total)),
            created_at=now,
        )
        db.add(job)
        created.append(job)
    if created:
        db.commit()
        for job in created:
            db.refresh(job)
    return created


def load_ticket(job: TestPrintJob) -> dict:
    try:
        data = json.loads(job.ticket_json or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        return {}
    return data


def job_for_line(db: Session, line_id: int) -> TestPrintJob | None:
    return db.scalar(select(TestPrintJob).where(TestPrintJob.line_id == line_id))


def jobs_for_order(db: Session, order_id: int) -> list[TestPrintJob]:
    return list(
        db.scalars(
            select(TestPrintJob)
            .where(TestPrintJob.order_id == order_id)
            .order_by(TestPrintJob.id.asc())
        ).all()
    )


def pending_jobs(db: Session) -> list[TestPrintJob]:
    return list(
        db.scalars(
            select(TestPrintJob)
            .where(TestPrintJob.status == "pending")
            .order_by(TestPrintJob.created_at.asc(), TestPrintJob.id.asc())
        ).all()
    )


def claim_job(db: Session, line_id: int) -> TestPrintJob | None:
    """Mark one pending drink claimed. None if that line was already taken."""
    job = job_for_line(db, line_id)
    if job is None or job.status != "pending":
        return None
    job.status = "claimed"
    job.claimed_at = _now()
    db.commit()
    db.refresh(job)
    return job


def finish_job(db: Session, line_id: int) -> TestPrintJob | None:
    job = job_for_line(db, line_id)
    if job is None:
        return None
    job.status = "printed"
    job.printed_at = _now()
    db.commit()
    db.refresh(job)
    return job


def release_job(db: Session, line_id: int) -> TestPrintJob | None:
    """A failed PassPRNT return can print this drink again. A finished drink stays finished."""
    job = job_for_line(db, line_id)
    if job is None or job.status == "printed":
        return job
    job.status = "pending"
    job.claimed_at = None
    db.commit()
    db.refresh(job)
    return job
