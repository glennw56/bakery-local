"""Live getreports → desk reports / weekend / loyalty views. No sqlite upsert.

Desk fetches this on every refresh when GETREPORTS_URL is set.
Laptop sqlite is unchanged when GETREPORTS_URL is unset.
Loyalty phones stay on desk (never allUsers). /loyalty on getreports is gated.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import httpx

from app.area_codes import lookup_phone
from app.drinks import is_drink
from app.weekend import DRINK_MIX_ORDER, MANGO_NAME, VIET_NAME, _pct, pair_sentence

CHICAGO = ZoneInfo("America/Chicago")
CHANNEL_ORDER = ("Point of Sale", "DoorDash", "Uber Eats", "Square Online")


def _http_get(url: str, headers: dict[str, str] | None = None, timeout: float = 15.0) -> Any:
    """{} on live fetch error. Caller already checked GETREPORTS_URL."""
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers=headers or {})
            response.raise_for_status()
            return response.json()
    except Exception:
        return {}


def loyalty_url(base: str) -> str:
    """Always GET getreports /loyalty (Glenn also accepts ?loyalty=1). Never the sales /."""
    raw = (base or "").strip()
    parsed = urlparse(raw)
    path = (parsed.path or "").rstrip("/")
    if not path.endswith("/loyalty"):
        path = f"{path}/loyalty" if path else "/loyalty"
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("loyalty", "1")
    return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, urlencode(query), ""))


def fetch_sales() -> Any | None:
    """GET sales JSON. None when GETREPORTS_URL is unset (laptop sqlite). {} on error."""
    url = (os.environ.get("GETREPORTS_URL") or "").strip()
    if not url:
        return None
    return _http_get(url)


def fetch_loyalty(ingest_key: str | None = None) -> Any | None:
    """GET /loyalty with X-Ingest-Key. None when GETREPORTS_URL unset. {} on error."""
    url = (os.environ.get("GETREPORTS_URL") or "").strip()
    if not url:
        return None
    headers: dict[str, str] = {}
    key = (ingest_key or "").strip()
    if key:
        headers["X-Ingest-Key"] = key
    return _http_get(loyalty_url(url), headers, timeout=60.0)


def _as_of_chicago(payload: dict) -> datetime:
    raw = str(payload.get("as_of") or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CHICAGO)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _channel_rows(raw: Any) -> list[dict[str, Any]]:
    """tickets + cents only. Do not copy payload dollars."""
    if not isinstance(raw, dict):
        return []
    names = [n for n in CHANNEL_ORDER if n in raw]
    names += sorted((n for n in raw if n not in CHANNEL_ORDER), key=str.casefold)
    out: list[dict[str, Any]] = []
    for name in names:
        row = raw.get(name)
        if not isinstance(row, dict):
            continue
        label = str(name or "").strip()
        if not label:
            continue
        out.append({"name": label, "tickets": _int(row.get("tickets")), "cents": _int(row.get("cents"))})
    return out


def _rollup(block: Any) -> dict[str, Any]:
    if not isinstance(block, dict):
        block = {}
    tickets = _int(block.get("tickets"))
    cents = _int(block.get("cents"))
    channels = _channel_rows(block.get("channels"))
    top = block.get("top_items") if isinstance(block.get("top_items"), list) else []
    items: list[tuple[str, int, int]] = []
    for row in top:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        items.append((name, _int(row.get("qty")), _int(row.get("cents"))))
    return {"tickets": tickets, "cents": cents, "channels": channels, "top_items": items}


def _qty_map(items: list[tuple[str, int, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, qty, _cents in items:
        out[name] = out.get(name, 0) + qty
    return out


def _named_qty(mapping: dict[str, int], name: str) -> int:
    if name in mapping:
        return mapping[name]
    needle = name.casefold()
    for key, qty in mapping.items():
        if key.casefold() == needle:
            return qty
    return 0


def _split_pastry_drink(items: list[tuple[str, int, int]]) -> tuple[int, int, int, int]:
    pastry_qty = pastry_cents = drink_qty = drink_cents = 0
    for name, qty, cents in items:
        if is_drink(name):
            drink_qty += qty
            drink_cents += cents
        else:
            pastry_qty += qty
            pastry_cents += cents
    return pastry_qty, pastry_cents, drink_qty, drink_cents


@dataclass
class LiveSummary:
    sold_on: date
    tickets: int = 0
    cents: int = 0
    note: str = ""


def reports_from_payload(payload: Any) -> dict[str, Any]:
    """Template vars for /reports. Empty merch/modifiers — getreports does not send them."""
    data = payload if isinstance(payload, dict) else {}
    sales = data.get("sales") if isinstance(data.get("sales"), dict) else {}
    today = _rollup(sales.get("today"))
    week = _rollup(sales.get("week"))
    as_of = _as_of_chicago(data)
    today_on = as_of.date()
    week_on = (as_of - timedelta(days=as_of.weekday())).date()
    summaries = [
        LiveSummary(sold_on=today_on, tickets=today["tickets"], cents=today["cents"], note="Today"),
        LiveSummary(sold_on=week_on, tickets=week["tickets"], cents=week["cents"], note="Week to date"),
    ]
    if today_on.weekday() >= 5:
        weekend_tickets, weekend_cents = today["tickets"], today["cents"]
        weekday_tickets = weekday_cents = 0
    else:
        weekday_tickets, weekday_cents = today["tickets"], today["cents"]
        weekend_tickets = weekend_cents = 0
    top_items = week["top_items"] or today["top_items"]
    return {
        "summaries": summaries,
        "weekend_tickets": weekend_tickets,
        "weekend_cents": weekend_cents,
        "weekday_tickets": weekday_tickets,
        "weekday_cents": weekday_cents,
        "top_items": top_items,
        "today_channels": today["channels"],
        "week_channels": week["channels"],
        "modifiers_by_drink": {},
        "merch": [],
        "today_tickets": today["tickets"],
        "today_cents": today["cents"],
        "as_of": as_of,
    }


def scorecard_from_payload(payload: Any) -> dict[str, Any]:
    """Today vs week-to-date. getreports has no Saturday pair — do not invent one."""
    data = payload if isinstance(payload, dict) else {}
    sales = data.get("sales") if isinstance(data.get("sales"), dict) else {}
    today = _rollup(sales.get("today"))
    week = _rollup(sales.get("week"))
    as_of = _as_of_chicago(data)
    this_on = as_of.date()
    last_on = (as_of - timedelta(days=as_of.weekday())).date()
    t_pastry_q, t_pastry_c, t_drink_q, t_drink_c = _split_pastry_drink(today["top_items"])
    l_pastry_q, l_pastry_c, l_drink_q, l_drink_c = _split_pastry_drink(week["top_items"])
    this_tot = {
        "tickets": today["tickets"],
        "cents": today["cents"],
        "pastry_qty": t_pastry_q,
        "pastry_cents": t_pastry_c,
        "drink_qty": t_drink_q,
        "drink_cents": t_drink_c,
        "boba_modifiers": 0,
    }
    last_tot = {
        "tickets": week["tickets"],
        "cents": week["cents"],
        "pastry_qty": l_pastry_q,
        "pastry_cents": l_pastry_c,
        "drink_qty": l_drink_q,
        "drink_cents": l_drink_c,
        "boba_modifiers": 0,
    }
    this_drinks = {n: q for n, q, _c in today["top_items"] if is_drink(n)}
    last_drinks = {n: q for n, q, _c in week["top_items"] if is_drink(n)}
    extras = sorted(
        {*(this_drinks.keys()), *(last_drinks.keys())} - set(DRINK_MIX_ORDER),
        key=str.casefold,
    )
    drink_mix = []
    for name in list(DRINK_MIX_ORDER) + extras:
        this_n = _named_qty(this_drinks, name)
        last_n = _named_qty(last_drinks, name)
        drink_mix.append({"name": name, "last": last_n, "this": this_n, "delta": this_n - last_n})
    last_top_map = _qty_map(week["top_items"])
    top = []
    for rank, (name, qty, _cents) in enumerate(today["top_items"][:10], start=1):
        last_n = _named_qty(last_top_map, name)
        top.append({"rank": rank, "name": name, "last": last_n, "this": qty, "delta": qty - last_n})
    this_viet = _named_qty(this_drinks, VIET_NAME)
    last_viet = _named_qty(last_drinks, VIET_NAME)
    this_mango = _named_qty(_qty_map(today["top_items"]), MANGO_NAME)
    last_mango = _named_qty(last_top_map, MANGO_NAME)
    empty = today["tickets"] == 0 and week["tickets"] == 0 and not today["top_items"] and not week["top_items"]
    return {
        "empty": empty,
        "this_on": this_on,
        "last_on": last_on,
        "this_heading": "Today",
        "last_heading": "Week to date",
        "this_label": f"Today, {this_on.strftime('%b')} {this_on.day}, {this_on.year}",
        "last_label": f"Week to date from {last_on.strftime('%b')} {last_on.day}, {last_on.year}",
        "this_iso": this_on.isoformat(),
        "last_iso": last_on.isoformat(),
        "this": this_tot,
        "last": last_tot,
        "tickets_delta": this_tot["tickets"] - last_tot["tickets"],
        "tickets_pct": _pct(this_tot["tickets"], last_tot["tickets"]),
        "cents_delta": this_tot["cents"] - last_tot["cents"],
        "cents_pct": _pct(this_tot["cents"], last_tot["cents"]),
        "pastry_qty_delta": this_tot["pastry_qty"] - last_tot["pastry_qty"],
        "pastry_cents_delta": this_tot["pastry_cents"] - last_tot["pastry_cents"],
        "drink_qty_delta": this_tot["drink_qty"] - last_tot["drink_qty"],
        "drink_cents_delta": this_tot["drink_cents"] - last_tot["drink_cents"],
        "boba_delta": 0,
        "drink_mix": drink_mix,
        "top": top,
        "pair_sentence": pair_sentence(this_viet, this_mango, last_viet, last_mango, this_on),
        "csv_qs": "",
    }


@dataclass
class LiveMember:
    """Duck-types LoyaltyMember for member_view / stats / CSV. Not persisted."""

    id: str
    square_loyalty_id: str = ""
    square_customer_id: str = ""
    given_name: str = ""
    family_name: str = ""
    phone: str = ""
    email: str = ""
    points: int = 0
    lifetime_points: int = 0
    enrolled_at: datetime | None = None
    updated_at: datetime | None = None
    visits: int = 0
    last_visit_at: datetime | None = None
    first_visit_at: datetime | None = None
    lifetime_cents: int = 0
    favorite_item: str = ""
    favorite_drink: str = ""
    favorite_modifier: str = ""
    zip_code: str = ""
    creation_source: str = "getreports"
    email_unsubscribed: int = 0
    segments_json: str = "[]"
    notes: str = ""
    status: str = "active"
    area_code: str = ""
    area_metro: str = ""
    area_state: str = ""
    area_region: str = "unknown"


_MEMBER_LIST_KEYS = (
    "members",
    "accounts",
    "loyalty_accounts",
    "loyaltyAccounts",
    "items",
    "rows",
)
_WRAPPER_KEYS = ("loyalty", "data", "result", "payload", "body")
_ID_KEYS = ("id", "loyalty_account_id", "loyaltyAccountId", "account_id", "accountId")
_PHONE_KEYS = ("phone", "phone_number", "phoneNumber")
_POINTS_KEYS = ("points", "balance")
_CUSTOMER_KEYS = ("customer_id", "customerId")


def _maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw or raw[0] not in "{[":
        return value
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return value


def _pick(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    lowered = {str(k).casefold(): v for k, v in row.items()}
    for key in keys:
        got = lowered.get(key.casefold())
        if got not in (None, ""):
            return got
    return None


def _phone_from_row(row: dict[str, Any]) -> str:
    phone = _pick(row, _PHONE_KEYS)
    if phone not in (None, ""):
        return str(phone).strip()
    mapping = row.get("mapping")
    if isinstance(mapping, dict):
        phone = _pick(mapping, _PHONE_KEYS + ("value",))
        if phone not in (None, ""):
            return str(phone).strip()
    mappings = row.get("mappings")
    if isinstance(mappings, list):
        for item in mappings:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "").strip().upper()
            if kind == "PHONE" or _pick(item, _PHONE_KEYS):
                phone = _pick(item, _PHONE_KEYS + ("value",))
                if phone not in (None, ""):
                    return str(phone).strip()
    return ""


def _looks_like_member(row: dict[str, Any]) -> bool:
    return _pick(row, _ID_KEYS + _PHONE_KEYS + _POINTS_KEYS + _CUSTOMER_KEYS) is not None


def _rows_from_value(value: Any) -> list[dict[str, Any]] | None:
    value = _maybe_json(value)
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if not isinstance(value, dict) or not value:
        return None
    vals = list(value.values())
    map_of_dicts = bool(vals) and all(isinstance(v, dict) for v in vals)
    if map_of_dicts and not _looks_like_member(value):
        out: list[dict[str, Any]] = []
        for key, row in value.items():
            item = dict(row)
            if _pick(item, _ID_KEYS + _CUSTOMER_KEYS) in (None, ""):
                item["id"] = str(key)
            out.append(item)
        return out
    if _looks_like_member(value):
        return [value]
    return None


def _walk_blocks(payload: Any) -> list[dict[str, Any]]:
    root = _maybe_json(payload)
    if isinstance(root, list):
        return []
    if not isinstance(root, dict):
        return []
    out: list[dict[str, Any]] = [root]
    seen = {id(root)}
    i = 0
    while i < len(out):
        block = out[i]
        i += 1
        for key in _WRAPPER_KEYS:
            inner = _maybe_json(block.get(key))
            if isinstance(inner, dict) and id(inner) not in seen:
                seen.add(id(inner))
                out.append(inner)
    return out


def _member_list_present(block: dict[str, Any]) -> bool:
    for key in _MEMBER_LIST_KEYS:
        got = _maybe_json(block.get(key))
        if isinstance(got, (list, dict)) and got:
            return True
    loyalty = _maybe_json(block.get("loyalty"))
    return isinstance(loyalty, list) and bool(loyalty)


def _member_rows(payload: Any) -> list[dict[str, Any]]:
    """Glenn shape is loyalty.members; also accept top-level / data / accounts wraps."""
    root = _maybe_json(payload)
    if isinstance(root, list):
        return [row for row in root if isinstance(row, dict)]
    for block in _walk_blocks(root):
        if block.get("gated") and not _member_list_present(block):
            continue
        for key in _MEMBER_LIST_KEYS:
            rows = _rows_from_value(block.get(key))
            if rows:
                return rows
        loyalty = _maybe_json(block.get("loyalty"))
        if isinstance(loyalty, list):
            rows = _rows_from_value(loyalty)
            if rows:
                return rows
    return []


def members_from_payload(payload: Any) -> list[LiveMember]:
    rows = _member_rows(payload)
    out: list[LiveMember] = []
    for index, row in enumerate(rows):
        mid = _pick(row, _ID_KEYS)
        if mid in (None, ""):
            mid = _pick(row, _CUSTOMER_KEYS)
        mid_s = str(mid).strip() if mid not in (None, "") else f"row-{index}"
        phone = _phone_from_row(row)
        geo = lookup_phone(phone)
        cust = _pick(row, _CUSTOMER_KEYS)
        out.append(
            LiveMember(
                id=mid_s,
                square_loyalty_id=mid_s,
                square_customer_id=str(cust or "").strip(),
                phone=phone,
                points=_int(_pick(row, _POINTS_KEYS)),
                area_code=geo["area_code"],
                area_metro=geo["area_metro"],
                area_state=geo["area_state"],
                area_region=geo["area_region"],
            )
        )
    return out


def filter_members(
    members: list[LiveMember],
    q: str = "",
    segment: str = "all",
    sort: str = "points",
    direction: str = "desc",
    now: datetime | None = None,
) -> list[LiveMember]:
    from app.loyalty import compute_status, days_since, display_name, utc_now

    now = now or utc_now()
    term = (q or "").strip().casefold()
    rows = list(members)
    if term:
        matched: list[LiveMember] = []
        for m in rows:
            blob = " ".join(
                [
                    display_name(m),
                    m.phone or "",
                    m.email or "",
                    str(m.id),
                ]
            ).casefold()
            if term in blob:
                matched.append(m)
        rows = matched
    segment = (segment or "all").strip().lower()
    if segment not in ("", "all"):
        kept: list[LiveMember] = []
        for m in rows:
            status = compute_status(m, now)
            last_days = days_since(m.last_visit_at, now)
            has_email = bool((m.email or "").strip())
            has_phone = bool((m.phone or "").strip())
            ok = False
            if segment == "active":
                ok = m.last_visit_at is not None and (last_days or 0) <= 30
            elif segment == "lapsed":
                ok = status == "lapsed"
            elif segment == "high":
                ok = int(m.points or 0) >= 100
            elif segment == "never":
                ok = status == "never_purchased"
            elif segment == "email":
                ok = has_email
            elif segment == "phone":
                ok = has_phone and not has_email
            elif segment == "local":
                ok = m.area_region == "local"
            elif segment == "alabama":
                ok = m.area_region == "alabama"
            elif segment in ("out_of_state", "out"):
                ok = m.area_region == "out_of_state"
            elif segment in ("unknown", "unknown_phone"):
                ok = m.area_region == "unknown"
            if ok:
                kept.append(m)
        rows = kept
    sort = (sort or "points").strip().lower()
    descending = (direction or "desc").strip().lower() != "asc"

    def key(m: LiveMember):
        if sort == "lifetime":
            return (int(m.lifetime_points or 0), str(m.id))
        if sort == "last_visit":
            return (m.last_visit_at or datetime.min, str(m.id))
        if sort == "spend":
            return (int(m.lifetime_cents or 0), str(m.id))
        if sort == "name":
            return ((m.family_name or "").casefold(), (m.given_name or "").casefold(), str(m.id))
        if sort == "enrolled":
            return (m.enrolled_at or datetime.min, str(m.id))
        return (int(m.points or 0), str(m.id))

    rows.sort(key=key, reverse=descending)
    return rows


def live_reports_context(payload: Any | None = None) -> dict[str, Any] | None:
    raw = payload if payload is not None else fetch_sales()
    if raw is None:
        return None
    return reports_from_payload(raw)


def live_scorecard(payload: Any | None = None) -> dict[str, Any] | None:
    raw = payload if payload is not None else fetch_sales()
    if raw is None:
        return None
    return scorecard_from_payload(raw)


def live_members(payload: Any | None = None) -> list[LiveMember] | None:
    """Parse-and-render. None when URL unset. [] when gated, no key, 401, or fetch error."""
    if payload is not None:
        return members_from_payload(payload)
    url = (os.environ.get("GETREPORTS_URL") or "").strip()
    if not url:
        return None
    key = (os.environ.get("INGEST_KEY") or "").strip()
    if not key:
        return []
    raw = fetch_loyalty(key)
    if raw is None:
        return None
    return members_from_payload(raw)
