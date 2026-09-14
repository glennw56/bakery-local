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
