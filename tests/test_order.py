"""QR drink order: curated menu, Square payment-link payload, status mapping.

No live Square token. HTTP is mocked.
"""

from __future__ import annotations

import os
import tempfile

if "BAKERY_DB" not in os.environ:
    _fd, _db = tempfile.mkstemp(suffix=".db")
    os.close(_fd)
    os.environ["BAKERY_DB"] = _db

from fastapi.testclient import TestClient  # noqa: E402

from app.drinks import is_drink  # noqa: E402
from app.main import app  # noqa: E402
from app import order as order_svc  # noqa: E402


client = TestClient(app)

CART = {
    "name": "Ronald",
    "pickup": "to-go",
    "phone": "2055550100",
    "items": [
        {
            "id": "viet-iced-coffee",
            "qty": 1,
            "modifiers": {"milk": "condensed", "sweet": "normal", "addons": []},
        },
        {
            "id": "biscoff-coffee",
            "qty": 1,
            "modifiers": {"milk": "oat", "sweet": "extra", "addons": []},
        },
    ],
}


def test_all_catalog_names_are_drinks() -> None:
    for drink in order_svc.DRINKS:
        assert is_drink(drink["square_name"]), drink["square_name"]
        assert "biscoff roll" not in drink["square_name"].casefold()


def test_validate_cart_and_payment_link_body(monkeypatch) -> None:
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    cart = order_svc.validate_cart(CART)
    assert cart["total_cents"] == 550 + 650
    assert cart["name"] == "Ronald"
    body = order_svc.build_payment_link_body(
        cart,
        location_id="L4CK6YWGT5XQX",
        redirect_url="https://example.run.app/order/status",
        idempotency_key="abc123",
    )
    names = [line["name"] for line in body["order"]["line_items"]]
    assert names == ["Vietnamese Coffee", "Biscoff Coffee"]
    assert body["order"]["location_id"] == "L4CK6YWGT5XQX"
    assert body["checkout_options"]["redirect_url"].endswith("/order/status")
    assert body["checkout_options"]["accepted_payment_methods"]["google_pay"] is True
    assert body["order"]["fulfillments"][0]["type"] == "PICKUP"
    assert "Name: Ronald" in body["order"]["line_items"][0]["note"]
    dump = str(body)
    assert "SQUARE_ACCESS_TOKEN" not in dump
    assert "EAA" not in dump


def test_missing_name_rejected() -> None:
    bad = dict(CART, name="  ")
    try:
        order_svc.validate_cart(bad)
        assert False, "expected OrderError"
    except order_svc.OrderError as exc:
        assert exc.status_code == 400
        assert "Name" in exc.message


def test_unknown_drink_rejected() -> None:
    bad = dict(CART, items=[{"id": "croissant", "qty": 1, "modifiers": {}}])
    try:
        order_svc.validate_cart(bad)
        assert False, "expected OrderError"
    except order_svc.OrderError as exc:
        assert "Unknown drink" in exc.message


def test_menu_has_six_drinks_and_no_token() -> None:
    res = client.get("/order/api/menu")
    assert res.status_code == 200
    data = res.json()
    assert len(data["drinks"]) == 6
    assert all(d["photo"].startswith("/static/order/drinks/") for d in data["drinks"])
    ids = [d["id"] for d in data["drinks"]]
    assert ids == [
        "viet-iced-coffee",
        "hot-coffee",
        "biscoff-coffee",
        "matcha-latte",
        "milk-tea",
        "fruit-tea",
    ]
    text = res.text.casefold()
    assert "square_access_token" not in text
    assert "eaaa" not in text


def test_order_pages_and_assets() -> None:
    page = client.get("/order")
    assert page.status_code == 200
    assert "Sunshine's" in page.text
    assert "/static/order/order.js" in page.text
    assert "SQUARE_ACCESS_TOKEN" not in page.text
    js = client.get("/static/order/order.js")
    assert js.status_code == 200
    assert "SQUARE_ACCESS_TOKEN" not in js.text
    assert "Head to the pickup counter" in js.text
    assert "Go to pickup" in js.text
    assert "grab it" not in js.text.casefold()
    assert "name on the cup" not in js.text.casefold()
    css = client.get("/static/order/order.css")
    assert css.status_code == 200
    assert "#e8b4b8" in css.text
    photo = client.get("/static/order/drinks/viet-iced-coffee.svg")
    assert photo.status_code == 200
    tent = client.get("/order/tent")
    assert tent.status_code == 200
    assert "Scan to order drinks" in tent.text
    assert "Pay on your phone" in tent.text
    qr = client.get("/qr", follow_redirects=False)
    assert qr.status_code in (302, 303)
    assert qr.headers["location"].endswith("/order")


