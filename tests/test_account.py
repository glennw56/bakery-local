"""Square phone-login privacy: session tokens, no email, GET is read-only, profile update."""

from __future__ import annotations

import os

os.environ.setdefault("SESSION_SECRET", "test-account-session-secret")

from fastapi.testclient import TestClient  # noqa: E402
import pytest  # noqa: E402

from app.main import app  # noqa: E402
from app import account as account_svc  # noqa: E402

client = TestClient(app)

CUSTOMER = {
    "id": "CUST_ADA",
    "phone_number": "+12055550100",
    "given_name": "Ada",
    "family_name": "Lovelace",
    "nickname": "",
    "email_address": "ada@example.com",
    "email": "ada@hidden.com",
    "version": 4,
}

LOYALTY = {"enrolled": False, "account_id": "", "points": 0, "program_id": "PROG"}

SQUARE_ORDER = {
    "id": "ORDER_MILK_TEA",
    "customer_id": "CUST_ADA",
    "reference_id": "QR-42",
    "created_at": "2026-04-01T15:30:00Z",
    "state": "COMPLETED",
    "tenders": [
        {
            "id": "tender-milk-tea",
            "type": "CARD",
            "payment_id": "pay-milk-tea",
            "amount_money": {"amount": 650, "currency": "USD"},
        }
    ],
    "net_amount_due_money": {"amount": 0, "currency": "USD"},
    "net_amounts": {"total_money": {"amount": 650, "currency": "USD"}},
    "fulfillments": [
        {
            "state": "COMPLETED",
            "pickup_details": {
                "recipient": {
                    "phone_number": "+12055550100",
                    "display_name": "Ada",
                },
            },
        }
    ],
    "line_items": [
        {
            "name": "Milk Tea",
            "quantity": "1",
            "catalog_object_id": "VAR_MILK_TEA",
            "variation_name": "Regular",
            "base_price_money": {"amount": 550, "currency": "USD"},
            "total_money": {"amount": 650, "currency": "USD"},
            "modifiers": [
                {
                    "uid": "mod-oat",
                    "name": "Oat Milk",
                    "quantity": "1",
                    "catalog_object_id": "MOD_OAT",
                    "base_price_money": {"amount": 75, "currency": "USD"},
                    "total_price_money": {"amount": 75, "currency": "USD"},
                },
                {
                    "name": "Tapioca Boba",
                    "quantity": "2",
                    "catalog_object_id": "MOD_BOBA",
                    "base_price_money": {"amount": 25, "currency": "USD"},
                },
                {
                    "display_name": "Regular Sweet",
                    "quantity": "1",
                    "total_money": {"amount": 0, "currency": "USD"},
                    "base_price_money": {"amount": 0, "currency": "USD"},
                },
                {"catalog_object_id": "MOD_SKIP_NO_NAME"},
            ],
        }
    ],
}

PAID_MAKING_ORDER = {
    "id": "ORDER_PAID_MAKING",
    "customer_id": "CUST_ADA",
    "reference_id": "QR-43",
    "created_at": "2026-04-01T16:00:00Z",
    "state": "OPEN",
    "tenders": [
        {
            "id": "tender-fruit-tea",
            "type": "WALLET",
            "payment_id": "pay-fruit-tea",
        }
    ],
    "net_amount_due_money": {"amount": 0, "currency": "USD"},
    "net_amounts": {"total_money": {"amount": 550, "currency": "USD"}},
    "fulfillments": [
        {
            "state": "PROPOSED",
            "pickup_details": {
                "recipient": {
                    "phone_number": "+12055550100",
                    "display_name": "Ada",
                },
            },
        }
    ],
    "line_items": [
        {
            "name": "Fruit Tea",
            "quantity": "1",
            "total_money": {"amount": 550, "currency": "USD"},
        }
    ],
}

