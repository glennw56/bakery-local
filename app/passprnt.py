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
    """One small 58mm slip. Styles sit on the tags, same shape as Test print.

    A style block or an image makes the starpassprnt:// URL long enough that
    Android Chrome drops it. Test print works because this HTML stays short.
    """
    index = int(ticket.get("drink_index") or 1)
    total = int(ticket.get("drink_total") or 1)
    count_label = f"Drink {index} of {total}"
    qty = int(ticket.get("qty") or 1)
    name = html.escape(str(ticket.get("drink_name") or ""))
    if qty > 1:
        name += f" ×{qty}"
    mods = []
    for mod in ticket.get("modifiers") or []:
        group = mod.get("group") or ""
        value = mod.get("value") or ""
        label = f"{group} {value}".strip() if group else value
        mods.append(
            f'<p style="margin:0.3mm 0 0;font-size:14px;font-weight:700">{html.escape(label)}</p>'
        )
    return (
        "<!DOCTYPE html><html><head>"
        '<meta charset="utf-8">'
        '<meta name="format-detection" content="telephone=no">'
        "</head><body style=\"margin:0;width:"
        f"{PRINT_WIDTH_MM}mm;background:#fff;color:#000;font-family:Arial,sans-serif\">"
        '<p style="margin:0;font-size:12px;font-weight:700">Order '
        f'{html.escape(str(ticket.get("order_id") or ""))}</p>'
        '<h1 style="margin:1mm 0 0;font-size:22px;line-height:1.05">'
        f'{html.escape(str(ticket.get("ticket_name") or "Walk-in"))}</h1>'
        '<p style="margin:0.5mm 0 1.5mm;font-size:14px;font-weight:700">'
        f'{html.escape(str(ticket.get("clock") or ""))}</p>'
        '<h2 style="margin:0;padding-top:1.2mm;border-top:0.4mm solid #000;font-size:22px;line-height:1.05">'
        f"{name}</h2>"
        f'{"".join(mods)}'
        '<p style="margin:1.5mm 0 0;padding-top:1.2mm;border-top:0.6mm solid #000;font-size:18px;font-weight:800">'
        f"{html.escape(count_label)}</p>"
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
