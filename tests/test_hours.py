"""Opening-hours gate. Square and the clock are mocked. No live Square calls."""

from __future__ import annotations

import logging
import os
import tempfile
import time

if "BAKERY_DB" not in os.environ:
    _fd, _db = tempfile.mkstemp(suffix=".db")
    os.close(_fd)
    os.environ["BAKERY_DB"] = _db

from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import hours as hours_svc  # noqa: E402
from app import order as order_svc  # noqa: E402
from app.main import app  # noqa: E402
from tests.fixtures.sample_catalog import (  # noqa: E402
    IRONDALE,
    ML_MILK,
    ML_SAUCE,
    ML_SWEET,
    MOD,
    VAR,
    CatalogFakeClient,
)

CHICAGO = ZoneInfo("America/Chicago")
TOKEN = "sandbox-test-token-not-real"
client = TestClient(app)

ALL_DAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]

CATALOG_CART = {
    "name": "Ronald",
    "pickup": "to-go",
    "phone": "2055550100",
    "items": [
        {
            "id": VAR["viet"],
            "qty": 1,
            "modifiers": {
                ML_MILK: MOD["condensed"],
                ML_SWEET: MOD["sweet-normal"],
                ML_SAUCE: MOD["sauce-caramel"],
            },
        }
    ],
}


def setup_function() -> None:
    hours_svc.clear_hours_cache()


def teardown_function() -> None:
    hours_svc.clear_hours_cache()


def chicago(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=CHICAGO)


def periods_for(days: list[str], start: str = "09:00:00", end: str = "17:00:00") -> list[dict]:
    return [
        {"day_of_week": day, "start_local_time": start, "end_local_time": end}
        for day in days
    ]


def location_body(
    periods: list[dict],
    *,
    timezone: str = "America/Chicago",
    location_id: str = IRONDALE,
) -> dict:
    return {
        "location": {
            "id": location_id,
            "timezone": timezone,
            "business_hours": {"periods": periods},
        }
    }


def week_body() -> dict:
    return location_body(periods_for(ALL_DAYS))


def status_at(when: datetime, periods: list[dict] | None = None, **kwargs) -> hours_svc.ShopStatus:
    body = location_body(periods if periods is not None else periods_for(ALL_DAYS), **kwargs)
    schedule = hours_svc.parse_location(body, location_id=kwargs.get("location_id", IRONDALE))
    return hours_svc.status_for_schedule(schedule, now=when)


class Resp:
    def __init__(self, status_code: int, body: dict | None):
        self.status_code = status_code
        self._body = body
        self.content = b"{}" if body is not None else b""

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class ApiClient:
    """Catalog fake plus a Locations GET. Records every call. Never talks to Square."""

    def __init__(self, *, location_body: dict | None = None, location_status: int = 200, location_exc: Exception | None = None):
        self.location_body = location_body if location_body is not None else {}
        self.location_status = location_status
        self.location_exc = location_exc
        self.gets: list[tuple[str, dict | None]] = []
        self.posts: list[str] = []
        self.inner = CatalogFakeClient(
            payment_link={
                "id": "LINKFAKE",
                "order_id": "ORDERFAKE123456789",
                "url": "https://square.link/u/fake",
            }
        )

    def get(self, url, headers=None, params=None):
        self.gets.append((url, headers))
        if "/v2/locations/" in url:
            if self.location_exc is not None:
                raise self.location_exc
            return Resp(self.location_status, self.location_body)
        return self.inner.get(url, headers=headers, params=params)

    def post(self, url, headers=None, json=None):
        self.posts.append(url)
        return self.inner.post(url, headers=headers, json=json)

    def close(self):
        pass


def _square_env(monkeypatch) -> None:
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("SQUARE_LOCATION_ID_IRONDALE", IRONDALE)
    monkeypatch.setenv("BAKERY_SERVICE", "drinks")
    monkeypatch.setenv("HOURS_CACHE_SECONDS", "600")


