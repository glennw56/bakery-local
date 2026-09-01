"""Live getorders → drink board views. No sqlite, no Firestore.

Public drinks board fetches this on every refresh when GETORDERS_URL is set.
Laptop Square ingest is unchanged when GETORDERS_URL is unset.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx

from app.drinks import is_drink

LINE_HEAD = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")
CHICAGO = ZoneInfo("America/Chicago")


def tickets_from_payload(payload: Any) -> list[dict]:
    """getorders is a list of [iso_time, order_id, ...drink_groups]."""
    if not isinstance(payload, list):
        return []
    out: list[dict] = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 3:
            continue
        ts = str(row[0] or "")
        oid = str(row[1] or "").strip()
        ordered_at = _naive_utc(ts)
        for idx, group in enumerate(row[2:]):
            if not isinstance(group, list) or not group:
                continue
            head = str(group[0] or "")
            match = LINE_HEAD.match(head)
            if not match:
                continue
            qty = int(match.group(1))
            name = match.group(2).strip()
            if not is_drink(name):
                continue
            ticket_name = ""
            mods: list[dict[str, str]] = []
            for extra in group[1:]:
                text = str(extra or "").strip()
                if not text:
                    continue
                if text.lower().startswith("name:"):
                    ticket_name = text.split(":", 1)[1].strip()
                else:
                    mods.append({"group": "", "value": text})
            out.append(
                {
                    "ordered_at": ordered_at,
                    "drink_name": name,
                    "modifiers": mods,
                    "qty": max(1, qty),
                    "ticket_name": ticket_name,
                    "source": "getorders",
                    "square_order_id": oid or None,
                    "square_line_uid": f"{oid}:{idx}" if oid else None,
                }
            )
    return out


def _naive_utc(value: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def live_ticket_views(minutes: int, payload: Any | None = None) -> list[dict] | None:
    """Parse-and-render. Returns None when GETORDERS_URL is unset (use sqlite)."""
    url = (os.environ.get("GETORDERS_URL") or "").strip()
    if payload is None and not url:
        return None
    if payload is None:
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()
        except Exception:
            return []
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=max(1, minutes))
    now = datetime.now(timezone.utc)
    views: list[dict] = []
    for row in tickets_from_payload(payload):
        ordered = row["ordered_at"]
        if ordered < cutoff:
            continue
        aware = ordered.replace(tzinfo=timezone.utc)
        age = max(0, int((now - aware).total_seconds() // 60))
        views.append(
            {
                "id": None,
                "drink_name": row["drink_name"],
                "qty": row["qty"],
                "ticket_name": row["ticket_name"],
                "source": row["source"],
                "modifiers": row["modifiers"],
                "clock": aware.astimezone(CHICAGO).strftime("%-I:%M %p"),
                "age_min": age,
                "square_order_id": row["square_order_id"],
                "ordered_at": ordered,
            }
        )
    views.sort(key=lambda v: v["ordered_at"], reverse=True)
    for v in views:
        v.pop("ordered_at", None)
    return views


def group_order_views(tickets: list[dict]) -> list[dict]:
    """One KDS card per Square order. Newest order first."""
    orders: list[dict] = []
    index: dict[str, dict] = {}
    for t in tickets:
        oid = str(t.get("square_order_id") or t.get("id") or "").strip()
        if not oid:
            oid = f"anon-{len(orders)}"
        if oid not in index:
            order = {
                "order_id": oid,
                "clock": t.get("clock") or "",
                "age_min": t.get("age_min") or 0,
                "ticket_name": t.get("ticket_name") or "",
                "accent": t.get("drink_name") or "",
                "drinks": [],
            }
            index[oid] = order
            orders.append(order)
        order = index[oid]
        if t.get("ticket_name") and not order["ticket_name"]:
            order["ticket_name"] = t["ticket_name"]
        order["drinks"].append(
            {
                "drink_name": t.get("drink_name") or "",
                "qty": t.get("qty") or 1,
                "modifiers": t.get("modifiers") or [],
            }
        )
    return orders


def live_order_views(
    minutes: int,
    payload: Any | None = None,
    cleared: Iterable[str] | None = None,
) -> list[dict] | None:
    tickets = live_ticket_views(minutes, payload=payload)
    if tickets is None:
        return None
    skip = {str(x).strip() for x in (cleared or []) if str(x).strip()}
    if skip:
        tickets = [t for t in tickets if str(t.get("square_order_id") or "") not in skip]
    return group_order_views(tickets)
