"""Fixture-only getreports parser. No live Square token. Phones in fixtures are fake."""

from __future__ import annotations

import json
from pathlib import Path

from urllib.parse import parse_qs, urlparse

from app.getreports import (
    fetch_loyalty,
    fetch_sales,
    loyalty_url,
    members_from_payload,
    reports_from_payload,
    scorecard_from_payload,
)
from app.loyalty import member_view

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_getreports.json"
LOYALTY = Path(__file__).resolve().parent / "fixtures" / "sample_getreports_loyalty.json"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeClient:
    last_url = ""
    last_headers: dict = {}
    payload: object = None

    def __init__(self, timeout=None):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None):
        type(self).last_url = url
        type(self).last_headers = dict(headers or {})
        return _FakeResp(self.payload)


def test_reports_maps_today_and_week_top_items() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ctx = reports_from_payload(payload)
    assert ctx["today_tickets"] == 21
    assert ctx["today_cents"] == 46732
    assert len(ctx["summaries"]) == 2
    assert ctx["summaries"][0].note == "Today"
    assert ctx["summaries"][0].tickets == 21
    assert ctx["summaries"][1].note == "Week to date"
    assert ctx["summaries"][1].tickets == 46
    names = [row[0] for row in ctx["top_items"]]
    assert "Vietnamese Coffee" in names
    assert "Birthday Cake Macaron" in names
    assert ctx["modifiers_by_drink"] == {}
    assert ctx["merch"] == []
    # 2026-09-01 is a Tuesday → today counts as weekday
    assert ctx["weekday_tickets"] == 21
    assert ctx["weekend_tickets"] == 0
    today_ch = {row["name"]: row for row in ctx["today_channels"]}
    assert today_ch["Point of Sale"]["tickets"] == 19
    assert today_ch["Point of Sale"]["cents"] == 41158
    assert "dollars" not in today_ch["Point of Sale"]
    week_ch = {row["name"]: row for row in ctx["week_channels"]}
    assert week_ch["Uber Eats"]["cents"] == 1822


def test_live_reports_unset_returns_none(monkeypatch) -> None:
    from app import getreports

    monkeypatch.delenv("GETREPORTS_URL", raising=False)
    monkeypatch.delenv("INGEST_KEY", raising=False)
    assert getreports.fetch_sales() is None
    assert getreports.fetch_loyalty("unused") is None
    assert getreports.live_reports_context() is None
    assert getreports.live_scorecard() is None
    assert getreports.live_members() is None


def test_fetch_sales_mocks_httpx(monkeypatch) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    _FakeClient.payload = payload
    _FakeClient.last_url = ""
    _FakeClient.last_headers = {}
    monkeypatch.setenv("GETREPORTS_URL", "https://getreports.example.test")
    monkeypatch.delenv("INGEST_KEY", raising=False)
    monkeypatch.setattr("app.getreports.httpx.Client", _FakeClient)
    got = fetch_sales()
    assert got["ok"] is True
    assert got["sales"]["today"]["tickets"] == 21
    assert _FakeClient.last_url == "https://getreports.example.test"
    assert "X-Ingest-Key" not in _FakeClient.last_headers