def test_laptop_demo_checkout(monkeypatch) -> None:
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("BAKERY_SERVICE", "laptop")
    res = client.post("/order/api/checkout", json=CART)
    assert res.status_code == 200
    data = res.json()
    assert data["demo"] is True
    assert data["url"].startswith("/order/status")
    assert "square.link" not in (data.get("url") or "")
    status = client.get("/order/api/status?oid=demo")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "making"
    assert body["paid"] is True
    ready = client.get("/order/api/status?oid=demo-ready")
    assert ready.json()["status"] == "ready"


def test_square_checkout_mocked(monkeypatch) -> None:
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "sandbox-test-token-not-real")
    monkeypatch.setenv("SQUARE_LOCATION_ID_IRONDALE", "L4CK6YWGT5XQX")
    monkeypatch.setenv("BAKERY_SERVICE", "drinks")

    class FakeResp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {
                "payment_link": {
                    "id": "LINKFAKE",
                    "order_id": "ORDERFAKE123456789",
                    "url": "https://square.link/u/fake",
                }
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def post(self, url, headers=None, json=None):
            assert "online-checkout/payment-links" in url
            assert headers["Authorization"] == "Bearer sandbox-test-token-not-real"
            assert json["order"]["location_id"] == "L4CK6YWGT5XQX"
            assert json["checkout_options"]["redirect_url"].endswith("/order/status")
            self.payload = json
            return FakeResp()

        def close(self):
            pass

    fake = FakeClient()
    result = order_svc.checkout(CART, origin="https://drinks.example", client=fake)
    assert result["url"] == "https://square.link/u/fake"
    assert result["order_id"] == "ORDERFAKE123456789"
    assert result["demo"] is False


def test_status_from_square_order_ready() -> None:
    order = {
        "state": "OPEN",
        "reference_id": "QR-14",
        "tenders": [{"id": "t1", "type": "CARD"}],
        "fulfillments": [
            {
                "state": "PREPARED",
                "pickup_details": {
                    "note": "to go",
                    "recipient": {"display_name": "Ronald"},
                },
            }
        ],
        "line_items": [
            {
                "name": "Vietnamese Coffee",
                "quantity": "1",
                "modifiers": [{"name": "Condensed"}],
            }
        ],
    }
    view = order_svc.status_from_square_order(order, "OID")
    assert view["status"] == "ready"
    assert view["order_number"] == "14"
    assert view["items"][0]["name"] == "Vietnamese Coffee"


def test_status_making_when_paid_proposed() -> None:
    order = {
        "state": "OPEN",
        "tenders": [{"id": "t1", "type": "WALLET"}],
        "fulfillments": [{"state": "PROPOSED", "pickup_details": {"note": "to go", "recipient": {}}}],
        "line_items": [{"name": "Milk Tea", "quantity": "1", "modifiers": [{"name": "Taro"}]}],
    }
    view = order_svc.status_from_square_order(order, "OID")
    assert view["status"] == "making"
    assert view["paid"] is True


def test_drinks_service_serves_order(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "drinks")
    page = client.get("/order")
    assert page.status_code == 200
    loyalty = client.get("/loyalty")
    assert loyalty.status_code == 404


def test_desk_hides_order(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "desk")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    page = client.get("/order")
    assert page.status_code == 404


def test_drinks_without_token_cannot_charge(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "drinks")
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    res = client.post("/order/api/checkout", json=CART)
    assert res.status_code == 503
    assert "square.link" not in res.text.casefold()
    assert res.json()["error"]


def test_location_env_prefers_irondale(monkeypatch) -> None:
    monkeypatch.setenv("SQUARE_LOCATION_ID_IRONDALE", "IRONDALEID")
    monkeypatch.setenv("SQUARE_LOCATION_ID", "OTHER")
    assert order_svc.irondale_location_id() == "IRONDALEID"
    monkeypatch.delenv("SQUARE_LOCATION_ID_IRONDALE")
    monkeypatch.setenv("LOCATION_ID", "FROMLOC")
    assert order_svc.irondale_location_id() == "FROMLOC"


def test_looks_like_square_order_id() -> None:
    assert order_svc.looks_like_square_order_id("ORDERFAKE123456789")
    assert not order_svc.looks_like_square_order_id("12")
    assert not order_svc.looks_like_square_order_id("anon-1")
    assert not order_svc.looks_like_square_order_id("demo")
