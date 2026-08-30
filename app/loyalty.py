"""Loyalty @Service under the controller: filter, sort, stats, hometown rollup, CSV.

Routes in app/main.py call this. Area-code geography is app/area_codes.py (static NANP).
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.area_codes import format_phone, lookup_phone
from app.models import LoyaltyEvent, LoyaltyMember

CHICAGO = ZoneInfo("America/Chicago")

PROGRAM = {
    "id": "462c4068-b444-4798-996f-357009cb8316",
    "merchant": "Sunshine's Bakery",
    "location": "Irondale",
    "location_id": "L4CK6YWGT5XQX",
    "accrual": "1 point per $1.00 spend before tax",
    "rewards": [
        {"points": 100, "name": "A free Fruit Tea"},
        {"points": 200, "name": "$10.00 off entire sale"},
    ],
}

REWARD_TIERS = (
    (100, "A free Fruit Tea"),
    (200, "$10.00 off entire sale"),
)

SEGMENT_CHIPS = [
    ("all", "All"),
    ("active", "Active 30d"),
    ("lapsed", "Lapsed 60+"),
    ("high", "Ready ≥100"),
    ("never", "Never purchased"),
    ("email", "Has email"),
    ("phone", "Phone only"),
    ("local", "Local 205"),
    ("alabama", "Alabama"),
    ("out_of_state", "Out of state"),
    ("unknown", "Unknown phone"),
]

SORTS = [
    ("points", "Points"),
    ("lifetime", "Lifetime pts"),
    ("last_visit", "Last visit"),
    ("spend", "Lifetime $"),
    ("name", "Name"),
    ("enrolled", "Enrolled"),
]

CSV_COLUMNS = [
    "id",
    "square_loyalty_id",
    "square_customer_id",
    "given_name",
    "family_name",
    "display_name",
    "phone",
    "email",
    "points",
    "lifetime_points",
    "pts_to_next",
    "next_reward",
    "visits",
    "last_visit_at",
    "days_since_visit",
    "first_visit_at",
    "lifetime_cents",
    "lifetime_dollars",
    "favorite_item",
    "favorite_drink",
    "favorite_modifier",
    "creation_source",
    "segments",
    "enrolled_at",
    "updated_at",
    "status",
    "area_code",
    "metro",
    "state",
    "region",
    "zip",
    "email_unsubscribed",
    "notes",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def chicago_stamp(dt: datetime | None) -> str:
    if dt is None:
        return ""
    local = as_utc(dt).astimezone(CHICAGO)
    hour = local.strftime("%I").lstrip("0") or "0"
    return f"{local.strftime('%b')} {local.day}, {hour}:{local.strftime('%M %p')}"


def chicago_date(dt: datetime | None) -> str:
    if dt is None:
        return ""
    local = as_utc(dt).astimezone(CHICAGO)
    return f"{local.strftime('%b')} {local.day}, {local.year}"


def parse_segments(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [p.strip() for p in raw.split(",") if p.strip()]
    if isinstance(data, list):
        return [str(x) for x in data if str(x).strip()]
    return []


def display_name(member: LoyaltyMember) -> str:
    name = f"{(member.given_name or '').strip()} {(member.family_name or '').strip()}".strip()
    return name


def next_reward(points: int) -> tuple[int, str, int]:
    """Return (threshold, label, points still needed). Needed is 0 when already redeemable at that tier."""
    pts = int(points or 0)
    for thresh, name in REWARD_TIERS:
        if pts < thresh:
            return thresh, name, thresh - pts
    return 200, "$10.00 off entire sale", 0


def days_since(dt: datetime | None, now: datetime | None = None) -> int | None:
    if dt is None:
        return None
    now = now or utc_now()
    delta = as_utc(now).replace(tzinfo=None) - (dt if dt.tzinfo is None else dt.replace(tzinfo=None))
    return max(0, delta.days)


def compute_status(member: LoyaltyMember, now: datetime | None = None) -> str:
    now = now or utc_now()
    visits = int(member.visits or 0)
    life = int(member.lifetime_points or 0)
    cents = int(member.lifetime_cents or 0)
    last = member.last_visit_at
    if visits == 0 and last is None and life == 0 and cents == 0:
        return "never_purchased"
    if last is not None:
        age = days_since(last, now) or 0
        if age >= 60:
            return "lapsed"
        return "active"
    return "active"


def apply_area_from_phone(member: LoyaltyMember) -> None:
    geo = lookup_phone(member.phone)
    member.area_code = geo["area_code"]
    member.area_metro = geo["area_metro"]
    member.area_state = geo["area_state"]
    member.area_region = geo["area_region"]


def member_view(member: LoyaltyMember, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    pts = int(member.points or 0)
    thresh, reward_name, needed = next_reward(pts)
    last_days = days_since(member.last_visit_at, now)
    status = compute_status(member, now)
    name = display_name(member)
    phone_fmt = format_phone(member.phone)
    has_email = bool((member.email or "").strip())
    has_phone = bool((member.phone or "").strip())
    ready = pts >= 100
    segments = parse_segments(member.segments_json)
    geo_label = ""
    if member.area_code:
        metro = member.area_metro or "Unknown"
        st = member.area_state
        geo_label = f"{member.area_code} · {metro}" + (f", {st}" if st else "")
    usable = _usable_for(
        has_email=has_email,
        unsub=bool(member.email_unsubscribed),
        has_phone=has_phone,
        last_days=last_days,
        status=status,
        ready=ready,
        pts=pts,
        favorite_drink=(member.favorite_drink or "").strip(),
        favorite_item=(member.favorite_item or "").strip(),
        geo_label=geo_label,
        region=member.area_region or "",
    )
    return {
        "member": member,
        "id": member.id,
        "display_name": name or "Phone only",
        "has_name": bool(name),
        "phone_fmt": phone_fmt,
        "email": (member.email or "").strip(),
        "points": pts,
        "lifetime_points": int(member.lifetime_points or 0),
        "pts_to_next": needed,
        "next_reward_at": thresh,
        "next_reward_name": reward_name,
        "next_reward_label": (
            f"Ready · {reward_name}" if needed == 0 else f"{needed} to {reward_name}"
        ),
        "ready": ready,
        "visits": int(member.visits or 0),
        "last_visit": chicago_stamp(member.last_visit_at) if member.last_visit_at else "",
        "last_visit_days": last_days,
        "first_visit": chicago_stamp(member.first_visit_at) if member.first_visit_at else "",
        "enrolled": chicago_date(member.enrolled_at) if member.enrolled_at else "",
        "enrolled_stamp": chicago_stamp(member.enrolled_at) if member.enrolled_at else "",
        "lifetime_cents": int(member.lifetime_cents or 0),
        "status": status,
        "lapsed": status == "lapsed",
        "never": status == "never_purchased",
        "segments": segments,
        "geo_label": geo_label or "Unknown",
        "has_email": has_email,
        "has_phone": has_phone,
        "unsubscribed": bool(member.email_unsubscribed),
        "usable_for": usable,
        "row_class": _row_class(status, ready),
    }


def _row_class(status: str, ready: bool) -> str:
    bits = []
    if ready:
        bits.append("ready")
    if status == "lapsed":
        bits.append("lapsed")
    elif status == "never_purchased":
        bits.append("never")
    return " ".join(bits)


def _usable_for(
    *,
    has_email: bool,
    unsub: bool,
    has_phone: bool,
    last_days: int | None,
    status: str,
    ready: bool,
    pts: int,
    favorite_drink: str,
    favorite_item: str,
    geo_label: str,
    region: str,
) -> str:
    bits: list[str] = []
    if has_email and has_phone and not unsub:
        bits.append("email + SMS")
    elif has_email and not unsub:
        bits.append("email only")
    elif has_email and unsub and has_phone:
        bits.append("SMS only · email unsubscribed")
    elif has_phone:
        bits.append("SMS only")
    else:
        bits.append("no contact")
    if region == "local":
        bits.append("local 205")
    elif region == "alabama":
        bits.append("Alabama")
    elif region == "out_of_state":
        bits.append("out of state")
    if last_days is not None:
        if last_days >= 60:
            bits.append(f"lapsed {last_days}d")
        else:
            bits.append(f"last visit {last_days}d ago")
    elif status == "never_purchased":
        bits.append("never purchased")
    fav = favorite_drink or favorite_item
    if fav:
        bits.append(f"favorite {fav}")
    if ready:
        if pts >= 200:
            bits.append("ready for $10 off")
        else:
            bits.append("ready for free Fruit Tea")
    if geo_label and geo_label != "Unknown":
        bits.append(geo_label)
    return " · ".join(bits)


def _segment_clause(segment: str, now: datetime):
    segment = (segment or "all").strip().lower()
    cutoff_30 = now - timedelta(days=30)
    cutoff_60 = now - timedelta(days=60)
    if segment in ("", "all"):
        return None
    if segment == "active":
        return LoyaltyMember.last_visit_at >= cutoff_30
    if segment == "lapsed":
        return LoyaltyMember.last_visit_at <= cutoff_60
    if segment == "high":
        return LoyaltyMember.points >= 100
    if segment == "never":
        return (
            (LoyaltyMember.visits == 0)
            & LoyaltyMember.last_visit_at.is_(None)
            & (LoyaltyMember.lifetime_points == 0)
            & (LoyaltyMember.lifetime_cents == 0)
        )
    if segment == "email":
        return LoyaltyMember.email != ""
    if segment == "phone":
        return (LoyaltyMember.phone != "") & (LoyaltyMember.email == "")
    if segment == "local":
        return LoyaltyMember.area_region == "local"
    if segment == "alabama":
        # Other AL (not the 205/659 local overlay) — matches hometown rollup buckets.
        return LoyaltyMember.area_region == "alabama"
    if segment in ("out_of_state", "out"):
        return LoyaltyMember.area_region == "out_of_state"
    if segment in ("unknown", "unknown_phone"):
        return LoyaltyMember.area_region == "unknown"
    return None


def filtered_query(
    q: str = "",
    segment: str = "all",
    sort: str = "points",
    direction: str = "desc",
    now: datetime | None = None,
) -> Select[tuple[LoyaltyMember]]:
    now = now or utc_now()
    stmt = select(LoyaltyMember)
    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        stmt = stmt.where(
            or_(
                LoyaltyMember.given_name.ilike(like),
                LoyaltyMember.family_name.ilike(like),
                LoyaltyMember.phone.ilike(like),
                LoyaltyMember.email.ilike(like),
                (LoyaltyMember.given_name + " " + LoyaltyMember.family_name).ilike(like),
            )
        )
    clause = _segment_clause(segment, now)
    if clause is not None:
        stmt = stmt.where(clause)

    sort = (sort or "points").strip().lower()
    direction = (direction or "desc").strip().lower()
    if direction not in ("asc", "desc"):
        direction = "desc"
    descending = direction == "desc"

    def order(col, nulls_last: bool = True):
        ordered = col.desc() if descending else col.asc()
        if nulls_last:
            return (col.is_(None), ordered)
        return (ordered,)

    if sort == "lifetime":
        stmt = stmt.order_by(*order(LoyaltyMember.lifetime_points, nulls_last=False), LoyaltyMember.id)
    elif sort == "last_visit":
        stmt = stmt.order_by(*order(LoyaltyMember.last_visit_at), LoyaltyMember.id)
    elif sort == "spend":
        stmt = stmt.order_by(*order(LoyaltyMember.lifetime_cents, nulls_last=False), LoyaltyMember.id)
    elif sort == "name":
        # Blanks last, then family, given.
        blank = (LoyaltyMember.family_name == "") & (LoyaltyMember.given_name == "")
        name_dir = (
            LoyaltyMember.family_name.desc() if descending else LoyaltyMember.family_name.asc()
        )
        given_dir = LoyaltyMember.given_name.desc() if descending else LoyaltyMember.given_name.asc()
        stmt = stmt.order_by(blank, name_dir, given_dir, LoyaltyMember.id)
    elif sort == "enrolled":
        stmt = stmt.order_by(*order(LoyaltyMember.enrolled_at), LoyaltyMember.id)
    else:
        stmt = stmt.order_by(*order(LoyaltyMember.points, nulls_last=False), LoyaltyMember.id)
    return stmt


def list_members(
    db: Session,
    q: str = "",
    segment: str = "all",
    sort: str = "points",
    direction: str = "desc",
    now: datetime | None = None,
) -> list[LoyaltyMember]:
    now = now or utc_now()
    return list(db.scalars(filtered_query(q, segment, sort, direction, now)).all())


def compute_stats(members: list[LoyaltyMember], now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    n = len(members)
    points = sum(int(m.points or 0) for m in members)
    lifetime = sum(int(m.lifetime_points or 0) for m in members)
    spend = sum(int(m.lifetime_cents or 0) for m in members)
    active = lapsed = never = ready = emailed = phone_only = 0
    for m in members:
        status = compute_status(m, now)
        if status == "active" and m.last_visit_at is not None and (days_since(m.last_visit_at, now) or 0) <= 30:
            active += 1
        if status == "lapsed":
            lapsed += 1
        if status == "never_purchased":
            never += 1
        if int(m.points or 0) >= 100:
            ready += 1
        if (m.email or "").strip():
            emailed += 1
        elif (m.phone or "").strip():
            phone_only += 1
    avg_spend = int(spend / n) if n else 0
    return {
        "members": n,
        "points": points,
        "lifetime_points": lifetime,
        "active_30": active,
        "lapsed_60": lapsed,
        "ready": ready,
        "with_email": emailed,
        "phone_only": phone_only,
        "never": never,
        "avg_spend_cents": avg_spend,
        "lifetime_spend_cents": spend,
    }


def hometown_rollup(members: list[LoyaltyMember]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for m in members:
        key = (
            m.area_code or "",
            m.area_metro or "Unknown",
            m.area_state or "",
            m.area_region or "unknown",
        )
        buckets[key] += 1
    total = len(members) or 1
    rows = []
    for (code, metro, state, region), count in sorted(buckets.items(), key=lambda kv: (-kv[1], kv[0][0])):
        rows.append(
            {
                "area_code": code or "—",
                "metro": metro,
                "state": state,
                "region": region,
                "count": count,
                "pct": round(100.0 * count / total, 1),
            }
        )
    return rows


def region_counts(members: list[LoyaltyMember]) -> dict[str, int]:
    out = {"local": 0, "alabama": 0, "out_of_state": 0, "unknown": 0}
    for m in members:
        r = m.area_region or "unknown"
        if r not in out:
            r = "unknown"
        out[r] += 1
    return out


def filter_qs(**kwargs: Any) -> str:
    clean = {k: v for k, v in kwargs.items() if v not in (None, "", "all")}
    return urlencode(clean)


def csv_bytes(members: list[LoyaltyMember], now: datetime | None = None) -> bytes:
    now = now or utc_now()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for m in members:
        view = member_view(m, now)
        writer.writerow(
            {
                "id": m.id,
                "square_loyalty_id": m.square_loyalty_id or "",
                "square_customer_id": m.square_customer_id or "",
                "given_name": m.given_name or "",
                "family_name": m.family_name or "",
                "display_name": view["display_name"],
                "phone": m.phone or "",
                "email": m.email or "",
                "points": m.points,
                "lifetime_points": m.lifetime_points,
                "pts_to_next": view["pts_to_next"],
                "next_reward": view["next_reward_name"],
                "visits": m.visits,
                "last_visit_at": chicago_stamp(m.last_visit_at) if m.last_visit_at else "",
                "days_since_visit": view["last_visit_days"] if view["last_visit_days"] is not None else "",
                "first_visit_at": chicago_stamp(m.first_visit_at) if m.first_visit_at else "",
                "lifetime_cents": m.lifetime_cents,
                "lifetime_dollars": f"{(m.lifetime_cents or 0) / 100:.2f}",
                "favorite_item": m.favorite_item or "",
                "favorite_drink": m.favorite_drink or "",
                "favorite_modifier": m.favorite_modifier or "",
                "creation_source": m.creation_source or "",
                "segments": "|".join(view["segments"]),
                "enrolled_at": chicago_stamp(m.enrolled_at) if m.enrolled_at else "",
                "updated_at": chicago_stamp(m.updated_at) if m.updated_at else "",
                "status": view["status"],
                "area_code": m.area_code or "",
                "metro": m.area_metro or "",
                "state": m.area_state or "",
                "region": m.area_region or "",
                "zip": m.zip_code or "",
                "email_unsubscribed": m.email_unsubscribed,
                "notes": m.notes or "",
            }
        )
    # utf-8-sig so Excel on Windows opens it; still UTF-8.
    return buf.getvalue().encode("utf-8-sig")


def events_for(db: Session, member_id: int) -> list[LoyaltyEvent]:
    return list(
        db.scalars(
            select(LoyaltyEvent)
            .where(LoyaltyEvent.member_id == member_id)
            .order_by(LoyaltyEvent.at.desc(), LoyaltyEvent.id.desc())
        ).all()
    )
