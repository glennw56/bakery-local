"""Upsert aggregated Saturday counts into weekend_days / weekend_items.

Does not touch sales_daily, sales_summary, loyalty, or merch. No Square keys.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import WeekendDay, WeekendItem  # noqa: E402

SAMPLE_JSON = ROOT / "scripts" / "sample_weekend.json"


def load_file(path: Path, db: Session) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    days = payload.get("days") or []
    n = 0
    for row in days:
        upsert_day(db, row)
        n += 1
    return n


def upsert_day(db: Session, row: dict) -> WeekendDay:
    sold_on = date.fromisoformat(str(row["sold_on"]))
    day = db.scalar(select(WeekendDay).where(WeekendDay.sold_on == sold_on))
    if day is None:
        day = WeekendDay(sold_on=sold_on)
        db.add(day)
        db.flush()
    day.tickets = int(row.get("tickets") or 0)
    day.cents = int(row.get("gross_cents") or 0)
    day.pastry_qty = int(row.get("pastry_qty") or 0)
    day.pastry_cents = int(row.get("pastry_cents") or 0)
    day.drink_qty = int(row.get("drink_qty") or 0)
    day.drink_cents = int(row.get("drink_cents") or 0)
    day.boba_modifiers = int(row.get("boba_modifiers") or 0)

    db.execute(delete(WeekendItem).where(WeekendItem.day_id == day.id))
    drinks = row.get("drinks") or {}
    for name, qty in drinks.items():
        db.add(
            WeekendItem(
                day_id=day.id,
                kind="drink",
                name=str(name),
                qty=int(qty or 0),
                rank=0,
            )
        )
    for rank, entry in enumerate(row.get("top_items") or [], start=1):
        if isinstance(entry, dict):
            name, qty = entry.get("name"), entry.get("qty")
        else:
            name, qty = entry[0], entry[1]
        db.add(
            WeekendItem(
                day_id=day.id,
                kind="top",
                name=str(name),
                qty=int(qty or 0),
                rank=rank,
            )
        )
    return day


def load_if_empty(db: Session, path: Path | None = None) -> int:
    existing = db.scalar(select(func.count(WeekendDay.id))) or 0
    if existing:
        print("weekend_days already have rows; skip those")
        return int(existing)
    path = path or SAMPLE_JSON
    if not path.is_file():
        print(f"no weekend sample at {path}; skip")
        return 0
    n = load_file(path, db)
    print(f"seeded {n} weekend_days from {path.name}")
    return n


def main() -> None:
    init_db()
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else SAMPLE_JSON
    with SessionLocal() as db:
        n = load_file(path, db)
        db.commit()
        print(f"upserted {n} weekend_days from {path}")


if __name__ == "__main__":
    main()
