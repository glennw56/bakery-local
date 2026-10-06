"""Irondale opening hours from the Square location (read-only).

Checkout calls this before CreatePaymentLink. Hours are the location's
``business_hours`` periods, interpreted in the location timezone (America/Chicago
for Irondale). A successful read is cached for about 10 minutes. The cached
value is the weekly schedule, not a frozen open/closed bit, so the clock is
applied on every request.

Failure policy is fail-open when nothing usable is cached: a Locations API
timeout must not take the register offline. If a previous good schedule is
cached, that schedule is used even after the freshness window. A failed read
is not retried for a short backoff so one hung request cannot add a timeout
to every following checkout.

No writes. GET /v2/locations/{id} only.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote
from datetime import date, datetime, time as clock_time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.catalog import SQUARE_VERSION

log = logging.getLogger(__name__)

DAYS = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")
_DAY_SET = frozenset(DAYS)
DEFAULT_TIMEZONE = "America/Chicago"
FETCH_TIMEOUT_SECONDS = 8.0
FAILURE_BACKOFF_SECONDS = 60.0

_lock = threading.Lock()
_cache: "CacheSlot | None" = None


class HoursReadError(Exception):
    """Locations hours could not be read. Never includes the access token."""


@dataclass(frozen=True)
class Period:
    day: str
    start: clock_time
    end: clock_time
    end_next_day: bool = False


@dataclass(frozen=True)
class Schedule:
    location_id: str
    timezone: str
    periods: tuple[Period, ...]


@dataclass
class CacheSlot:
    location_id: str
    schedule: Schedule | None
    fresh_until: float
    retry_after: float


@dataclass
class ShopStatus:
    open_now: bool
    next_open: str | None
    hours: dict[str, Any] | None
    message: str

    def as_public(self) -> dict[str, Any]:
        return {
            "open_now": self.open_now,
            "next_open": self.next_open,
            "hours": self.hours,
        }


def cache_ttl_seconds() -> int:
    raw = (os.environ.get("HOURS_CACHE_SECONDS") or "600").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 600


def clear_hours_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def current_status(
    *,
    token: str,
    location_id: str,
    api_base: str,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> ShopStatus:
    schedule = load_schedule(
        token=token,
        location_id=location_id,
        api_base=api_base,
        client=client,
    )
    if schedule is None:
        return ShopStatus(open_now=True, next_open=None, hours=None, message="")
    return status_for_schedule(schedule, now=now)


def load_schedule(
    *,
    token: str,
    location_id: str,
    api_base: str,
    client: httpx.Client | None = None,
) -> Schedule | None:
    """Return a schedule, or None when hours are unknown (caller fail-opens)."""
    token = (token or "").strip()
    location_id = (location_id or "").strip()
    if not token or not location_id:
        return None

    now_ts = time.time()
    with _lock:
        slot = _cache if _cache is not None and _cache.location_id == location_id else None
        if slot is not None and slot.schedule is not None and now_ts < slot.fresh_until:
            return slot.schedule
        if slot is not None and now_ts < slot.retry_after:
            return slot.schedule

    try:
        schedule = _fetch_schedule(
            token=token,
            location_id=location_id,
            api_base=api_base,
            client=client,
        )
    except HoursReadError as exc:
        _remember_failure(location_id, str(exc))
        return _cached_schedule(location_id)

    _remember_success(schedule)
    return schedule


def status_for_schedule(schedule: Schedule, *, now: datetime | None = None) -> ShopStatus:
    moment = now or utc_now()
    open_now, next_open = evaluate(schedule, moment)
    message = ""
    next_iso: str | None = None
    if not open_now:
        if next_open is None:
            message = "We're closed right now."
        else:
            local_now = _as_local(moment, schedule.timezone)
            friendly = _friendly_when(next_open, local_now)
            message = f"We're closed right now. We open at {friendly}."
            next_iso = next_open.isoformat(timespec="seconds")
    return ShopStatus(
        open_now=open_now,
        next_open=next_iso,
        hours=_public_hours(schedule),
        message=message,
    )


def evaluate(schedule: Schedule, now: datetime) -> tuple[bool, datetime | None]:
    """Return (open_now, next_open_local). next_open is None when open or unknown."""
    tz = ZoneInfo(schedule.timezone)
    local_now = _as_local(now, schedule.timezone)
    by_day = _periods_by_day(schedule)
    for offset in range(8):
        day = local_now.date() + timedelta(days=offset)
        for period in by_day.get(_square_day(day), []):
            start = datetime.combine(day, period.start, tzinfo=tz)
            end_day = day + timedelta(days=1) if period.end_next_day else day
            end = datetime.combine(end_day, period.end, tzinfo=tz)
            if end <= start:
                continue
            if start <= local_now < end:
                return True, None
            if start > local_now:
                return False, start
    return False, None


def parse_location(body: Any, *, location_id: str) -> Schedule:
    if not isinstance(body, dict):
        raise HoursReadError("location response was not an object")
    loc = body.get("location")
    if not isinstance(loc, dict):
        raise HoursReadError("location response missing location")
    business = loc.get("business_hours")
    if not isinstance(business, dict) or "periods" not in business:
        raise HoursReadError("location has no business_hours")
    raw_periods = business.get("periods")
    if not isinstance(raw_periods, list):
        raise HoursReadError("business_hours.periods is not a list")

    periods: list[Period] = []
    for raw in raw_periods:
        if not isinstance(raw, dict):
            continue
        parsed = _parse_period(raw)
        if parsed is not None:
            periods.append(parsed)
    if raw_periods and not periods:
        raise HoursReadError("business_hours periods were unreadable")

    tz_name = _timezone_name(loc.get("timezone"), location_id=location_id)
    returned = str(loc.get("id") or "").strip()
    if returned and returned != location_id:
        log.warning(
            "Square location id %s did not match requested %s",
            returned,
            location_id,
        )
    return Schedule(location_id=location_id, timezone=tz_name, periods=tuple(periods))


def _fetch_schedule(
    *,
    token: str,
    location_id: str,
    api_base: str,
    client: httpx.Client | None,
) -> Schedule:
    url = f"{api_base.rstrip('/')}/v2/locations/{quote(location_id, safe='')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Square-Version": SQUARE_VERSION,
        "Accept": "application/json",
    }
    own = client is None
    http = client or httpx.Client(timeout=FETCH_TIMEOUT_SECONDS)
    try:
        try:
            response = http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise HoursReadError(exc.__class__.__name__) from exc
        if response.status_code >= 400:
            raise HoursReadError(f"HTTP {response.status_code}")
        try:
            body = response.json()
        except Exception as exc:
            raise HoursReadError("invalid JSON") from exc
    finally:
        if own:
            http.close()
    return parse_location(body, location_id=location_id)


def _remember_success(schedule: Schedule) -> None:
    global _cache
    now_ts = time.time()
    ttl = cache_ttl_seconds()
    with _lock:
        _cache = CacheSlot(
            location_id=schedule.location_id,
            schedule=schedule,
            fresh_until=now_ts + ttl,
            retry_after=now_ts + ttl,
        )


def _remember_failure(location_id: str, reason: str) -> None:
    global _cache
    now_ts = time.time()
    with _lock:
        previous = _cache if _cache is not None and _cache.location_id == location_id else None
        kept = previous.schedule if previous is not None else None
        _cache = CacheSlot(
            location_id=location_id,
            schedule=kept,
            fresh_until=previous.fresh_until if previous is not None else 0.0,
            retry_after=now_ts + FAILURE_BACKOFF_SECONDS,
        )
    if kept is not None:
        log.warning(
            "Square business hours read failed for %s (%s); using cached hours",
            location_id,
            reason,
        )
    else:
        log.warning(
            "Square business hours read failed for %s (%s); no cached hours, failing open so checkout is allowed",
            location_id,
            reason,
        )


def _cached_schedule(location_id: str) -> Schedule | None:
    with _lock:
        slot = _cache
        if slot is None or slot.location_id != location_id:
            return None
        return slot.schedule


def _timezone_name(raw: Any, *, location_id: str) -> str:
    name = str(raw or "").strip()
    if not name:
        return DEFAULT_TIMEZONE
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning(
            "Location %s has unknown timezone %r; using %s",
            location_id,
            name,
            DEFAULT_TIMEZONE,
        )
        return DEFAULT_TIMEZONE
    return name


def _parse_period(raw: dict[str, Any]) -> Period | None:
    day = str(raw.get("day_of_week") or "").strip().upper()
    if day not in _DAY_SET:
        return None
    start = _parse_clock(raw.get("start_local_time"))
    end_raw = raw.get("end_local_time")
    end_next = isinstance(end_raw, str) and end_raw.strip() in ("24:00", "24:00:00")
    if end_next:
        end = clock_time(0, 0)
    else:
        end = _parse_clock(end_raw)
    if start is None or end is None:
        return None
    if not end_next and end <= start:
        return None
    return Period(day=day, start=start, end=end, end_next_day=end_next)


def _parse_clock(raw: Any) -> clock_time | None:
    if not isinstance(raw, str):
        return None
    parts = raw.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    if hour == 24 or not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return clock_time(hour, minute, second)


def _periods_by_day(schedule: Schedule) -> dict[str, list[Period]]:
    grouped: dict[str, list[Period]] = {day: [] for day in DAYS}
    for period in schedule.periods:
        grouped.setdefault(period.day, []).append(period)
    for day in grouped:
        grouped[day].sort(key=lambda period: (period.start, period.end))
    return grouped


def _public_hours(schedule: Schedule) -> dict[str, Any]:
    grouped = _periods_by_day(schedule)
    days = []
    for day in DAYS:
        periods = []
        for period in grouped.get(day, []):
            end_label = "24:00:00" if period.end_next_day else period.end.strftime("%H:%M:%S")
            periods.append(
                {
                    "start_local_time": period.start.strftime("%H:%M:%S"),
                    "end_local_time": end_label,
                }
            )
        days.append({"day_of_week": day, "periods": periods})
    return {
        "location_id": schedule.location_id,
        "timezone": schedule.timezone,
        "days": days,
    }


def _as_local(now: datetime, tz_name: str) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ZoneInfo(tz_name))


def _square_day(day: date) -> str:
    # date.weekday(): Monday is 0. Square's week starts on Sunday.
    return DAYS[(day.weekday() + 1) % 7]


def _friendly_when(when: datetime, local_now: datetime) -> str:
    clock = when.strftime("%I:%M %p")
    if clock.startswith("0"):
        clock = clock[1:]
    if when.date() == local_now.date():
        return f"{clock} today"
    if when.date() == local_now.date() + timedelta(days=1):
        return f"{clock} tomorrow"
    return f"{clock} {when.strftime('%A')}"