def test_open_during_business_hours() -> None:
    state = status_at(chicago(2026, 10, 6, 10, 0))
    assert state.open_now is True
    assert state.next_open is None
    assert state.hours is not None
    assert state.hours["timezone"] == "America/Chicago"
    assert state.hours["location_id"] == IRONDALE
    tuesday = next(day for day in state.hours["days"] if day["day_of_week"] == "TUE")
    assert tuesday["periods"] == [{"start_local_time": "09:00:00", "end_local_time": "17:00:00"}]


def test_open_at_exactly_opening_and_closed_at_exactly_closing() -> None:
    assert status_at(chicago(2026, 10, 6, 9, 0)).open_now is True
    assert status_at(chicago(2026, 10, 6, 16, 59)).open_now is True
    closed = status_at(chicago(2026, 10, 6, 17, 0))
    assert closed.open_now is False
    assert closed.next_open == "2026-10-07T09:00:00-05:00"
    assert closed.message == "We're closed right now. We open at 9:00 AM tomorrow."


def test_before_opening_names_today() -> None:
    state = status_at(chicago(2026, 10, 6, 8, 0))
    assert state.open_now is False
    assert state.next_open == "2026-10-06T09:00:00-05:00"
    assert state.message == "We're closed right now. We open at 9:00 AM today."


def test_after_closing_names_tomorrow() -> None:
    state = status_at(chicago(2026, 10, 6, 18, 30))
    assert state.open_now is False
    assert state.next_open == "2026-10-07T09:00:00-05:00"
    assert state.message == "We're closed right now. We open at 9:00 AM tomorrow."


def test_closed_day_skips_to_the_next_open_day() -> None:
    periods = periods_for(["MON", "TUE", "WED", "THU", "FRI", "SAT"])
    saturday_night = status_at(chicago(2026, 10, 10, 18, 0), periods)
    assert saturday_night.open_now is False
    assert saturday_night.next_open == "2026-10-12T09:00:00-05:00"
    assert saturday_night.message == "We're closed right now. We open at 9:00 AM Monday."
    sunday = next(day for day in saturday_night.hours["days"] if day["day_of_week"] == "SUN")
    assert sunday["periods"] == []

    sunday_afternoon = status_at(chicago(2026, 10, 11, 15, 0), periods)
    assert sunday_afternoon.open_now is False
    assert sunday_afternoon.message == "We're closed right now. We open at 9:00 AM tomorrow."
    assert sunday_afternoon.next_open == "2026-10-12T09:00:00-05:00"


def test_multiple_periods_and_unsorted_square_payload() -> None:
    split = []
    for day in ALL_DAYS:
        split.append({"day_of_week": day, "start_local_time": "13:00:00", "end_local_time": "17:00:00"})
        split.append({"day_of_week": day, "start_local_time": "09:00:00", "end_local_time": "12:00:00"})
    assert status_at(chicago(2026, 10, 6, 11, 0), split).open_now is True
    gap = status_at(chicago(2026, 10, 6, 12, 30), split)
    assert gap.open_now is False
    assert gap.next_open == "2026-10-06T13:00:00-05:00"
    assert gap.message == "We're closed right now. We open at 1:00 PM today."
    assert status_at(chicago(2026, 10, 6, 13, 0), split).open_now is True
    assert status_at(chicago(2026, 10, 6, 12, 0), split).open_now is False
    after = status_at(chicago(2026, 10, 6, 17, 0), split)
    assert after.next_open == "2026-10-07T09:00:00-05:00"
    assert after.message == "We're closed right now. We open at 9:00 AM tomorrow."


def test_weekday_mapping_uses_square_day_names() -> None:
    tuesday_only = periods_for(["TUE"])
    assert status_at(chicago(2026, 10, 6, 10, 0), tuesday_only).open_now is True
    wednesday = status_at(chicago(2026, 10, 7, 10, 0), tuesday_only)
    assert wednesday.open_now is False
    assert wednesday.next_open == "2026-10-13T09:00:00-05:00"
    assert wednesday.message == "We're closed right now. We open at 9:00 AM Tuesday."
    sunday_only = periods_for(["SUN"])
    assert status_at(chicago(2026, 10, 11, 10, 0), sunday_only).open_now is True


