"""QR drink order: live Square Catalog menu, payment-link ids, status mapping.

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

from app.catalog import clear_menu_cache  # noqa: E402
from app.drinks import is_drink  # noqa: E402
from app.main import app  # noqa: E402
from app import order as order_svc  # noqa: E402
from tests.fixtures.sample_catalog import (  # noqa: E402
    IRONDALE,
    ML_MILK,
    ML_SAUCE,
    ML_SWEET,
    MOD,
    VAR,
    CatalogFakeClient,
)


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
    clear_menu_cache()
    order_svc.reset_ready_sms_state()


def teardown_function() -> None:
    clear_menu_cache()
    order_svc.reset_ready_sms_state()


def test_all_catalog_names_are_drinks() -> None:
    for drink in order_svc.DEMO_DRINKS:
        assert is_drink(drink["square_name"]), drink["square_name"]
        assert "biscoff roll" not in drink["square_name"].casefold()


def test_validate_cart_and_payment_link_body(monkeypatch) -> None:
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    cart = order_svc.validate_cart(CART)
    assert cart["total_cents"] == 550 + 650
    assert cart["subtotal_cents"] == 550 + 650
    assert cart["tip_cents"] == 0
    assert cart["tip_type"] == "none"
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
    assert body["checkout_options"]["allow_tipping"] is False
    assert "service_charges" not in body["order"]
    assert body["order"]["fulfillments"][0]["type"] == "PICKUP"
    assert body["order"]["line_items"][0]["note"] == "Ronald"
    assert body["order"]["line_items"][1]["note"] == "Ronald"
    assert "to go" not in body["order"]["line_items"][0]["note"].casefold()
    assert "QR Irondale" not in body["order"]["line_items"][0]["note"]
    assert body["order"]["fulfillments"][0]["pickup_details"]["note"] == "to go"
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
    assert data["source"] == "demo"
    assert data["ready_sms"] is False
    assert len(data["drinks"]) == 7
    assert all(d["photo"].startswith("/static/order/drinks/") for d in data["drinks"])
    ids = [d["id"] for d in data["drinks"]]
    assert ids[:6] == [
        "viet-iced-coffee",
        "hot-coffee",
        "biscoff-coffee",
        "matcha-latte",
        "milk-tea",
        "fruit-tea",
    ]
    assert ids[-1] == "lemonade"
    lemonade = next(d for d in data["drinks"] if d["id"] == "lemonade")
    assert lemonade["groups"] == []
    assert lemonade["name"] == "Lemonade"
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
    assert "data-add" not in js.text
    assert "addItem(drink.id, defaultMods" not in js.text
    assert js.text.count("addItem(") == 2
    assert "Add to order" in js.text
    assert "drink-step" in js.text
    assert "No extra options. Add" in js.text
    assert "Tap a drink to choose options" in js.text
    assert 'BRAND_LOGO = "/static/order/logo.svg"' in js.text
    assert "brand-mark" in js.text
    assert "We'll have it at pickup." in js.text
    assert "No tip" in js.text
    assert "Custom $" in js.text
    assert 'data-tip="15"' in js.text
    assert 'data-tip="18"' in js.text
    assert 'data-tip="20"' in js.text
    assert "Tip is added before checkout." in js.text
    assert "Square ready text" not in js.text
    logo = client.get("/static/order/logo.svg")
    assert logo.status_code == 200
    assert "Sunshine" in logo.text
    css = client.get("/static/order/order.css")
    assert css.status_code == 200
    assert "#e8b4b8" in css.text
    assert ".tip-btn" in css.text
    assert ".totals-due" in css.text
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

    fake = CatalogFakeClient(
        payment_link={
            "id": "LINKFAKE",
            "order_id": "ORDERFAKE123456789",
            "url": "https://square.link/u/fake",
        }
    )
    result = order_svc.checkout(CATALOG_CART, origin="https://drinks.example", client=fake)
    assert result["url"] == "https://square.link/u/fake"
    assert result["order_id"] == "ORDERFAKE123456789"
    assert result["demo"] is False
    pay_calls = [payload for url, payload in fake.calls if payload and "online-checkout/payment-links" in url]
    assert pay_calls
    payload = pay_calls[0]
    line = payload["order"]["line_items"][0]
    assert line["catalog_object_id"] == VAR["viet"]
    assert "base_price_money" not in line
    mod_ids = {m["catalog_object_id"] for m in line["modifiers"]}
    assert MOD["condensed"] in mod_ids
    assert MOD["sauce-caramel"] in mod_ids
    assert all("base_price_money" not in m for m in line["modifiers"])
    assert payload["order"]["location_id"] == "L4CK6YWGT5XQX"
    dump = str(payload)
    assert "SQUARE_ACCESS_TOKEN" not in dump
    assert "sandbox-test-token-not-real" not in dump


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


def test_menu_from_square_catalog(monkeypatch) -> None:
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "sandbox-test-token-not-real")
    monkeypatch.setenv("SQUARE_LOCATION_ID_IRONDALE", IRONDALE)
    fake = CatalogFakeClient()
    data = order_svc.menu_payload(client=fake)
    assert data["source"] == "square"
    assert data["pay_mode"] == "square"
    ids = [d["id"] for d in data["drinks"]]
    assert VAR["viet"] in ids
    assert VAR["lemonade"] in ids
    assert "viet-iced-coffee" not in ids
    sauce = None
    for drink in data["drinks"]:
        if drink["id"] == VAR["viet"]:
            sauce = next(g for g in drink["groups"] if g["label"] == "Sauce")
    assert sauce is not None
    assert {o["label"] for o in sauce["options"]} >= {"Caramel", "Mocha", "White chocolate"}
    dump = str(data)
    assert "sandbox-test-token-not-real" not in dump
    assert "SQUARE_ACCESS_TOKEN" not in dump


def test_payment_link_body_uses_catalog_object_ids(monkeypatch) -> None:
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "sandbox-test-token-not-real")
    monkeypatch.setenv("SQUARE_LOCATION_ID_IRONDALE", IRONDALE)
    fake = CatalogFakeClient()
    cart = order_svc.validate_cart(CATALOG_CART, client=fake)
    assert cart["items"][0]["catalog_object_id"] == VAR["viet"]
    body = order_svc.build_payment_link_body(
        cart,
        location_id=IRONDALE,
        redirect_url="https://example.run.app/order/status",
        idempotency_key="abc123",
    )
    line = body["order"]["line_items"][0]
    assert line["catalog_object_id"] == VAR["viet"]
    assert line["name"] == "Vietnamese Coffee"
    assert line["note"] == "Ronald"
    assert "to go" not in line["note"].casefold()
    assert "base_price_money" not in line
    assert body["checkout_options"]["allow_tipping"] is False
    assert "service_charges" not in body["order"]
    assert {m["catalog_object_id"] for m in line["modifiers"]} >= {
        MOD["condensed"],
        MOD["sweet-normal"],
        MOD["sauce-caramel"],
    }


def test_catalog_error_menu_does_not_leak_token(monkeypatch) -> None:
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "sandbox-test-token-not-real")

    class Boom:
        def post(self, url, headers=None, json=None):
            class R:
                status_code = 401
                content = b"{}"

                def json(self):
                    return {"errors": [{"code": "UNAUTHORIZED", "detail": "nope"}]}

            return R()

        def close(self):
            pass

    data = order_svc.menu_payload(client=Boom())
    assert data["source"] == "error"
    assert data["drinks"] == []
    assert "ITEMS_READ" in data["catalog_error"]
    assert "sandbox-test-token-not-real" not in str(data)


class _Resp:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.content = b"{}"

    def json(self):
        return self._body


class ReadySmsFake:
    """httpx-like: Square retrieve/update + Twilio Messages."""

    def __init__(self, order: dict, *, twilio_status: int = 201):
        self.order = dict(order)
        self.twilio_status = twilio_status
        self.posts: list[dict] = []
        self.puts: list[dict] = []
        self.gets: list[str] = []

    def get(self, url, headers=None, params=None):
        self.gets.append(url)
        if "/v2/orders/" in url:
            return _Resp(200, {"order": self.order})
        return _Resp(404, {})

    def put(self, url, headers=None, json=None):
        self.puts.append({"url": url, "json": json})
        version = int(self.order.get("version") or 1) + 1
        patch = (json or {}).get("order") or {}
        self.order["version"] = version
        if "metadata" in patch:
            meta = dict(self.order.get("metadata") or {})
            meta.update(patch["metadata"])
            self.order["metadata"] = meta
        if "fulfillments" in patch and self.order.get("fulfillments"):
            state = patch["fulfillments"][0].get("state")
            if state:
                self.order["fulfillments"][0]["state"] = state
        return _Resp(200, {"order": self.order})

    def post(self, url, headers=None, json=None, data=None, auth=None):
        self.posts.append({"url": url, "json": json, "data": data, "auth": auth})
        if "twilio.com" in url and "Messages.json" in url:
            return _Resp(self.twilio_status, {"sid": "SMfake"})
        return _Resp(404, {})

    def close(self):
        pass


READY_ORDER = {
    "version": 3,
    "state": "OPEN",
    "reference_id": "QR-14",
    "tenders": [{"id": "t1", "type": "CARD"}],
    "fulfillments": [
        {
            "uid": "ful1",
            "state": "PREPARED",
            "pickup_details": {
                "note": "to go",
                "recipient": {"display_name": "Ronald", "phone_number": "+12055550100"},
            },
        }
    ],
    "line_items": [
        {
            "name": "Vietnamese Coffee",
            "quantity": "1",
            "note": "Ronald",
            "modifiers": [{"name": "Condensed"}],
        }
    ],
}


def _twilio_env(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfakeaccountsid000000000000000")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-twilio-token-not-real")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+12055550999")


def test_menu_ready_sms_flag_when_twilio_configured(monkeypatch) -> None:
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    _twilio_env(monkeypatch)
    data = client.get("/order/api/menu").json()
    assert data["ready_sms"] is True
    js = client.get("/static/order/order.js").text
    assert "Phone for a ready text (optional)" in js


def test_ready_sms_sends_once_on_status_poll(monkeypatch) -> None:
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "sandbox-test-token-not-real")
    _twilio_env(monkeypatch)
    fake = ReadySmsFake(READY_ORDER)
    first = order_svc.lookup_status(order_id="ORDERFAKE123456789", client=fake)
    assert first["status"] == "ready"
    assert first["ready_sms"] is True
    assert first["text_opt_in"] is True
    twilio = [p for p in fake.posts if p["data"] and "Messages.json" in p["url"]]
    assert len(twilio) == 1
    assert twilio[0]["data"]["To"] == "+12055550100"
    assert "ready" in twilio[0]["data"]["Body"].casefold()
    assert "pickup" in twilio[0]["data"]["Body"].casefold()
    assert twilio[0]["auth"][0] == "ACfakeaccountsid000000000000000"
    assert "fake-twilio-token-not-real" not in str(first)
    second = order_svc.lookup_status(order_id="ORDERFAKE123456789", client=fake)
    assert second["status"] == "ready"
    twilio_again = [p for p in fake.posts if p["data"] and "Messages.json" in p["url"]]
    assert len(twilio_again) == 1
    meta_puts = [p for p in fake.puts if (p["json"] or {}).get("order", {}).get("metadata")]
    assert meta_puts
    assert meta_puts[0]["json"]["order"]["metadata"]["qr_ready_sms"] == "sent"


def test_ready_sms_skipped_without_phone_or_twilio(monkeypatch) -> None:
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "sandbox-test-token-not-real")
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWILIO_FROM_NUMBER", raising=False)
    order = {
        **READY_ORDER,
        "fulfillments": [
            {
                "uid": "ful1",
                "state": "PREPARED",
                "pickup_details": {"note": "to go", "recipient": {"display_name": "Ronald"}},
            }
        ],
    }
    fake = ReadySmsFake(order)
    view = order_svc.lookup_status(order_id="ORDERFAKE123456789", client=fake)
    assert view["status"] == "ready"
    assert view["ready_sms"] is False
    assert fake.posts == []


def test_ready_sms_on_board_mark_prepared(monkeypatch) -> None:
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "sandbox-test-token-not-real")
    _twilio_env(monkeypatch)
    making = dict(READY_ORDER)
    making["fulfillments"] = [
        {
            "uid": "ful1",
            "state": "PROPOSED",
            "pickup_details": {
                "note": "to go",
                "recipient": {"display_name": "Ronald", "phone_number": "2055550100"},
            },
        }
    ]
    fake = ReadySmsFake(making)
    assert order_svc.mark_pickup_prepared("ORDERFAKE123456789", client=fake) is True
    twilio = [p for p in fake.posts if p.get("data") and "Messages.json" in p["url"]]
    assert len(twilio) == 1
    assert twilio[0]["data"]["To"] == "+12055550100"
    states = [
        ((p["json"] or {}).get("order") or {}).get("fulfillments", [{}])[0].get("state")
        for p in fake.puts
        if (p["json"] or {}).get("order", {}).get("fulfillments")
    ]
    assert "PREPARED" in states


def test_status_line_detail_ignores_name_note() -> None:
    view = order_svc.status_from_square_order(READY_ORDER, "OID")
    assert view["items"][0]["detail"] == "Condensed"
    assert "Ronald" not in view["items"][0]["detail"]


def test_tip_percent_cents_half_up() -> None:
    assert order_svc.tip_percent_cents(1200, 15) == 180
    assert order_svc.tip_percent_cents(1200, 18) == 216
    assert order_svc.tip_percent_cents(1200, 20) == 240
    assert order_svc.tip_percent_cents(550, 18) == 99
    assert order_svc.tip_percent_cents(401, 18) == 72  # 72.18 → 72
    assert order_svc.tip_percent_cents(555, 18) == 100  # 99.9 → 100
    assert order_svc.tip_percent_cents(1, 15) == 0
    assert order_svc.tip_percent_cents(0, 18) == 0


def test_parse_cart_tip_none_and_omitted() -> None:
    assert order_svc.parse_cart_tip({}, 1200) == {
        "tip_type": "none",
        "tip_percent": 0,
        "tip_cents": 0,
    }
    assert order_svc.parse_cart_tip({"tip": {"type": "none"}}, 1200)["tip_cents"] == 0
    assert order_svc.parse_cart_tip({"tip": {"type": "custom", "amount_cents": 0}}, 1200)[
        "tip_type"
    ] == "none"


def test_parse_cart_tip_percent_and_custom() -> None:
    eighteen = order_svc.parse_cart_tip({"tip": {"type": "percent", "percent": 18}}, 1200)
    assert eighteen == {"tip_type": "percent", "tip_percent": 18, "tip_cents": 216}
    custom = order_svc.parse_cart_tip({"tip": {"type": "custom", "amount_cents": 250}}, 1200)
    assert custom == {"tip_type": "custom", "tip_percent": 0, "tip_cents": 250}


def test_parse_cart_tip_rejects_bad_values() -> None:
    cases = [
        {"tip": "18"},
        {"tip": {"type": "percent", "percent": 25}},
        {"tip": {"type": "percent"}},
        {"tip": {"type": "custom", "amount_cents": -1}},
        {"tip": {"type": "custom", "amount_cents": 10001}},
        {"tip": {"type": "maybe"}},
    ]
    for body in cases:
        try:
            order_svc.parse_cart_tip(body, 1200)
            assert False, f"expected OrderError for {body}"
        except order_svc.OrderError as exc:
            assert exc.status_code == 400


def test_validate_cart_18_percent_tip(monkeypatch) -> None:
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    cart = order_svc.validate_cart(dict(CART, tip={"type": "percent", "percent": 18}))
    assert cart["subtotal_cents"] == 1200
    assert cart["tip_type"] == "percent"
    assert cart["tip_percent"] == 18
    assert cart["tip_cents"] == 216
    assert cart["total_cents"] == 1416


def test_payment_link_body_includes_tip_service_charge(monkeypatch) -> None:
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    cart = order_svc.validate_cart(dict(CART, tip={"type": "custom", "amount_cents": 200}))
    body = order_svc.build_payment_link_body(
        cart,
        location_id="L4CK6YWGT5XQX",
        redirect_url="https://example.run.app/order/status",
        idempotency_key="tip-custom",
    )
    assert body["checkout_options"]["allow_tipping"] is False
    charges = body["order"]["service_charges"]
    assert len(charges) == 1
    assert charges[0] == {
        "name": "Tip",
        "amount_money": {"amount": 200, "currency": "USD"},
        "calculation_phase": "TOTAL_PHASE",
        "taxable": False,
        "scope": "ORDER",
        "type": "CUSTOM",
    }
    assert "tip $2.00" in body["payment_note"]
    names = [line["name"] for line in body["order"]["line_items"]]
    assert "Tip" not in names


def test_square_checkout_payload_has_no_tip_without_selection(monkeypatch) -> None:
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "sandbox-test-token-not-real")
    monkeypatch.setenv("SQUARE_LOCATION_ID_IRONDALE", IRONDALE)
    monkeypatch.setenv("BAKERY_SERVICE", "drinks")
    fake = CatalogFakeClient(
        payment_link={
            "id": "LINKFAKE",
            "order_id": "ORDERFAKE123456789",
            "url": "https://square.link/u/fake",
        }
    )
    result = order_svc.checkout(
        dict(CATALOG_CART, tip={"type": "none"}),
        origin="https://drinks.example",
        client=fake,
    )
    assert result["tip_cents"] == 0
    assert result["total_cents"] == result["subtotal_cents"]
    payload = next(p for url, p in fake.calls if p and "online-checkout/payment-links" in url)
    assert payload["checkout_options"]["allow_tipping"] is False
    assert "service_charges" not in payload["order"]


def test_square_checkout_payload_charges_percent_tip(monkeypatch) -> None:
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "sandbox-test-token-not-real")
    monkeypatch.setenv("SQUARE_LOCATION_ID_IRONDALE", IRONDALE)
    monkeypatch.setenv("BAKERY_SERVICE", "drinks")
    fake = CatalogFakeClient(
        payment_link={
            "id": "LINKFAKE",
            "order_id": "ORDERFAKE123456789",
            "url": "https://square.link/u/fake",
        }
    )
    result = order_svc.checkout(
        dict(CATALOG_CART, tip={"type": "percent", "percent": 20}),
        origin="https://drinks.example",
        client=fake,
    )
    payload = next(p for url, p in fake.calls if p and "online-checkout/payment-links" in url)
    charge = payload["order"]["service_charges"][0]
    assert charge["name"] == "Tip"
    assert charge["amount_money"]["amount"] == result["tip_cents"]
    assert result["total_cents"] == result["subtotal_cents"] + result["tip_cents"]
    assert result["tip_cents"] == order_svc.tip_percent_cents(result["subtotal_cents"], 20)
    assert payload["checkout_options"]["allow_tipping"] is False
    line = payload["order"]["line_items"][0]
    assert line["catalog_object_id"] == VAR["viet"]
    assert line["note"] == "Ronald"


def test_laptop_demo_checkout_includes_tip_total(monkeypatch) -> None:
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("BAKERY_SERVICE", "laptop")
    res = client.post(
        "/order/api/checkout",
        json=dict(CART, tip={"type": "percent", "percent": 15}),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["demo"] is True
    assert data["tip_cents"] == 180
    assert data["total_cents"] == 1380