def test_fetch_loyalty_sends_header_and_skips_live_key(monkeypatch) -> None:
    payload = json.loads(LOYALTY.read_text(encoding="utf-8"))
    _FakeClient.payload = payload
    _FakeClient.last_url = ""
    _FakeClient.last_headers = {}
    monkeypatch.setenv("GETREPORTS_URL", "https://getreports.example.test")
    monkeypatch.setattr("app.getreports.httpx.Client", _FakeClient)
    got = fetch_loyalty("test-not-live")
    assert got["loyalty"]["count"] == 2
    parsed = urlparse(_FakeClient.last_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "getreports.example.test"
    assert parsed.path.rstrip("/") == "/loyalty"
    assert parsed.path.rstrip("/") != ""
    assert parse_qs(parsed.query).get("loyalty") == ["1"]
    assert _FakeClient.last_headers == {"X-Ingest-Key": "test-not-live"}


def test_gated_loyalty_is_empty() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["loyalty"]["gated"] is True
    assert members_from_payload(payload) == []


def test_loyalty_maps_fake_phones_and_hometown() -> None:
    payload = json.loads(LOYALTY.read_text(encoding="utf-8"))
    rows = members_from_payload(payload)
    assert len(rows) == 2
    by_id = {r.id: r for r in rows}
    hi = by_id["LOY_FAKE_1"]
    assert hi.points == 150
    assert hi.phone == "+15555550100"
    assert hi.given_name == ""
    assert hi.email == ""
    assert hi.lifetime_points == 0
    assert hi.last_visit_at is None
    assert isinstance(hi.id, str)
    view = member_view(hi)
    assert view["id"] == "LOY_FAKE_1"
    assert view["points"] == 150
    local = by_id["LOY_FAKE_2"]
    assert local.phone == "+12055550100"
    assert local.area_code == "205"
    assert local.area_region == "local"
    from app.getreports import filter_members

    high = filter_members(rows, segment="high")
    assert [m.id for m in high] == ["LOY_FAKE_1"]
    local_rows = filter_members(rows, segment="local")
    assert [m.id for m in local_rows] == ["LOY_FAKE_2"]


def test_scorecard_today_vs_week_drink_mix() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    card = scorecard_from_payload(payload)
    assert card["empty"] is False
    assert card["this_heading"] == "Today"
    assert card["last_heading"] == "Week to date"
    assert card["this"]["tickets"] == 21
    assert card["last"]["tickets"] == 46
    mix = {row["name"]: row for row in card["drink_mix"]}
    assert mix["Vietnamese Coffee"]["this"] == 5
    assert mix["Vietnamese Coffee"]["last"] == 6
    top_names = [row["name"] for row in card["top"]]
    assert top_names[0] == "Vietnamese Coffee"
    assert "Vietnamese Coffee" in card["pair_sentence"]


def test_live_members_skips_fetch_without_key(monkeypatch) -> None:
    from app import getreports

    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("must not call /loyalty without a key")

    monkeypatch.setenv("GETREPORTS_URL", "https://getreports.example.test")
    monkeypatch.delenv("INGEST_KEY", raising=False)
    monkeypatch.setattr(getreports, "fetch_loyalty", boom)
    assert getreports.live_members() == []
    assert called["n"] == 0



def test_live_routes_parse_and_render(monkeypatch) -> None:
    import os
    import tempfile

    if "BAKERY_DB" not in os.environ:
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["BAKERY_DB"] = db

    from fastapi.testclient import TestClient

    from app.db import init_db
    from app.getreports import members_from_payload
    from app.main import app

    init_db()
    sales = json.loads(FIXTURE.read_text(encoding="utf-8"))
    loyalty = json.loads(LOYALTY.read_text(encoding="utf-8"))
    monkeypatch.setenv("GETREPORTS_URL", "https://getreports.example.test")
    monkeypatch.delenv("INGEST_KEY", raising=False)
    monkeypatch.setattr("app.getreports.fetch_sales", lambda: sales)
    monkeypatch.setattr(
        "app.getreports.live_members",
        lambda payload=None: members_from_payload(loyalty),
    )
    client = TestClient(app)
    reports = client.get("/reports")
    assert reports.status_code == 200
    assert "Vietnamese Coffee" in reports.text
    assert "Point of Sale" in reports.text
    assert "DoorDash" in reports.text
    assert "No modifier rows yet." in reports.text
    assert "No merch rows yet." in reports.text
    weekend = client.get("/weekend")
    assert weekend.status_code == 200
    assert "Today" in weekend.text
    assert "Week to date" in weekend.text
    listed = client.get("/loyalty")
    assert listed.status_code == 200
    assert "LOY_FAKE_1" in listed.text or "(205) 555-0100" in listed.text
    assert "/loyalty/LOY_FAKE_2" in listed.text
    detail = client.get("/loyalty/LOY_FAKE_2")
    assert detail.status_code == 200
    assert "(205) 555-0100" in detail.text
    csv = client.get("/loyalty.csv")
    assert csv.status_code == 200
    assert "LOY_FAKE_2" in csv.text
    assert "+12055550100" in csv.text

FAKE_PHONE_A = "+15555550100"
FAKE_PHONE_B = "+12055550100"


def test_loyalty_url_never_sales_root() -> None:
    parsed = urlparse(loyalty_url("https://getreports.example.test"))
    assert parsed.path.rstrip("/") == "/loyalty"
    already = urlparse(loyalty_url("https://getreports.example.test/loyalty"))
    assert already.path.rstrip("/") == "/loyalty"
    slash = urlparse(loyalty_url("https://getreports.example.test/"))
    assert slash.path.rstrip("/") == "/loyalty"


def test_members_from_alternate_live_shapes() -> None:
    """Live /loyalty may wrap members under data/accounts or Square field names."""
    shapes = [
        {
            "count": 2,
            "members": [
                {"id": "LOY_FAKE_1", "points": 150, "phone": FAKE_PHONE_A},
                {"id": "LOY_FAKE_2", "points": 40, "phone": FAKE_PHONE_B},
            ],
        },
        {
            "data": {
                "members": [
                    {"id": "LOY_FAKE_1", "points": 150, "phone": FAKE_PHONE_A},
                    {"id": "LOY_FAKE_2", "points": 40, "phone": FAKE_PHONE_B},
                ]
            }
        },
        {
            "loyalty": {
                "count": 2,
                "accounts": [
                    {
                        "loyalty_account_id": "LOY_FAKE_1",
                        "balance": 150,
                        "phone_number": FAKE_PHONE_A,
                        "customer_id": "CUST_FAKE_1",
                    },
                    {
                        "loyalty_account_id": "LOY_FAKE_2",
                        "balance": 40,
                        "phone_number": FAKE_PHONE_B,
                        "customer_id": "CUST_FAKE_2",
                    },
                ],
            }
        },
        {
            "ok": True,
            "loyalty": {
                "count": 2,
                "loyalty_accounts": [
                    {
                        "id": "LOY_FAKE_1",
                        "balance": 150,
                        "customer_id": "CUST_FAKE_1",
                        "mappings": [{"type": "PHONE", "phone_number": FAKE_PHONE_A}],
                    },
                    {
                        "id": "LOY_FAKE_2",
                        "balance": 40,
                        "customer_id": "CUST_FAKE_2",
                        "mapping": {"phone_number": FAKE_PHONE_B},
                    },
                ],
            },
        },
    ]
    for payload in shapes:
        rows = members_from_payload(payload)
        assert len(rows) == 2
        by_id = {r.id: r for r in rows}
        assert by_id["LOY_FAKE_1"].points == 150
        assert by_id["LOY_FAKE_1"].phone == FAKE_PHONE_A
        assert by_id["LOY_FAKE_2"].phone == FAKE_PHONE_B
        count = payload.get("count")
        if count is None and isinstance(payload.get("loyalty"), dict):
            count = payload["loyalty"].get("count")
        if count is not None:
            assert len(rows) == int(count)


def test_alias_shape_loyalty_page_members_not_zero(monkeypatch) -> None:
    import os
    import tempfile

    if "BAKERY_DB" not in os.environ:
        fd, db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["BAKERY_DB"] = db

    from fastapi.testclient import TestClient

    from app.db import init_db
    from app.getreports import members_from_payload
    from app.main import app

    init_db()
    payload = {
        "ok": True,
        "loyalty": {
            "count": 2,
            "accounts": [
                {
                    "loyalty_account_id": "LOY_FAKE_1",
                    "balance": 150,
                    "phone_number": FAKE_PHONE_A,
                },
                {
                    "loyalty_account_id": "LOY_FAKE_2",
                    "balance": 40,
                    "phone_number": FAKE_PHONE_B,
                },
            ],
        },
    }
    rows = members_from_payload(payload)
    assert len(rows) == 2
    monkeypatch.setenv("GETREPORTS_URL", "https://getreports.example.test")
    monkeypatch.delenv("INGEST_KEY", raising=False)
    monkeypatch.setattr("app.getreports.fetch_sales", lambda: {"ok": True, "sales": {}})
    monkeypatch.setattr(
        "app.getreports.live_members",
        lambda payload=None: members_from_payload(payload) if payload is not None else rows,
    )
    client = TestClient(app)
    listed = client.get("/loyalty")
    assert listed.status_code == 200
    after_members = listed.text.split("Members", 1)[1][:240]
    assert '<strong class="stat-value">0</strong>' not in after_members
    assert '<strong class="stat-value">2</strong>' in listed.text
    assert "LOY_FAKE_1" in listed.text or "(205) 555-0100" in listed.text


def test_fixture_two_fake_members_never_zero() -> None:
    payload = json.loads(LOYALTY.read_text(encoding="utf-8"))
    rows = members_from_payload(payload)
    assert payload["loyalty"]["count"] == 2
    assert len(rows) == 2
    assert len(rows) == payload["loyalty"]["count"]
    phones = {m.phone for m in rows}
    assert FAKE_PHONE_B in phones


def test_json_string_and_customer_id_only_shapes() -> None:
    nested = {
        "loyalty": json.dumps(
            {
                "count": 2,
                "members": [
                    {"id": "LOY_FAKE_1", "points": 150, "phone": FAKE_PHONE_A},
                    {"id": "LOY_FAKE_2", "points": 40, "phone": FAKE_PHONE_B},
                ],
            }
        )
    }
    rows = members_from_payload(nested)
    assert len(rows) == 2
    assert {m.id for m in rows} == {"LOY_FAKE_1", "LOY_FAKE_2"}

    camel = {
        "count": 2,
        "members": [
            {"customerId": "CUST_FAKE_1", "points": 150, "phoneNumber": FAKE_PHONE_A},
            {"customerId": "CUST_FAKE_2", "points": 40, "phoneNumber": FAKE_PHONE_B},
        ],
    }
    rows = members_from_payload(camel)
    assert len(rows) == camel["count"]
    by_id = {m.id: m for m in rows}
    assert by_id["CUST_FAKE_1"].phone == FAKE_PHONE_A
    assert by_id["CUST_FAKE_2"].phone == FAKE_PHONE_B
    assert by_id["CUST_FAKE_1"].points == 150