UNPAID_OPEN_ORDER = {
    "id": "ORDER_UNPAID_TICKET",
    "customer_id": "CUST_ADA",
    "reference_id": "QR-99",
    "created_at": "2026-04-01T16:05:00Z",
    "state": "OPEN",
    "net_amount_due_money": {"amount": 650, "currency": "USD"},
    "net_amounts": {"total_money": {"amount": 650, "currency": "USD"}},
    "fulfillments": [
        {
            "state": "PROPOSED",
            "pickup_details": {
                "recipient": {
                    "phone_number": "+12055550100",
                    "display_name": "Ada",
                },
            },
        }
    ],
    "line_items": [{"name": "Milk Tea", "quantity": "1"}],
}

MIXED_CUSTOMER_ORDERS = [UNPAID_OPEN_ORDER, SQUARE_ORDER, PAID_MAKING_ORDER]


def setup_function() -> None:
    account_svc.reset_login_rate_limits()


def _stub_square(monkeypatch, *, enrolls=None, updates=None) -> list:
    joins: list = enrolls if enrolls is not None else []
    update_calls: list = updates if updates is not None else []

    def fake_enroll(phone, join, **kw):
        joins.append(bool(join))
        return dict(LOYALTY)

    def fake_update(customer_id, fields, **kw):
        update_calls.append(
            {
                "customer_id": customer_id,
                "fields": dict(fields),
                "version": kw.get("version"),
            }
        )
        merged = dict(CUSTOMER)
        if "given_name" in fields:
            merged["given_name"] = fields["given_name"]
        if "family_name" in fields:
            merged["family_name"] = fields["family_name"]
        if "email_address" in fields:
            merged["email_address"] = fields["email_address"]
        return merged

    monkeypatch.setattr(account_svc, "search_customer_by_phone", lambda *a, **k: CUSTOMER)
    monkeypatch.setattr(account_svc, "retrieve_customer", lambda *a, **k: CUSTOMER)
    monkeypatch.setattr(account_svc, "create_customer", lambda *a, **k: CUSTOMER)
    monkeypatch.setattr(account_svc, "update_customer", fake_update)
    monkeypatch.setattr(account_svc, "enroll_loyalty", fake_enroll)
    monkeypatch.setattr(account_svc, "customer_orders", lambda *a, **k: [])
    monkeypatch.setattr(account_svc, "open_queue_orders", lambda *a, **k: [])
    return joins


def test_normalize_phone_us_and_e164() -> None:
    assert account_svc.normalize_phone("205-555-0100") == "+12055550100"
    assert account_svc.normalize_phone("(205) 555-0100") == "+12055550100"
    assert account_svc.normalize_phone("12055550100") == "+12055550100"
    assert account_svc.normalize_phone("+12055550100") == "+12055550100"
    assert account_svc.normalize_phone("") == ""
    assert account_svc.normalize_phone("123") == ""
    assert account_svc.normalize_phone("not-a-phone") == ""


def test_customer_public_never_includes_email() -> None:
    public = account_svc.customer_public(CUSTOMER)
    assert "email" not in public
    assert "email_address" not in public
    assert public["id"] == "CUST_ADA"
    assert public["given_name"] == "Ada"
    assert public["display_name"] == "Ada Lovelace"
    dumped = str(public)
    assert "ada@example.com" not in dumped
    assert "ada@hidden.com" not in dumped


def test_mint_and_verify_session_token(monkeypatch) -> None:
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    token, expires_at = account_svc.mint_session_token("CUST_ADA", "+12055550100", now=1_700_000_000)
    assert expires_at.endswith("Z")
    session = account_svc.verify_session_token(token, now=1_700_000_000)
    assert session["customer_id"] == "CUST_ADA"
    assert session["phone"] == "+12055550100"
    assert session["exp"] > 1_700_000_000


