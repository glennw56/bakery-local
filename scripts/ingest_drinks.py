"""Laptop-only Square drink ingest into local sqlite.

POS does not fire order.created webhooks. ListPayments (COMPLETED) then
RetrieveOrder. Token lives in .env, never git. Never print tokens or card last4.

    PYTHONPATH=. .venv/bin/python -m scripts.ingest_drinks
    PYTHONPATH=. .venv/bin/python -m scripts.ingest_drinks --watch
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal, init_db  # noqa: E402
from app.drinks import tickets_from_order, upsert_tickets  # noqa: E402

SQUARE_VERSION = "2025-01-23"
DEFAULT_LOCATION_ID = "L4CK6YWGT5XQX"
DEFAULT_API_BASE = "https://connect.squareup.com"
WATCH_SLEEP_SEC = 25


def load_local_env(path: Path) -> None:
    """Load KEY=VAL from .env. Skip comments. Never print values. Do not override env."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Square-Version": SQUARE_VERSION,
        "Accept": "application/json",
    }


def list_completed_payments(
    client: httpx.Client,
    *,
    token: str,
    location_id: str,
    begin_time: str,
    base_url: str,
) -> list[dict]:
    payments: list[dict] = []
    cursor: str | None = None
    while True:
        params: dict[str, str] = {
            "location_id": location_id,
            "begin_time": begin_time,
            "status": "COMPLETED",
            "sort_order": "DESC",
        }
        if cursor:
            params["cursor"] = cursor
        response = client.get(
            f"{base_url}/v2/payments",
            headers=_headers(token),
            params=params,
        )
        if response.status_code >= 400:
            print(f"ListPayments HTTP {response.status_code}")
            return payments
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
    client: httpx.Client,
    *,
    token: str,
    order_id: str,
    base_url: str,
) -> dict | None:
    response = client.get(
        f"{base_url}/v2/orders/{order_id}",
        headers=_headers(token),
    )
    if response.status_code != 200:
        print(f"RetrieveOrder HTTP {response.status_code}")
        return None
    body = response.json() if response.content else {}
    order = body.get("order") if isinstance(body, dict) else None
    return order if isinstance(order, dict) else None


def ingest_once(
    *,
    token: str,
    location_id: str,
    minutes: int,
    base_url: str,
) -> tuple[int, int]:
    begin = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    begin_time = begin.strftime("%Y-%m-%dT%H:%M:%SZ")
    inserted = skipped = 0
    with httpx.Client(timeout=30.0) as client, SessionLocal() as db:
        payments = list_completed_payments(
            client,
            token=token,
            location_id=location_id,
            begin_time=begin_time,
            base_url=base_url,
        )
        seen_orders: set[str] = set()
        for pay in payments:
            order_id = str(pay.get("order_id") or "").strip()
            if not order_id or order_id in seen_orders:
                continue
            seen_orders.add(order_id)
            order = retrieve_order(
                client, token=token, order_id=order_id, base_url=base_url
            )
            if not order:
                continue
            ins, skip = upsert_tickets(db, tickets_from_order(order, pay))
            inserted += ins
            skipped += skip
    return inserted, skipped


def main() -> None:
    load_local_env(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Ingest paid Square drinks into local sqlite")
    parser.add_argument("--minutes", type=int, default=20, help="lookback window (default 20)")
    parser.add_argument("--watch", action="store_true", help="loop every 25s")
    args = parser.parse_args()

    token = (os.environ.get("SQUARE_ACCESS_TOKEN") or "").strip()
    if not token:
        print("set SQUARE_ACCESS_TOKEN in .env (not git)")
        raise SystemExit(2)

    location_id = (os.environ.get("SQUARE_LOCATION_ID") or "").strip() or DEFAULT_LOCATION_ID
    base_url = (os.environ.get("SQUARE_API_BASE") or DEFAULT_API_BASE).rstrip("/")

    init_db()
    while True:
        inserted, skipped = ingest_once(
            token=token,
            location_id=location_id,
            minutes=args.minutes,
            base_url=base_url,
        )
        print(f"inserted={inserted} skipped={skipped}")
        if not args.watch:
            break
        time.sleep(WATCH_SLEEP_SEC)


if __name__ == "__main__":
    main()
