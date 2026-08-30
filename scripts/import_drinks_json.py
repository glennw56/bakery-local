"""Load drink tickets from a local JSON file into SQLite.

This is the "drop a save file" path until a live Square pull exists.
No Square keys. Usage from the repo root:

    PYTHONPATH=. .venv/bin/python -m scripts.import_drinks_json path/to/tickets.json

JSON shape (object or bare array):

    {
      "tickets": [
        {
          "ordered_at": "2026-08-29T14:12:00-05:00",
          "drink_name": "Fruit Tea",
          "modifiers": [
            {"group": "Flavor", "value": "Strawberry"},
            {"group": "Boba", "value": "Strawberry Bursting"}
          ],
          "qty": 1,
          "ticket_name": "Walk-in",
          "source": "import"
        }
      ]
    }

`ordered_at` is ISO-8601. A timezone offset is respected; a naive stamp is
treated as America/Chicago. `modifiers` may also be a dict
({"Flavor": "Strawberry"}) or a list of strings (["Strawberry", "Boba"]).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import DrinkTicket  # noqa: E402

CHICAGO = ZoneInfo("America/Chicago")


def parse_ordered_at(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CHICAGO)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def normalize_modifiers(value) -> str:
    if value is None:
        return "[]"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return json.dumps([{"group": "", "value": value}])
    if isinstance(value, dict):
        return json.dumps(
            [{"group": str(k), "value": str(v)} for k, v in value.items() if v],
            ensure_ascii=False,
        )
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append({"group": "", "value": item.strip()})
            elif isinstance(item, dict):
                val = item.get("value") or item.get("modifier") or ""
                group = item.get("group") or item.get("modifier_group") or ""
                if val:
                    out.append({"group": str(group), "value": str(val)})
        return json.dumps(out, ensure_ascii=False)
    return "[]"


def load_tickets(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        rows = data.get("tickets") or data.get("orders") or []
    elif isinstance(data, list):
        rows = data
    else:
        raise SystemExit("JSON must be an object with a tickets array, or a bare array")
    if not isinstance(rows, list):
        raise SystemExit("tickets must be a list")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Import drink tickets JSON into SQLite")
    parser.add_argument("json_path", type=Path, help="Path to tickets JSON")
    args = parser.parse_args()
    path = args.json_path.expanduser()
    if not path.is_file():
        raise SystemExit(f"file not found: {path}")

    rows = load_tickets(path)
    init_db()
    inserted = 0
    with SessionLocal() as db:
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            name = (raw.get("drink_name") or raw.get("drink") or "").strip()
            if not name:
                continue
            stamp = raw.get("ordered_at") or raw.get("orderedAt")
            ordered_at = parse_ordered_at(str(stamp)) if stamp else datetime.now(timezone.utc).replace(tzinfo=None)
            db.add(
                DrinkTicket(
                    ordered_at=ordered_at,
                    drink_name=name,
                    modifiers_json=normalize_modifiers(raw.get("modifiers") or raw.get("mods")),
                    qty=int(raw.get("qty") or 1),
                    ticket_name=str(raw.get("ticket_name") or raw.get("ticket") or ""),
                    source=str(raw.get("source") or "import"),
                )
            )
            inserted += 1
        db.commit()
    print(f"imported {inserted} drink_tickets from {path}")


if __name__ == "__main__":
    main()
