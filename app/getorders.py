"""Read existing Cloud Run getorders into drink_tickets.

Public drinks board uses this when GETORDERS_URL is set. No Square token.
Laptop Square ingest is unchanged when GETORDERS_URL is unset.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.drinks import is_drink, upsert_tickets

LINE_HEAD = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")


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
                    "modifiers_json": json.dumps(mods, ensure_ascii=False),
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


def sync_if_configured(db: Session) -> tuple[int, int]:
    url = (os.environ.get("GETORDERS_URL") or "").strip()
    if not url:
        return 0, 0
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return 0, 0
    return upsert_tickets(db, tickets_from_payload(payload))
