"""Parse Square orders into kitchen drink_tickets.

Unpaid open tickets cannot be ingested: POS does not fire order.created
webhooks, and ListPayments only returns COMPLETED payments. The tablet never
calls Square; it HTMX-polls local sqlite.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DrinkIngestSeen, DrinkTicket

# Square Catalog Drink category ids seen live. Exact name "Drink" only —
# not "Drink Station". Do not treat "biscoff" as a drink name needle
# (that catches Biscoff Roll, which is Roll / Sweet / Heat Up Station).
DRINK_CATEGORY_IDS = frozenset(
    {
        "BYKQS3P2SI7WP22F6BWKFZGR",
        "OCV6MHUVAXUXXXFATFKLJNPI",
        "ROPXOXPWBYM42T3LJQESG3NX",
    }
)
DRINK_CATEGORY_NAME = "Drink"

# Name fallback when the line has no Catalog category (getreports mix,
# getorders payload, older fixtures). No "biscoff" needle.
_DRINK_NEEDLES = (
    "milk tea",
    "fruit tea",
    "vietnamese coffee",
    "viet coffee",
    "coffee latte",
    "latte",
    "matcha",
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


def _clean_ids(values: Iterable[str] | None) -> set[str]:
    return {str(v).strip() for v in (values or []) if str(v).strip()}


def _clean_names(values: Iterable[str] | None) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()]


def is_drink(
    name: str | None,
    category_ids: Iterable[str] | None = None,
    category_names: Iterable[str] | None = None,
) -> bool:
    """True for Square Drink-category lines.

    Prefer Catalog membership: id in DRINK_CATEGORY_IDS or exact name
    "Drink". When category is present and is not Drink, the line is not
    a drink (Biscoff Roll). Name needles are fallback only.
    """
    ids = _clean_ids(category_ids)
    names = _clean_names(category_names)
    if ids or names:
        if ids & DRINK_CATEGORY_IDS:
            return True
        return any(n == DRINK_CATEGORY_NAME or n.casefold() == "drink" for n in names)

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


def _add_category(obj: object, ids: list[str], names: list[str]) -> None:
    if isinstance(obj, str):
        text = obj.strip()
        if not text:
            return
        if text.casefold() == "drink" or " " in text or len(text) < 16:
            names.append(text)
        else:
            ids.append(text)
        return
    if not isinstance(obj, dict):
        return
    cid = obj.get("id") or obj.get("category_id") or obj.get("catalog_category_id")
    if cid:
        ids.append(str(cid).strip())
    cname = obj.get("name") or obj.get("category_name")
    if cname:
        names.append(str(cname).strip())


def categories_from_line_item(item: dict | None) -> tuple[list[str], list[str]]:
    """Pull Catalog category ids/names off a Square order line (several shapes)."""
    ids: list[str] = []
    names: list[str] = []
    raw = item if isinstance(item, dict) else {}

    for key in ("category_id", "catalog_category_id"):
        val = raw.get(key)
        if val:
            ids.append(str(val).strip())

    cat = raw.get("category")
    if cat:
        _add_category(cat, ids, names)

    for key in ("category_ids", "categories", "catalog_categories"):
        block = raw.get(key)
        if isinstance(block, list):
            for entry in block:
                _add_category(entry, ids, names)
        elif block:
            _add_category(block, ids, names)

    for blob in (raw.get("catalog_object"), raw.get("item_data")):
        if not isinstance(blob, dict):
            continue
        data = blob.get("item_data") if isinstance(blob.get("item_data"), dict) else blob
        if not isinstance(data, dict):
            continue
        if data.get("category_id"):
            ids.append(str(data["category_id"]).strip())
        for entry in data.get("categories") or []:
            _add_category(entry, ids, names)
        reporting = data.get("reporting_category")
        if reporting:
            _add_category(reporting, ids, names)

    return [x for x in ids if x], [x for x in names if x]


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
        cat_ids, cat_names = categories_from_line_item(item)
        if not is_drink(name, category_ids=cat_ids, category_names=cat_names):
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
    """Insert tickets; skip if this Square line was already ingested.

    drink_ingest_seen survives tap-to-clear so --watch does not put a
    finished drink back on the board.
    """
    inserted = 0
    skipped = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for kw in tickets:
        oid = str(kw.get("square_order_id") or "").strip()
        uid = str(kw.get("square_line_uid") or "").strip()
        if oid and uid:
            seen = db.get(DrinkIngestSeen, (oid, uid))
            if seen is not None:
                skipped += 1
                continue
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
        if oid and uid:
            db.add(
                DrinkIngestSeen(
                    square_order_id=oid,
                    square_line_uid=uid,
                    ingested_at=now,
                )
            )
        inserted += 1
    db.commit()
    return inserted, skipped
