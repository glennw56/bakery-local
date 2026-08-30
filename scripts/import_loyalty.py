"""Load Square loyalty dump JSON into SQLite.

No Square tokens. File on disk only. Upserts by square_loyalty_id; safe to re-run
when more customer names/emails land. Usage from the repo root:

    PYTHONPATH=. .venv/bin/python -m scripts.import_loyalty
    PYTHONPATH=. .venv/bin/python -m scripts.import_loyalty data/square_loyalty.json

Default path is data/square_loyalty.json (gitignored).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.area_codes import lookup_code, lookup_phone  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.loyalty import compute_status, parse_iso  # noqa: E402
from app.models import LoyaltyEvent, LoyaltyMember  # noqa: E402

DEFAULT_PATH = ROOT / "data" / "square_loyalty.json"

EVENT_TYPES = {
    "ACCUMULATE": "ACCUMULATE",
    "ACCUMULATE_POINTS": "ACCUMULATE",
    "REDEEM": "REDEEM",
    "REDEEM_REWARD": "REDEEM",
    "ADJUST": "ADJUST",
    "ADJUST_POINTS": "ADJUST",
    "EXPIRE": "EXPIRE",
    "EXPIRE_POINTS": "EXPIRE",
    "CREATE": "OTHER",
    "OTHER": "OTHER",
}


def _s(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _i(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _segments_json(raw: Any) -> str:
    if raw is None or raw == "":
        return "[]"
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            return json.dumps(parts, ensure_ascii=False)
        raw = parsed
    if isinstance(raw, list):
        return json.dumps([str(x) for x in raw if str(x).strip()], ensure_ascii=False)
    return "[]"


def _geo_for(raw: dict[str, Any], phone: str) -> dict[str, str]:
    """Prefer dump area_* when present; otherwise NANP lookup from phone."""
    code = _s(raw.get("area_code") or raw.get("areaCode"))
    metro = _s(raw.get("area_metro") or raw.get("areaMetro") or raw.get("metro"))
    state = _s(raw.get("area_state") or raw.get("areaState") or raw.get("state"))
    region = _s(raw.get("area_region") or raw.get("areaRegion") or raw.get("region")).lower()
    if region in ("in_state", "in-state", "al"):
        region = "alabama"
    if region in ("oos", "out-of-state"):
        region = "out_of_state"
    if code and metro and region in ("local", "alabama", "out_of_state", "unknown"):
        return {
            "area_code": code,
            "area_metro": metro,
            "area_state": state,
            "area_region": region,
        }
    geo = lookup_phone(phone) if phone else lookup_code(code)
    if code and not geo["area_code"]:
        geo["area_code"] = code
    if metro and (geo["area_metro"] in ("", "Unknown") or not geo["area_code"]):
        geo["area_metro"] = metro
    if state and not geo["area_state"]:
        geo["area_state"] = state
    if region in ("local", "alabama", "out_of_state", "unknown"):
        geo["area_region"] = region
    return geo


def _event_type(raw: Any) -> str:
    key = _s(raw).upper().replace(" ", "_")
    return EVENT_TYPES.get(key, "OTHER" if key else "OTHER")


def load_payload(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    program: dict[str, Any] = {}
    members: list[dict[str, Any]] = []
    if isinstance(data, dict):
        prog = data.get("program") or {}
        if isinstance(prog, dict):
            program = prog
        rows = data.get("members") or data.get("loyalty_accounts") or data.get("accounts") or []
        if isinstance(rows, list):
            members = [r for r in rows if isinstance(r, dict)]
    elif isinstance(data, list):
        members = [r for r in data if isinstance(r, dict)]
    else:
        raise SystemExit("JSON must be an object with a members array, or a bare array")
    return program, members


def apply_member_fields(row: LoyaltyMember, raw: dict[str, Any]) -> None:
    phone = _s(raw.get("phone") or raw.get("phone_number") or raw.get("phoneNumber"))
    geo = _geo_for(raw, phone)
    row.square_customer_id = _s(raw.get("customer_id") or raw.get("customerId") or raw.get("square_customer_id"))
    row.given_name = _s(raw.get("given_name") or raw.get("givenName") or raw.get("first_name"))
    row.family_name = _s(raw.get("family_name") or raw.get("familyName") or raw.get("last_name"))
    row.phone = phone
    row.email = _s(raw.get("email") or raw.get("email_address"))
    row.points = _i(raw.get("points") or raw.get("balance"))
    row.lifetime_points = _i(raw.get("lifetime_points") or raw.get("lifetimePoints"))
    row.enrolled_at = parse_iso(raw.get("enrolled_at") or raw.get("enrolledAt"))
    row.updated_at = parse_iso(raw.get("updated_at") or raw.get("updatedAt"))
    row.visits = _i(raw.get("visits"))
    row.last_visit_at = parse_iso(raw.get("last_visit_at") or raw.get("lastVisitAt"))
    row.first_visit_at = parse_iso(raw.get("first_visit_at") or raw.get("firstVisitAt"))
    row.lifetime_cents = _i(raw.get("lifetime_cents") or raw.get("lifetimeCents"))
    row.favorite_item = _s(raw.get("favorite_item") or raw.get("favoriteItem"))
    row.favorite_drink = _s(raw.get("favorite_drink") or raw.get("favoriteDrink"))
    row.favorite_modifier = _s(raw.get("favorite_modifier") or raw.get("favoriteModifier"))
    row.zip_code = _s(raw.get("zip") or raw.get("postal_code") or raw.get("postalCode"))
    row.creation_source = _s(raw.get("creation_source") or raw.get("creationSource") or raw.get("source"))
    unsub = raw.get("email_unsubscribed")
    if unsub is True:
        row.email_unsubscribed = 1
    elif unsub is False:
        row.email_unsubscribed = 0
    else:
        row.email_unsubscribed = _i(unsub)
    row.segments_json = _segments_json(raw.get("segment_tags") or raw.get("segments") or raw.get("tags"))
    row.notes = _s(raw.get("notes") or raw.get("note"))
    row.area_code = geo["area_code"]
    row.area_metro = geo["area_metro"]
    row.area_state = geo["area_state"]
    row.area_region = geo["area_region"]
    row.status = compute_status(row)


def replace_events(db: Session, member: LoyaltyMember, raw_events: Any) -> int:
    if not isinstance(raw_events, list):
        return 0
    db.execute(delete(LoyaltyEvent).where(LoyaltyEvent.member_id == member.id))
    n = 0
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        at = parse_iso(item.get("at") or item.get("created_at") or item.get("createdAt"))
        if at is None:
            continue
        db.add(
            LoyaltyEvent(
                member_id=member.id,
                at=at,
                event_type=_event_type(item.get("type") or item.get("event_type")),
                points=_i(item.get("points")),
                order_id=_s(item.get("order_id") or item.get("orderId")),
                note=_s(item.get("note")),
            )
        )
        n += 1
    return n


def import_file(path: Path, db: Session) -> dict[str, int]:
    _program, rows = load_payload(path)
    existing = {
        m.square_loyalty_id: m
        for m in db.scalars(
            select(LoyaltyMember).where(LoyaltyMember.square_loyalty_id.is_not(None))
        ).all()
        if m.square_loyalty_id
    }
    inserted = updated = skipped = event_count = 0
    regions: Counter[str] = Counter()
    for raw in rows:
        lid = _s(raw.get("loyalty_id") or raw.get("id") or raw.get("square_loyalty_id"))
        if not lid:
            skipped += 1
            continue
        row = existing.get(lid)
        if row is None:
            row = LoyaltyMember(square_loyalty_id=lid)
            db.add(row)
            existing[lid] = row
            inserted += 1
        else:
            updated += 1
        apply_member_fields(row, raw)
        regions[row.area_region or "unknown"] += 1
        db.flush()
        event_count += replace_events(db, row, raw.get("events"))
    db.flush()
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "events": event_count,
        "total": inserted + updated,
        "local": int(regions.get("local", 0)),
        "alabama": int(regions.get("alabama", 0)),
        "out_of_state": int(regions.get("out_of_state", 0)),
        "unknown": int(regions.get("unknown", 0)),
    }


def summarize(stats: dict[str, int], path: Path) -> str:
    # Counts only — never dump phones.
    return (
        f"loyalty import {path.name}: {stats['total']} members "
        f"({stats['inserted']} new, {stats['updated']} updated, {stats['skipped']} skipped, "
        f"{stats['events']} events) · "
        f"{stats['local']} local, {stats['alabama']} other AL, "
        f"{stats['out_of_state']} out of state, {stats['unknown']} unknown"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Square loyalty JSON into SQLite")
    parser.add_argument(
        "json_path",
        nargs="?",
        type=Path,
        default=DEFAULT_PATH,
        help="Path to square_loyalty.json (default data/square_loyalty.json)",
    )
    args = parser.parse_args()
    path = args.json_path.expanduser()
    if not path.is_file():
        raise SystemExit(f"file not found: {path}")
    init_db()
    with SessionLocal() as db:
        stats = import_file(path, db)
        db.commit()
    print(summarize(stats, path))


if __name__ == "__main__":
    main()