def test_session_token_rejects_tamper_and_expiry(monkeypatch) -> None:
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    token, _ = account_svc.mint_session_token("CUST_ADA", "+12055550100", now=1_700_000_000, ttl=60)
    with pytest.raises(account_svc.AccountError) as expired:
        account_svc.verify_session_token(token, now=1_700_000_000 + 61)
    assert expired.value.status_code == 401
    blob, mac = token.rsplit(".", 1)
    tampered = blob + "." + ("0" if mac[0] != "0" else "1") + mac[1:]
    with pytest.raises(account_svc.AccountError) as bad:
        account_svc.verify_session_token(tampered, now=1_700_000_000)
    assert bad.value.status_code == 401


def test_session_secret_falls_back_to_square_token_hash(monkeypatch) -> None:
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "sq0at-fake-a")
    token, _ = account_svc.mint_session_token("C1", "+12055550100", now=1_700_000_000)
    assert account_svc.verify_session_token(token, now=1_700_000_000)["customer_id"] == "C1"
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "sq0at-fake-b")
    with pytest.raises(account_svc.AccountError):
        account_svc.verify_session_token(token, now=1_700_000_000)


def test_token_from_headers_bearer_and_x_session() -> None:
    assert account_svc.token_from_headers("Bearer abc.def", "") == "abc.def"
    assert account_svc.token_from_headers("bearer abc.def", "") == "abc.def"
    assert account_svc.token_from_headers("", "xyz") == "xyz"
    assert account_svc.token_from_headers("", "") == ""


def test_wants_loyalty_is_explicit_opt_in() -> None:
    assert account_svc.wants_loyalty({"join_loyalty": True}) is True
    assert account_svc.wants_loyalty({"join_loyalty": "true"}) is True
    assert account_svc.wants_loyalty({"phone": "2055550100"}) is False
    assert account_svc.wants_loyalty({"join_loyalty": False}) is False
    assert account_svc.wants_loyalty({}) is False


def test_get_account_does_not_enroll_loyalty(monkeypatch) -> None:
    joins = _stub_square(monkeypatch)
    payload = account_svc.get_account("CUST_ADA", "")
    assert joins == [False]
    assert "email" not in payload["customer"]
    assert "session_token" not in payload


def test_login_enrolls_only_when_opted_in(monkeypatch) -> None:
    joins = _stub_square(monkeypatch)
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    skipped = account_svc.login_or_signup({"phone": "2055550100"})
    assert joins == [False]
    assert skipped["session_token"]
    assert "email" not in skipped["customer"]
    opted = account_svc.login_or_signup({"phone": "2055550100", "join_loyalty": True})
    assert joins == [False, True]
    assert opted["ok"] is True


def test_get_without_token_is_401() -> None:
    for path in (
        "/order/api/account?phone=2055550100",
        "/order/api/customer?customer_id=CUST_ADA",
        "/order/api/account/status?phone=2055550100",
        "/order/api/orders?customer_id=CUST_ADA",
        "/order/api/orders/ORDER_MILK_TEA?customer_id=CUST_ADA",
    ):
        response = client.get(path)
        assert response.status_code == 401, path
        body = response.json()
        assert body["ok"] is False
        assert "email" not in body
        assert "customer" not in body