def test_dst_offsets_follow_america_chicago() -> None:
    # Same UTC clock is open in summer (CDT) and still before open in winter (CST).
    summer = status_at(datetime(2026, 7, 15, 14, 30, tzinfo=ZoneInfo("UTC")))
    winter = status_at(datetime(2026, 1, 15, 14, 30, tzinfo=ZoneInfo("UTC")))
    assert summer.open_now is True
    assert winter.open_now is False
    assert winter.next_open == "2026-01-15T09:00:00-06:00"
    assert winter.message == "We're closed right now. We open at 9:00 AM today."

    before_fall_back = status_at(chicago(2026, 10, 31, 8, 0))
    after_fall_back = status_at(chicago(2026, 11, 1, 8, 0))
    spring = status_at(chicago(2026, 3, 8, 8, 0))
    assert before_fall_back.next_open == "2026-10-31T09:00:00-05:00"
    assert after_fall_back.next_open == "2026-11-01T09:00:00-06:00"
    assert spring.next_open == "2026-03-08T09:00:00-05:00"


def test_empty_periods_are_closed_with_a_message() -> None:
    state = status_at(chicago(2026, 10, 6, 10, 0), [])
    assert state.open_now is False
    assert state.next_open is None
    assert state.message == "We're closed right now."
    assert state.hours is not None


def test_missing_business_hours_is_unreadable() -> None:
    with pytest.raises(hours_svc.HoursReadError):
        hours_svc.parse_location({"location": {"id": IRONDALE, "timezone": "America/Chicago"}}, location_id=IRONDALE)


def test_unknown_timezone_falls_back_to_chicago() -> None:
    state = status_at(
        chicago(2026, 10, 6, 8, 0),
        timezone="Not/AZone",
    )
    assert state.hours["timezone"] == "America/Chicago"
    assert state.next_open == "2026-10-06T09:00:00-05:00"


def test_checkout_while_open_creates_one_payment_link(monkeypatch) -> None:
    _square_env(monkeypatch)
    monkeypatch.setattr(hours_svc, "utc_now", lambda: chicago(2026, 10, 6, 10, 0))
    fake = ApiClient(location_body=week_body())
    result = order_svc.checkout(CATALOG_CART, origin="https://drinks.example", client=fake)
    assert result["url"] == "https://square.link/u/fake"
    assert result["demo"] is False
    assert len(fake.gets) == 1
    assert fake.gets[0][0].endswith(f"/v2/locations/{IRONDALE}")
    assert fake.gets[0][1]["Authorization"] == f"Bearer {TOKEN}"
    assert fake.gets[0][1]["Square-Version"]
    assert sum("payment-links" in url for url in fake.posts) == 1
    assert fake.posts[-1].endswith("/v2/online-checkout/payment-links")
    assert not any(url.endswith("/v2/orders") for url in fake.posts)

    clock = {"now": chicago(2026, 10, 6, 18, 0)}
    monkeypatch.setattr(hours_svc, "utc_now", lambda: clock["now"])
    with pytest.raises(order_svc.ClosedError) as raised:
        order_svc.checkout(CATALOG_CART, origin="https://drinks.example", client=fake)
    assert raised.value.payload["message"] == "We're closed right now. We open at 9:00 AM tomorrow."
    assert len(fake.gets) == 1
    assert sum("payment-links" in url for url in fake.posts) == 1


def test_checkout_route_409_before_opening_creates_nothing(monkeypatch) -> None:
    _square_env(monkeypatch)
    monkeypatch.setattr(hours_svc, "utc_now", lambda: chicago(2026, 10, 6, 8, 0))
    fake = ApiClient(location_body=week_body())
    monkeypatch.setattr(hours_svc.httpx, "Client", lambda *args, **kwargs: fake)

    def _no_cart(*args, **kwargs):
        raise AssertionError("cart was validated while the shop was closed")

    def _no_link(*args, **kwargs):
        raise AssertionError("payment link was created while the shop was closed")

    monkeypatch.setattr(order_svc, "validate_cart", _no_cart)
    monkeypatch.setattr(order_svc, "create_payment_link", _no_link)
    res = client.post("/order/api/checkout", json={"name": "Ronald", "items": []})
    assert res.status_code == 409
    assert res.json() == {
        "ok": False,
        "error": "closed",
        "message": "We're closed right now. We open at 9:00 AM today.",
        "next_open": "2026-10-06T09:00:00-05:00",
    }
    assert fake.posts == []
    assert len(fake.gets) == 1


