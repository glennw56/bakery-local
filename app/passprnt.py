"""58mm drink tickets for Star PassPRNT on the TSP100IV.

PassPRNT is Star's free iOS/Android app. The board opens
starpassprnt://v1/print/nopreview with the ticket HTML, then PassPRNT
returns to the back URL. Nothing here talks to Square.

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


def ticket_snapshot(order, clock: str) -> dict:
    """Freeze the drinks on an order. Later clears do not change this."""
    lines = []
    drink_count = 0
    for line in sorted(order.lines, key=lambda item: item.id):
        qty = int(line.qty)
        drink_count += qty
        try:
            raw_mods = json.loads(line.modifiers_json or "[]")
        except json.JSONDecodeError:
            raw_mods = []
        modifiers = []
        if isinstance(raw_mods, list):
            for mod in raw_mods:
                if not isinstance(mod, dict):
                    continue
                value = str(mod.get("value") or "").strip()
                if not value:
                    continue
                modifiers.append(
                    {"group": str(mod.get("group") or "").strip(), "value": value}
                )
        lines.append(
            {
                "drink_name": line.drink_name,
                "qty": qty,
                "modifiers": modifiers,
            }
        )
    name = (order.ticket_name or "").strip() or "Walk-in"
    return {
        "order_id": int(order.id),
        "ticket_name": name,
        "clock": clock,
        "drink_count": drink_count,
        "lines": lines,
    }


def render_ticket_html(ticket: dict) -> str:
    """HTML PassPRNT turns into the 58mm raster. CSS is inline."""
    count = int(ticket.get("drink_count") or 0)
    count_label = "1 drink" if count == 1 else f"{count} drinks"
    drink_blocks = []
    for line in ticket.get("lines") or []:
        qty = int(line.get("qty") or 1)
        qty_html = f' <span class="qty">×{qty}</span>' if qty > 1 else ""
        mods = []
        for mod in line.get("modifiers") or []:
            group = mod.get("group") or ""
            value = mod.get("value") or ""
            label = f"{group} {value}".strip() if group else value
            mods.append(f'<p class="mod">{html.escape(label)}</p>')
        drink_blocks.append(
            "<section class=\"drink\">"
            f'<h2>{html.escape(str(line.get("drink_name") or ""))}{qty_html}</h2>'
            f'{"".join(mods)}'
            "</section>"
        )
    body = "\n".join(drink_blocks)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="format-detection" content="telephone=no">
<title>Drink ticket {html.escape(str(ticket.get("order_id") or ""))}</title>
<style>
  html, body {{ margin: 0; padding: 0; background: #fff; color: #000; }}
  body {{
    width: {PRINT_WIDTH_MM}mm;
    font-family: Arial, Helvetica, sans-serif;
  }}
  .num {{
    margin: 0;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.04em;
  }}
  h1 {{
    margin: 1mm 0 0;
    font-size: 22px;
    line-height: 1.05;
    font-weight: 800;
  }}
  .time {{
    margin: 0.5mm 0 1.5mm;
    font-size: 14px;
    font-weight: 700;
  }}
  .drink {{
    margin: 0;
    padding: 1.2mm 0 0.4mm;
    border-top: 0.4mm solid #000;
  }}
  h2 {{
    margin: 0;
    font-size: 22px;
    line-height: 1.05;
    font-weight: 800;
  }}
  .qty {{ font-size: 16px; }}
  .mod {{
    margin: 0.3mm 0 0;
    font-size: 14px;
    line-height: 1.15;
    font-weight: 700;
  }}
  .count {{
    margin: 1.5mm 0 0;
    padding-top: 1.2mm;
    border-top: 0.6mm solid #000;
    font-size: 18px;
    font-weight: 800;
  }}
</style>
</head>
<body>
<p class="num">Order {html.escape(str(ticket.get("order_id") or ""))}</p>
<h1>{html.escape(str(ticket.get("ticket_name") or "Walk-in"))}</h1>
<p class="time">{html.escape(str(ticket.get("clock") or ""))}</p>
{body}
<p class="count">{html.escape(count_label)}</p>
</body>
</html>
"""


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


def enqueue_print_job(db: Session, order, clock: str) -> TestPrintJob:
    ticket = ticket_snapshot(order, clock)
    job = TestPrintJob(
        order_id=int(order.id),
        status="pending",
        ticket_json=json.dumps(ticket),
        created_at=_now(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def load_ticket(job: TestPrintJob) -> dict:
    try:
        data = json.loads(job.ticket_json or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        return {}
    return data


def job_for_order(db: Session, order_id: int) -> TestPrintJob | None:
    return db.scalar(select(TestPrintJob).where(TestPrintJob.order_id == order_id))


def pending_jobs(db: Session) -> list[TestPrintJob]:
    return list(
        db.scalars(
            select(TestPrintJob)
            .where(TestPrintJob.status == "pending")
            .order_by(TestPrintJob.created_at.asc(), TestPrintJob.id.asc())
        ).all()
    )


def claim_job(db: Session, order_id: int) -> TestPrintJob | None:
    """Mark one pending ticket claimed. None if it was already taken."""
    job = job_for_order(db, order_id)
    if job is None or job.status != "pending":
        return None
    job.status = "claimed"
    job.claimed_at = _now()
    db.commit()
    db.refresh(job)
    return job


def finish_job(db: Session, order_id: int) -> TestPrintJob | None:
    job = job_for_order(db, order_id)
    if job is None:
        return None
    job.status = "printed"
    job.printed_at = _now()
    db.commit()
    db.refresh(job)
    return job


def release_job(db: Session, order_id: int) -> TestPrintJob | None:
    """A failed PassPRNT return can print again. A finished ticket stays finished."""
    job = job_for_order(db, order_id)
    if job is None or job.status == "printed":
        return job
    job.status = "pending"
    job.claimed_at = None
    db.commit()
    db.refresh(job)
    return job
