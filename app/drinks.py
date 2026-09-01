"""Parse Square orders into kitchen drink_tickets.

Unpaid open tickets cannot be ingested: POS does not fire order.created
webhooks, and ListPayments only returns COMPLETED payments. The tablet never
calls Square; it HTMX-polls local sqlite.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DrinkTicket

# True if the line name contains any of these (casefold), after skip rules.
_DRINK_NEEDLES = (
    "milk tea",
    "fruit tea",
    "vietnamese coffee",
    "viet coffee",
    "coffee latte",
    "latte",
    "matcha",
    "biscoff",
    "boba",
    "lemonade",
    "coffee",
)

_NOT_DRINK = (
    "croissant",
    "cinnamon roll",
    "cookie",
    "danish",
    "merch",
    "wholesale",
)


def is_drink(name: str | None) -> bool:
    """True for drink-like item names; pastry / merch / entremets are not drinks."""
    n = (name or "").casefold()
    if not n.strip():
        return False
    # entremet / entrement (Pink Lemonade Entrement is pastry)
    if "entremet" in n or "entrement" in n:
        return False
    if "coffee cake" in n:
        return False
    for snip in _NOT_DRINK:
        if snip in n:
            return False
    return any(needle in n for needle in _DRINK_NEEDLES)


def _naive_utc(value: str | None) -> datetime:
    raw = (value or "").strip()
    if not raw:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _qty(raw: Any) -> int:
    if raw is None or raw == "":
        return 1
    try:
        return int(raw)
    except (TypeError, ValueError):
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return 1


def _ticket_source(order: dict, payment: dict) -> str:
    product = str(
        ((payment.get("application_details") or {}).get("square_product") or "")
    ).upper()
    source_type = str(payment.get("source_type") or "").upper()
    order_src = str(((order.get("source") or {}).get("name") or "")).casefold()
    if product == "EXTERNAL" or source_type == "EXTERNAL" or "doordash" in order_src:
        return "doordash"
    if product == "ECOMMERCE_API" or "online" in order_src:
        return "online"
    if product == "SQUARE_POS" or "point of sale" in order_src:
        return "pos"
    if source_type in ("CARD", "CASH"):
        return "pos"
    return "pos"


def tickets_from_order(order_dict: dict, payment_dict: dict) -> list[dict]:
    """Return DrinkTicket kwargs for drink lines. Skip pastry. qty>1 stays one row."""
    order = order_dict or {}
    payment = payment_dict or {}
    if isinstance(order.get("order"), dict) and "line_items" in order["order"]:
        order = order["order"]
    order_id = str(order.get("id") or payment.get("order_id") or "").strip()
    ordered_at = _naive_utc(payment.get("created_at"))
    source = _ticket_source(order, payment)
    tickets: list[dict] = []
    for item in order.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not is_drink(name):
            continue
        mods = []
        for mod in item.get("modifiers") or []:
            if not isinstance(mod, dict):
                continue
            value = str(mod.get("name") or "").strip()
            if value:
                mods.append({"group": "", "value": value})
        tickets.append(
            {
                "ordered_at": ordered_at,
                "drink_name": name,
                "modifiers_json": json.dumps(mods, ensure_ascii=False),
                "qty": _qty(item.get("quantity")),
                "ticket_name": str(item.get("variation_name") or "").strip(),
                "source": source,
                "square_order_id": order_id or None,
                "square_line_uid": str(item.get("uid") or "").strip() or None,
            }
        )
    return tickets


def upsert_tickets(db: Session, tickets: list[dict]) -> tuple[int, int]:
    """Insert tickets; skip if (square_order_id, square_line_uid) already exists."""
    inserted = 0
    skipped = 0
    for kw in tickets:
        oid = kw.get("square_order_id")
        uid = kw.get("square_line_uid")
        if oid and uid:
            found = db.scalar(
                select(DrinkTicket.id).where(
                    DrinkTicket.square_order_id == oid,
                    DrinkTicket.square_line_uid == uid,
                )
            )
            if found is not None:
                skipped += 1
                continue
        db.add(DrinkTicket(**kw))
        inserted += 1
    db.commit()
    return inserted, skipped
