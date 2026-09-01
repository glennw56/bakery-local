"""Laptop-side Square drink ingest.

ListPayments (COMPLETED, last 20 minutes) then RetrieveOrder. Only drink-like
line items become drink_tickets. The tablet never calls Square — it keeps
HTMX-polling GET /board/tickets against local sqlite.

Unpaid open tickets cannot be ingested: there is no COMPLETED payment yet.
Webhooks are not required. Tokens come from .env on the shop laptop only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DrinkIngestSeen, DrinkTicket

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCATION_ID = "L4CK6YWGT5XQX"
SQUARE_API_VERSION = "2025-01-23"
SQUARE_API_BASE = "https://connect.squareup.com"
LOOKBACK_MINUTES = 20
WATCH_INTERVAL_SEC = 25

# Catalog / DEMO_DRINKS names. "lemonade" is a drink unless the line is an entremet.
DRINK_NEEDLES = (
    "vietnamese coffee",
    "viet coffee",
    "fruit tea",
    "milk tea",
    "matcha",
    "biscoff",
    "coffee latte",
    "latte",
    "lemonade",
    "boba",
)

PASTRY_MARKERS = (
    "entrement",
    "entremet",
    "cinnamon roll",
    "croissant",
    "cookie",
    "coffee cake",
    "danish",
    "wholesale",
)

DRINK_CATEGORIES = ("drink", "drinks", "beverage", "beverages")
PASTRY_CATEGORIES = ("pastry", "pastries", "bakery", "wholesale")


def load_env_file(path: Path | None = None) -> None:
    """Load KEY=VAL from .env without overriding a real environment variable."""
    env_path = path or (ROOT / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


def square_access_token() -> str:
    return (os.environ.get("SQUARE_ACCESS_TOKEN") or "").strip()


def square_location_id() -> str:
    return (os.environ.get("SQUARE_LOCATION_ID") or "").strip() or DEFAULT_LOCATION_ID


def parse_square_time(value: str | None) -> datetime:
    """RFC3339 / ISO-8601 → naive UTC (same convention as drink_tickets.ordered_at)."""
    if not value:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _qty(raw: Any) -> int:
    try:
        n = int(float(str(raw if raw is not None else "1")))
    except (TypeError, ValueError):
        return 1
    return max(1, n)


def _line_name(item: dict) -> str:
    return str(item.get("name") or item.get("item_name") or "").strip()


def _line_category(item: dict) -> str:
    for key in ("catalog_category", "category", "category_name"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().lower()
        if isinstance(val, dict):
            name = val.get("name") or val.get("category_name") or ""
            if name:
                return str(name).strip().lower()
    cat = item.get("catalog_object")
    if isinstance(cat, dict):
        data = cat.get("category_data") or {}
        name = data.get("name") or cat.get("name") or ""
        if name:
            return str(name).strip().lower()
    return ""


def is_drink(name: str | None) -> bool:
    return is_drink_line({"name": name or ""})


def is_drink_line(item: dict | None) -> bool:
    """True for drink-like Square line items; pastry/wholesale is never a drink."""
    if not isinstance(item, dict):
        return False
    name = _line_name(item).lower()
    if not name:
        return False
    if any(marker in name for marker in PASTRY_MARKERS):
        return False
    category = _line_category(item)
    if category and any(p in category for p in PASTRY_CATEGORIES):
        return False
    if category and any(d == category or d in category.split() for d in DRINK_CATEGORIES):
        return True
    return any(needle in name for needle in DRINK_NEEDLES)


def _modifier_rows(item: dict) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    variation = str(item.get("variation_name") or "").strip()
    if variation and variation.lower() not in ("regular", "standard", "default"):
        out.append({"group": "Size", "value": variation})
    mods = item.get("modifiers") or []
    if not isinstance(mods, list):
        return out
    for mod in mods:
        if not isinstance(mod, dict):
            continue
        value = str(mod.get("name") or mod.get("value") or "").strip()
        if not value:
            continue
        group = str(mod.get("group") or mod.get("modifier_option_name") or "").strip()
        out.append({"group": group, "value": value})
    return out


def ticket_source(order: dict, payment: dict | None = None) -> tuple[str, str]:
    """Return (source, ticket_name) for the kitchen ticket.

    POS → source POS, ticket_name Counter. DoorDash/EXTERNAL drinks keep the
    channel name so the board can tell them apart.
    """
    payment = payment or {}
    product = str(
        (payment.get("application_details") or {}).get("square_product")
        or order.get("square_product")
        or ""
    ).upper()
    source_name = str((order.get("source") or {}).get("name") or "").strip()
    lowered = source_name.lower()

    if product == "SQUARE_POS" or lowered in ("point of sale", "square pos", "pos"):
        return "POS", "Counter"
    if "doordash" in lowered:
        return "DoorDash", "DoorDash"
    if "uber" in lowered:
        return "Uber Eats", "Uber Eats"
    if product == "EXTERNAL":
        label = source_name or "EXTERNAL"
        return label, label
    if source_name:
        return source_name, source_name
    return "Square", "Square"


def unwrap_order(payload: dict) -> dict:
    if isinstance(payload.get("order"), dict) and "line_items" in payload["order"]:
        return payload["order"]
    return payload


def tickets_from_order(order: dict, payment: dict | None = None) -> list[dict]:
    """Parse a Square order (+ optional payment) into drink_ticket dicts.

    Skips pastry/wholesale. DoorDash EXTERNAL pastry is skipped; a DoorDash
    drink still becomes a ticket. Idempotency keys are square_order_id + line uid.
    """
    payment = payment or {}
    order = unwrap_order(order) if "line_items" not in order else order
    order_id = str(order.get("id") or payment.get("order_id") or "").strip()
    ordered_at = parse_square_time(
        payment.get("created_at") or order.get("created_at")
    )
    source, ticket_name = ticket_source(order, payment)
    tickets: list[dict] = []
    for item in order.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        if not is_drink_line(item):
            continue
        line_uid = str(item.get("uid") or "").strip()
        tickets.append(
            {
                "ordered_at": ordered_at,
                "drink_name": _line_name(item),
                "modifiers_json": json.dumps(_modifier_rows(item), ensure_ascii=False),
                "qty": _qty(item.get("quantity")),
                "ticket_name": ticket_name,
                "source": source,
                "square_order_id": order_id,
                "square_line_uid": line_uid,
            }
        )
    return tickets


def _already_seen(db: Session, order_id: str, line_uid: str) -> bool:
    if not order_id or not line_uid:
        return False
    seen = db.get(DrinkIngestSeen, (order_id, line_uid))
    if seen is not None:
        return True
    existing = db.scalar(
        select(DrinkTicket.id).where(
            DrinkTicket.square_order_id == order_id,
            DrinkTicket.square_line_uid == line_uid,
        )
    )
    return existing is not None


def apply_tickets(db: Session, tickets: list[dict]) -> dict[str, int]:
    """Insert new drink tickets. Skip duplicates by Square order id + line uid.

    Seen keys survive tap-to-clear so --watch does not put a finished drink back.
    """
    inserted = skipped = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in tickets:
        order_id = str(row.get("square_order_id") or "").strip()
        line_uid = str(row.get("square_line_uid") or "").strip()
        if _already_seen(db, order_id, line_uid):
            skipped += 1
            continue
        db.add(
            DrinkTicket(
                ordered_at=row["ordered_at"],
                drink_name=row["drink_name"],
                modifiers_json=row.get("modifiers_json") or "[]",
                qty=int(row.get("qty") or 1),
                ticket_name=str(row.get("ticket_name") or ""),
                source=str(row.get("source") or "POS"),
                square_order_id=order_id or None,
                square_line_uid=line_uid or None,
            )
        )
        if order_id and line_uid:
            db.add(
                DrinkIngestSeen(
                    square_order_id=order_id,
                    square_line_uid=line_uid,
                    ingested_at=now,
                )
            )
        inserted += 1
    return {"inserted": inserted, "skipped": skipped}


def _square_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Square-Version": SQUARE_API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def list_completed_payments(
    client: Any,
    *,
    token: str,
    location_id: str,
    begin_time: datetime,
    base_url: str = SQUARE_API_BASE,
) -> list[dict]:
    """GET /v2/payments — COMPLETED only, this location, since begin_time."""
    begin = begin_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payments: list[dict] = []
    cursor: str | None = None
    headers = _square_headers(token)
    while True:
        params = {
            "begin_time": begin,
            "location_id": location_id,
            "status": "COMPLETED",
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor
        response = client.get(f"{base_url}/v2/payments", headers=headers, params=params)
        if response.status_code >= 400:
            raise RuntimeError(_safe_square_error("ListPayments", response))
        body = response.json() if response.content else {}
        for pay in body.get("payments") or []:
            if not isinstance(pay, dict):
                continue
            if str(pay.get("status") or "").upper() != "COMPLETED":
                continue
            payments.append(pay)
        cursor = body.get("cursor")
        if not cursor:
            break
    return payments


def retrieve_order(
    client: Any,
    *,
    token: str,
    order_id: str,
    base_url: str = SQUARE_API_BASE,
) -> dict | None:
    headers = _square_headers(token)
    response = client.get(f"{base_url}/v2/orders/{order_id}", headers=headers)
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise RuntimeError(_safe_square_error("RetrieveOrder", response))
    body = response.json() if response.content else {}
    order = body.get("order") if isinstance(body, dict) else None
    return order if isinstance(order, dict) else None


def _safe_square_error(op: str, response: Any) -> str:
    """Error text without card data, tokens, or customer fields."""
    code = getattr(response, "status_code", "?")
    detail = ""
    try:
        body = response.json()
        errors = body.get("errors") if isinstance(body, dict) else None
        if isinstance(errors, list) and errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            detail = str(first.get("code") or first.get("detail") or "")[:200]
    except Exception:  # noqa: BLE001
        detail = ""
    if detail:
        return f"{op} failed HTTP {code}: {detail}"
    return f"{op} failed HTTP {code}"


def ingest_once(
    db: Session,
    *,
    token: str,
    location_id: str,
    client: Any,
    now: datetime | None = None,
    lookback_minutes: int = LOOKBACK_MINUTES,
    base_url: str = SQUARE_API_BASE,
) -> dict[str, int]:
    """Pull recent COMPLETED payments and write new drink tickets."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    begin = now - timedelta(minutes=lookback_minutes)
    payments = list_completed_payments(
        client,
        token=token,
        location_id=location_id,
        begin_time=begin,
        base_url=base_url,
    )
    inserted = skipped = orders = 0
    seen_orders: set[str] = set()
    for pay in payments:
        order_id = str(pay.get("order_id") or "").strip()
        if not order_id:
            continue
        # Same order can have two tenders; still retrieve once per order id
        # but apply_tickets is the real idempotency. Retrieve every time so
        # a later payment still maps ordered_at from that payment if new lines
        # appeared — Square lines don't change after pay, so skip extra GETs.
        if order_id in seen_orders:
            continue
        seen_orders.add(order_id)
        order = retrieve_order(client, token=token, order_id=order_id, base_url=base_url)
        if not order:
            continue
        orders += 1
        stats = apply_tickets(db, tickets_from_order(order, pay))
        inserted += stats["inserted"]
        skipped += stats["skipped"]
    db.commit()
    return {
        "payments": len(payments),
        "orders": orders,
        "inserted": inserted,
        "skipped": skipped,
    }