def test_get_with_invalid_token_is_401() -> None:
    response = client.get(
        "/order/api/account",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
    assert response.json()["ok"] is False


def test_post_login_returns_session_and_no_email(monkeypatch) -> None:
    _stub_square(monkeypatch)
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    response = client.post("/order/api/account/phone", json={"phone": "2055550100"})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "session_token" in data and data["session_token"]
    assert "expires_at" in data
    assert "email" not in data["customer"]
    assert "ada@example.com" not in str(data)


def test_get_with_bearer_token_returns_account(monkeypatch) -> None:
    joins = _stub_square(monkeypatch)
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    posted = client.post("/order/api/customer", json={"phone": "205-555-0100"})
    token = posted.json()["session_token"]
    joins.clear()
    got = client.get("/order/api/account", headers={"Authorization": f"Bearer {token}"})
    assert got.status_code == 200
    data = got.json()
    assert data["customer"]["id"] == "CUST_ADA"
    assert "email" not in data["customer"]
    assert joins == [False]
    alt = client.get("/order/api/account/status", headers={"X-Session-Token": token})
    assert alt.status_code == 200
    orders = client.get("/order/api/orders", headers={"Authorization": f"Bearer {token}"})
    assert orders.status_code == 200


def test_login_rate_limit_by_phone(monkeypatch) -> None:
    _stub_square(monkeypatch)
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    for _ in range(account_svc.LOGIN_PHONE_LIMIT):
        response = client.post("/order/api/account/phone", json={"phone": "2055550100"})
        assert response.status_code == 200
    blocked = client.post("/order/api/account/phone", json={"phone": "2055550100"})
    assert blocked.status_code == 429
    assert blocked.json()["ok"] is False
    assert blocked.headers.get("retry-after")


def test_login_rate_limit_by_ip(monkeypatch) -> None:
    _stub_square(monkeypatch)
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    for i in range(account_svc.LOGIN_IP_LIMIT):
        phone = f"205555{i:04d}"
        response = client.post("/order/api/account/phone", json={"phone": phone})
        assert response.status_code == 200, phone
    blocked = client.post("/order/api/account/phone", json={"phone": "2055559999"})
    assert blocked.status_code == 429


def test_parse_profile_fields_requires_one_valid_value() -> None:
    with pytest.raises(account_svc.AccountError) as empty:
        account_svc.parse_profile_fields({})
    assert empty.value.status_code == 400
    with pytest.raises(account_svc.AccountError):
        account_svc.parse_profile_fields({"given_name": "  ", "email": ""})
    with pytest.raises(account_svc.AccountError) as bad_email:
        account_svc.parse_profile_fields({"email": "not-an-email"})
    assert "email" in bad_email.value.message.lower()
    assert account_svc.parse_profile_fields({"given_name": "Ada"}) == {"given_name": "Ada"}
    assert account_svc.parse_profile_fields({"email": "ada@example.com"}) == {
        "email_address": "ada@example.com"
    }
    parsed = account_svc.parse_profile_fields(
        {"given_name": "Ada", "family_name": "Lovelace", "email": "ada@bakery.test"}
    )
    assert parsed == {
        "given_name": "Ada",
        "family_name": "Lovelace",
        "email_address": "ada@bakery.test",
    }


def test_update_customer_put_targets_path_id(monkeypatch) -> None:
    calls: list = []

    def fake_square(method, path, payload=None, **kw):
        calls.append((method, path, payload))
        return {"customer": {"id": "CUST_ADA", "given_name": payload.get("given_name")}}

    monkeypatch.setattr(account_svc, "_square_json", fake_square)
    customer = account_svc.update_customer("CUST_ADA", {"given_name": "Ada"}, version=3)
    assert customer["id"] == "CUST_ADA"
    assert calls == [
        ("PUT", "/v2/customers/CUST_ADA", {"given_name": "Ada", "version": 3}),
    ]


def test_update_profile_ignores_body_customer_id(monkeypatch) -> None:
    updates: list = []
    _stub_square(monkeypatch, updates=updates)
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    payload = account_svc.update_profile(
        "CUST_ADA",
        "+12055550100",
        {"given_name": "Ada", "customer_id": "EVIL_CUSTOMER"},
    )
    assert updates == [
        {"customer_id": "CUST_ADA", "fields": {"given_name": "Ada"}, "version": 4}
    ]
    assert payload["ok"] is True
    assert payload["created"] is False
    assert payload["customer"]["id"] == "CUST_ADA"
    assert payload["customer"]["given_name"] == "Ada"
    assert "email" not in payload["customer"]
    assert "email_address" not in payload["customer"]
    assert payload["session_token"]
    assert "ada@example.com" not in str(payload)


def test_post_profile_without_token_is_401() -> None:
    for path in ("/order/api/account/profile", "/order/api/customer/profile"):
        response = client.post(path, json={"given_name": "Ada"})
        assert response.status_code == 401, path
        body = response.json()
        assert body["ok"] is False
        assert "email" not in body
        assert "customer" not in body


def test_post_profile_empty_body_is_400(monkeypatch) -> None:
    _stub_square(monkeypatch)
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    token, _ = account_svc.mint_session_token("CUST_ADA", "+12055550100")
    response = client.post(
        "/order/api/account/profile",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_post_profile_invalid_email_is_400(monkeypatch) -> None:
    _stub_square(monkeypatch)
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    token, _ = account_svc.mint_session_token("CUST_ADA", "+12055550100")
    response = client.post(
        "/order/api/account/profile",
        json={"email": "ada-at-bakery"},
        headers={"X-Session-Token": token},
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_post_profile_updates_square_and_strips_email(monkeypatch) -> None:
    updates: list = []
    joins = _stub_square(monkeypatch, updates=updates)
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    posted = client.post("/order/api/account/phone", json={"phone": "2055550100"})
    token = posted.json()["session_token"]
    joins.clear()
    response = client.post(
        "/order/api/account/profile",
        json={
            "given_name": "Ada",
            "family_name": "Lovelace",
            "email": "ada@example.com",
            "customer_id": "EVIL_CUSTOMER",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["customer"]["id"] == "CUST_ADA"
    assert data["customer"]["given_name"] == "Ada"
    assert "email" not in data["customer"]
    assert "email_address" not in data["customer"]
    assert "ada@example.com" not in str(data)
    assert data["session_token"]
    assert updates == [
        {
            "customer_id": "CUST_ADA",
            "fields": {
                "given_name": "Ada",
                "family_name": "Lovelace",
                "email_address": "ada@example.com",
            },
            "version": 4,
        }
    ]
    assert joins == [False]


def test_post_customer_profile_alias(monkeypatch) -> None:
    updates: list = []
    _stub_square(monkeypatch, updates=updates)
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    token, _ = account_svc.mint_session_token("CUST_ADA", "+12055550100")
    response = client.post(
        "/order/api/customer/profile",
        json={"family_name": "Lovelace"},
        headers={"X-Session-Token": token},
    )
    assert response.status_code == 200
    assert response.json()["customer"]["family_name"] == "Lovelace"
    assert "email" not in response.json()["customer"]
    assert updates == [
        {"customer_id": "CUST_ADA", "fields": {"family_name": "Lovelace"}, "version": 4}
    ]


def test_summarize_order_includes_modifiers_and_variation() -> None:
    summary = account_svc.summarize_order(SQUARE_ORDER)
    assert summary["id"] == "ORDER_MILK_TEA"
    assert summary["order_number"] == "42"
    assert summary["total_cents"] == 650
    assert summary["status"] == "ready"
    assert len(summary["items"]) == 1
    item = summary["items"][0]
    assert item["name"] == "Milk Tea"
    assert item["qty"] == 1
    assert item["catalog_object_id"] == "VAR_MILK_TEA"
    assert item["catalog_variation_id"] == "VAR_MILK_TEA"
    assert item["variation_name"] == "Regular"
    assert item["price_cents"] == 650
    assert item["base_price_cents"] == 550
    mods = item["modifiers"]
    assert [m["name"] for m in mods] == ["Oat Milk", "Tapioca Boba", "Regular Sweet"]
    oat = mods[0]
    assert oat["quantity"] == 1
    assert oat["catalog_object_id"] == "MOD_OAT"
    assert oat["price_cents"] == 75
    assert oat["base_price_cents"] == 75
    boba = mods[1]
    assert boba["quantity"] == 2
    assert boba["catalog_object_id"] == "MOD_BOBA"
    assert boba["price_cents"] == 25
    assert boba["base_price_cents"] == 25
    sweet = mods[2]
    assert sweet["quantity"] == 1
    assert sweet["price_cents"] == 0
    assert "catalog_object_id" not in sweet
    dumped = str(summary)
    assert "email" not in dumped
    assert "ada@example.com" not in dumped


def test_line_items_without_modifiers_stay_minimal() -> None:
    items = account_svc._line_items(
        {"line_items": [{"name": "Croissant", "quantity": "2"}]}
    )
    assert items == [{"name": "Croissant", "qty": 2, "modifiers": []}]


def test_order_belongs_to_session_by_customer_or_phone() -> None:
    assert account_svc.order_belongs_to_session(SQUARE_ORDER, "CUST_ADA", "+19999999999")
    phone_only = dict(SQUARE_ORDER, customer_id="")
    assert account_svc.order_belongs_to_session(phone_only, "OTHER", "+12055550100")
    assert not account_svc.order_belongs_to_session(SQUARE_ORDER, "OTHER", "+19999999999")


def test_list_orders_public_json_includes_modifiers(monkeypatch) -> None:
    joins = _stub_square(monkeypatch)
    monkeypatch.setattr(account_svc, "customer_orders", lambda *a, **k: [SQUARE_ORDER])
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    token, _ = account_svc.mint_session_token("CUST_ADA", "+12055550100")
    joins.clear()
    response = client.get("/order/api/orders", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert joins == [False]
    assert "email" not in data["customer"]
    item = data["orders"][0]["items"][0]
    assert item["name"] == "Milk Tea"
    assert item["modifiers"][0]["name"] == "Oat Milk"
    assert item["modifiers"][0]["catalog_object_id"] == "MOD_OAT"
    assert item["modifiers"][0]["price_cents"] == 75
    assert item["catalog_variation_id"] == "VAR_MILK_TEA"


def test_get_order_by_id_is_session_scoped(monkeypatch) -> None:
    joins = _stub_square(monkeypatch)
    monkeypatch.setattr(account_svc, "retrieve_order", lambda *a, **k: dict(SQUARE_ORDER))
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    token, _ = account_svc.mint_session_token("CUST_ADA", "+12055550100")
    joins.clear()
    response = client.get(
        "/order/api/orders/ORDER_MILK_TEA",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert joins == []
    order = data["order"]
    assert order["id"] == "ORDER_MILK_TEA"
    item = order["items"][0]
    assert item["modifiers"][0]["name"] == "Oat Milk"
    assert item["modifiers"][1]["quantity"] == 2
    assert item["catalog_object_id"] == "VAR_MILK_TEA"
    assert "email" not in str(data)


def test_get_order_by_id_wrong_customer_is_403(monkeypatch) -> None:
    _stub_square(monkeypatch)
    foreign = dict(SQUARE_ORDER, customer_id="CUST_OTHER")
    foreign["fulfillments"] = [
        {"state": "COMPLETED", "pickup_details": {"recipient": {"phone_number": "+19998887777"}}}
    ]
    monkeypatch.setattr(account_svc, "retrieve_order", lambda *a, **k: foreign)
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    token, _ = account_svc.mint_session_token("CUST_ADA", "+12055550100")
    response = client.get(
        "/order/api/orders/ORDER_MILK_TEA?customer_id=CUST_OTHER",
        headers={"X-Session-Token": token},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["ok"] is False
    assert "email" not in body
    assert "order" not in body
    assert "customer" not in body


def test_get_order_by_id_not_found_is_404(monkeypatch) -> None:
    _stub_square(monkeypatch)
    monkeypatch.setattr(account_svc, "retrieve_order", lambda *a, **k: None)
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    token, _ = account_svc.mint_session_token("CUST_ADA", "+12055550100")
    response = client.get(
        "/order/api/orders/MISSING",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert response.json()["ok"] is False


def test_get_order_by_id_without_token_is_401() -> None:
    response = client.get("/order/api/orders/ORDER_MILK_TEA")
    assert response.status_code == 401
    assert response.json()["ok"] is False


def test_retrieve_order_calls_square_get(monkeypatch) -> None:
    calls: list = []

    def fake_square(method, path, payload=None, **kw):
        calls.append((method, path, payload))
        return {"order": dict(SQUARE_ORDER)}

    monkeypatch.setattr(account_svc, "_square_json", fake_square)
    order = account_svc.retrieve_order("ORDER_MILK_TEA")
    assert order["id"] == "ORDER_MILK_TEA"
    assert calls == [("GET", "/v2/orders/ORDER_MILK_TEA", None)]


def test_get_order_matches_phone_when_customer_id_missing(monkeypatch) -> None:
    phone_only = dict(SQUARE_ORDER, customer_id="")
    monkeypatch.setattr(account_svc, "retrieve_order", lambda *a, **k: phone_only)
    summary = account_svc.get_order("ORDER_MILK_TEA", "CUST_ADA", "+12055550100")
    assert summary["id"] == "ORDER_MILK_TEA"
    assert summary["items"][0]["modifiers"][0]["name"] == "Oat Milk"


def test_is_paid_order_uses_square_tenders_due_and_completed() -> None:
    assert account_svc.is_paid_order(SQUARE_ORDER) is True
    assert account_svc.is_paid_order(PAID_MAKING_ORDER) is True
    assert account_svc.is_paid_order(UNPAID_OPEN_ORDER) is False
    assert account_svc.is_paid_order({"state": "OPEN"}) is False
    assert account_svc.is_paid_order({"state": "DRAFT"}) is False
    assert account_svc.is_paid_order({"state": "CANCELED", "tenders": [{"id": "t1"}]}) is False
    assert account_svc.is_paid_order({"state": "COMPLETED"}) is True
    assert account_svc.is_paid_order(
        {"state": "OPEN", "net_amount_due_money": {"amount": 0, "currency": "USD"}}
    ) is True
    assert account_svc.is_paid_order(
        {
            "state": "OPEN",
            "tenders": [{"id": "t1", "type": "CARD"}],
            "net_amount_due_money": {"amount": 100, "currency": "USD"},
        }
    ) is False


def test_summarize_paid_orders_excludes_unpaid() -> None:
    summaries = account_svc.summarize_paid_orders(MIXED_CUSTOMER_ORDERS)
    assert [row["id"] for row in summaries] == ["ORDER_MILK_TEA", "ORDER_PAID_MAKING"]
    unpaid_summary = account_svc.summarize_order(UNPAID_OPEN_ORDER)
    assert unpaid_summary["id"] == "ORDER_UNPAID_TICKET"
    assert unpaid_summary["status"] == "making"


def test_list_orders_and_account_payload_are_paid_only(monkeypatch) -> None:
    joins = _stub_square(monkeypatch)
    monkeypatch.setattr(account_svc, "customer_orders", lambda *a, **k: list(MIXED_CUSTOMER_ORDERS))
    monkeypatch.setattr(account_svc, "open_queue_orders", lambda *a, **k: [UNPAID_OPEN_ORDER, PAID_MAKING_ORDER])
    payload = account_svc.list_orders("CUST_ADA", "+12055550100")
    assert [row["id"] for row in payload["orders"]] == ["ORDER_MILK_TEA", "ORDER_PAID_MAKING"]
    assert {row["id"] for row in payload["open_orders"]} == {
        "ORDER_UNPAID_TICKET",
        "ORDER_PAID_MAKING",
    }
    account = account_svc.get_account("CUST_ADA", "+12055550100")
    assert [row["id"] for row in account["orders"]] == ["ORDER_MILK_TEA", "ORDER_PAID_MAKING"]
    assert {row["id"] for row in account["open_orders"]} == {
        "ORDER_UNPAID_TICKET",
        "ORDER_PAID_MAKING",
    }
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    token, _ = account_svc.mint_session_token("CUST_ADA", "+12055550100")
    joins.clear()
    response = client.get("/order/api/orders", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert joins == [False]
    assert [row["id"] for row in data["orders"]] == ["ORDER_MILK_TEA", "ORDER_PAID_MAKING"]
    assert "ORDER_UNPAID_TICKET" not in [row["id"] for row in data["orders"]]
    assert "email" not in data["customer"]
    login = account_svc.login_or_signup({"phone": "2055550100"})
    assert [row["id"] for row in login["orders"]] == ["ORDER_MILK_TEA", "ORDER_PAID_MAKING"]
    assert "email" not in login["customer"]


def test_get_order_unpaid_owned_is_404(monkeypatch) -> None:
    _stub_square(monkeypatch)
    monkeypatch.setattr(account_svc, "retrieve_order", lambda *a, **k: dict(UNPAID_OPEN_ORDER))
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    token, _ = account_svc.mint_session_token("CUST_ADA", "+12055550100")
    response = client.get(
        "/order/api/orders/ORDER_UNPAID_TICKET",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    body = response.json()
    assert body["ok"] is False
    assert "order" not in body
    assert "email" not in body
    with pytest.raises(account_svc.AccountError) as missing:
        account_svc.get_order("ORDER_UNPAID_TICKET", "CUST_ADA", "+12055550100")
    assert missing.value.status_code == 404


def test_avatar_file_store_roundtrip(monkeypatch, tmp_path) -> None:
    store = tmp_path / "avatars.json"
    monkeypatch.setenv("SUNSHINE_AVATAR_STORE", str(store))
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    recipe = account_svc.sanitize_avatar({"skin": "NOPE", "hat": "sun"})
    assert recipe["skin"] == "peach"
    assert recipe["hat"] == "sun"
    saved = account_svc.upsert_account_avatar(
        "CUST_ADA",
        {
            "player_id": "plr_ada",
            "username": "ada_walk",
            "display_name": "Ada",
            "avatar_recipe": recipe,
        },
    )
    assert saved["ok"] is True
    assert saved["customized"] is True
    assert saved["source"] == "file"
    assert saved["public"]["username"] == "ada_walk"
    assert saved["public"]["avatar"]["hat"] == "sun"
    got = account_svc.get_account_avatar("CUST_ADA")
    assert got["public"]["player_id"] == "plr_ada"
    empty = account_svc.get_account_avatar("CUST_MISSING")
    assert empty["customized"] is False
    with pytest.raises(account_svc.AccountError):
        account_svc.upsert_account_avatar(
            "CUST_OTHER",
            {
                "username": "ada_walk",
                "display_name": "Other",
                "avatar_recipe": account_svc.default_avatar(),
            },
        )


def test_avatar_http_requires_session_and_saves(monkeypatch, tmp_path) -> None:
    _stub_square(monkeypatch)
    monkeypatch.setenv("SESSION_SECRET", "test-account-session-secret")
    monkeypatch.setenv("SUNSHINE_AVATAR_STORE", str(tmp_path / "avatars.json"))
    monkeypatch.delenv("SQUARE_ACCESS_TOKEN", raising=False)
    denied = client.get("/order/api/account/avatar")
    assert denied.status_code == 401
    token, _ = account_svc.mint_session_token("CUST_ADA", "+12055550100")
    headers = {"Authorization": f"Bearer {token}"}
    empty = client.get("/order/api/account/avatar", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["customized"] is False
    put = client.put(
        "/order/api/account/avatar",
        headers=headers,
        json={
            "player_id": "plr_http",
            "username": "ada_http",
            "display_name": "Ada",
            "avatar": {"hat": "beanie", "outfit": "wine"},
        },
    )
    assert put.status_code == 200
    body = put.json()
    assert body["ok"] is True
    assert body["public"]["avatar"]["hat"] == "beanie"
    got = client.get("/order/api/account/avatar", headers=headers)
    assert got.json()["public"]["username"] == "ada_http"