def test_checkout_route_409_after_closing(monkeypatch) -> None:
    _square_env(monkeypatch)
    monkeypatch.setattr(hours_svc, "utc_now", lambda: chicago(2026, 10, 6, 17, 0))
    fake = ApiClient(location_body=week_body())
    monkeypatch.setattr(hours_svc.httpx, "Client", lambda *args, **kwargs: fake)
    res = client.post("/order/api/checkout", json={"name": "Ronald", "items": []})
    assert res.status_code == 409
    body = res.json()
    assert body["ok"] is False
    assert body["error"] == "closed"
    assert body["message"] == "We're closed right now. We open at 9:00 AM tomorrow."
    assert body["next_open"] == "2026-10-07T09:00:00-05:00"
    assert fake.posts == []


def test_square_read_failure_fails_open_and_logs_without_the_token(monkeypatch, caplog) -> None:
    _square_env(monkeypatch)
    monkeypatch.setattr(hours_svc, "utc_now", lambda: chicago(2026, 10, 6, 23, 0))
    fake = ApiClient(location_exc=httpx.ConnectTimeout("timed out"))
    caplog.set_level(logging.WARNING, logger="app.hours")
    result = order_svc.checkout(CATALOG_CART, origin="https://drinks.example", client=fake)
    assert result["order_id"] == "ORDERFAKE123456789"
    assert sum("payment-links" in url for url in fake.posts) == 1
    assert "failing open" in caplog.text
    assert TOKEN not in caplog.text
    assert "SQUARE_ACCESS_TOKEN" not in caplog.text


def test_http_error_without_cache_fails_open(monkeypatch) -> None:
    _square_env(monkeypatch)
    monkeypatch.setattr(hours_svc, "utc_now", lambda: chicago(2026, 10, 6, 23, 0))
    fake = ApiClient(location_status=500, location_body={"errors": [{"code": "INTERNAL_SERVER_ERROR"}]})
    result = order_svc.checkout(CATALOG_CART, origin="https://drinks.example", client=fake)
    assert result["demo"] is False
    assert sum("payment-links" in url for url in fake.posts) == 1


def test_missing_business_hours_fails_open(monkeypatch) -> None:
    _square_env(monkeypatch)
    monkeypatch.setattr(hours_svc, "utc_now", lambda: chicago(2026, 10, 6, 23, 0))
    fake = ApiClient(location_body={"location": {"id": IRONDALE, "timezone": "America/Chicago"}})
    result = order_svc.checkout(CATALOG_CART, origin="https://drinks.example", client=fake)
    assert result["url"] == "https://square.link/u/fake"


def test_stale_cache_is_enforced_when_refetch_fails(monkeypatch) -> None:
    _square_env(monkeypatch)
    start = time.time()
    clock = {"t": start, "now": chicago(2026, 10, 6, 10, 0)}
    monkeypatch.setattr(hours_svc.time, "time", lambda: clock["t"])
    monkeypatch.setattr(hours_svc, "utc_now", lambda: clock["now"])
    prime = ApiClient(location_body=week_body())
    opened = order_svc.checkout(CATALOG_CART, origin="https://drinks.example", client=prime)
    assert opened["demo"] is False
    assert len(prime.gets) == 1

    clock["t"] = start + 601
    clock["now"] = chicago(2026, 10, 6, 18, 0)
    failing = ApiClient(location_exc=httpx.ReadTimeout("timed out"))
    with pytest.raises(order_svc.ClosedError) as raised:
        order_svc.checkout(CATALOG_CART, origin="https://drinks.example", client=failing)
    assert raised.value.status_code == 409
    assert raised.value.payload["error"] == "closed"
    assert raised.value.payload["next_open"] == "2026-10-07T09:00:00-05:00"
    assert failing.posts == []
    assert len(failing.gets) == 1

    again = ApiClient(location_exc=httpx.ReadTimeout("timed out"))
    with pytest.raises(order_svc.ClosedError):
        order_svc.checkout(CATALOG_CART, origin="https://drinks.example", client=again)
    assert again.gets == []
    assert again.posts == []


def test_failure_without_cache_is_not_retried_during_backoff(monkeypatch) -> None:
    _square_env(monkeypatch)
    start = time.time()
    clock = {"t": start}
    monkeypatch.setattr(hours_svc.time, "time", lambda: clock["t"])
    calls = {"n": 0}

    class Boom:
        def get(self, url, headers=None, params=None):
            calls["n"] += 1
            raise httpx.ConnectTimeout("timed out")

        def close(self):
            pass

    kwargs = {
        "token": TOKEN,
        "location_id": IRONDALE,
        "api_base": "https://connect.squareup.com",
        "client": Boom(),
    }
    assert hours_svc.load_schedule(**kwargs) is None
    assert hours_svc.load_schedule(**kwargs) is None
    assert calls["n"] == 1
    clock["t"] = start + hours_svc.FAILURE_BACKOFF_SECONDS + 1
    assert hours_svc.load_schedule(**kwargs) is None
    assert calls["n"] == 2


def test_other_location_cache_is_not_reused(monkeypatch) -> None:
    _square_env(monkeypatch)
    monkeypatch.setattr(hours_svc, "utc_now", lambda: chicago(2026, 10, 6, 23, 0))
    ok = ApiClient(location_body=week_body())
    hours_svc.current_status(
        token=TOKEN,
        location_id=IRONDALE,
        api_base="https://connect.squareup.com",
        client=ok,
    )
    other = ApiClient(location_exc=httpx.ConnectTimeout("timed out"))
    state = hours_svc.current_status(
        token=TOKEN,
        location_id="OTHERLOC",
        api_base="https://connect.squareup.com",
        client=other,
    )
    assert state.hours is None
    assert state.open_now is True
    assert len(other.gets) == 1
    assert other.gets[0][0].endswith("/v2/locations/OTHERLOC")


def test_hours_route_open_and_closed(monkeypatch) -> None:
    _square_env(monkeypatch)
    clock = {"now": chicago(2026, 10, 6, 10, 0)}
    monkeypatch.setattr(hours_svc, "utc_now", lambda: clock["now"])
    fake = ApiClient(location_body=week_body())
    monkeypatch.setattr(hours_svc.httpx, "Client", lambda *args, **kwargs: fake)
    opened = client.get("/order/api/hours")
    assert opened.status_code == 200
    body = opened.json()
    assert set(body) == {"open_now", "next_open", "hours"}
    assert body["open_now"] is True
    assert body["next_open"] is None
    assert body["hours"]["timezone"] == "America/Chicago"
    assert [day["day_of_week"] for day in body["hours"]["days"]] == ALL_DAYS
    assert len(fake.gets) == 1
    assert fake.posts == []

    clock["now"] = chicago(2026, 10, 6, 20, 0)
    closed = client.get("/order/api/hours")
    assert closed.status_code == 200
    closed_body = closed.json()
    assert closed_body["open_now"] is False
    assert closed_body["next_open"] == "2026-10-07T09:00:00-05:00"
    assert closed_body["hours"]["location_id"] == IRONDALE
    assert len(fake.gets) == 1


def test_hours_route_without_token_does_not_call_square(monkeypatch) -> None:
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("BAKERY_SERVICE", "laptop")

    def _no_client(*args, **kwargs):
        raise AssertionError("hours route opened an HTTP client without a token")

    monkeypatch.setattr(hours_svc.httpx, "Client", _no_client)
    res = client.get("/order/api/hours")
    assert res.status_code == 200
    assert res.json() == {"open_now": True, "next_open": None, "hours": None}


def test_explicit_closed_week_blocks_checkout(monkeypatch) -> None:
    _square_env(monkeypatch)
    monkeypatch.setattr(hours_svc, "utc_now", lambda: chicago(2026, 10, 6, 10, 0))
    fake = ApiClient(location_body=location_body([]))
    with pytest.raises(order_svc.ClosedError) as raised:
        order_svc.checkout(CATALOG_CART, origin="https://drinks.example", client=fake)
    assert raised.value.payload == {
        "ok": False,
        "error": "closed",
        "message": "We're closed right now.",
        "next_open": None,
    }
    assert fake.posts == []
