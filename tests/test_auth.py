"""Desk password gate. Fake password in test env only. Never a real secret."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth import ADMIN_EMAIL, COOKIE_NAME
from app.main import app

TEST_PASS = "test-desk-pass"


def _client() -> TestClient:
    return TestClient(app)


def _login(client: TestClient, email: str = ADMIN_EMAIL, password: str = TEST_PASS, **kw):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
        **kw,
    )


def test_unauthenticated_reports_and_loyalty_redirect_to_login(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "desk")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_PASS)
    client = _client()
    reports = client.get("/reports", follow_redirects=False)
    assert reports.status_code in (401, 302, 303)
    if reports.status_code in (302, 303):
        assert "/login" in reports.headers.get("location", "")
    loyalty = client.get("/loyalty", follow_redirects=False)
    assert loyalty.status_code in (401, 302, 303)
    if loyalty.status_code in (302, 303):
        assert "/login" in loyalty.headers.get("location", "")
    home = client.get("/", follow_redirects=False)
    assert home.status_code in (401, 302, 303)
    weekend = client.get("/weekend", follow_redirects=False)
    assert weekend.status_code in (401, 302, 303)
    csv = client.get("/loyalty.csv", follow_redirects=False)
    assert csv.status_code in (401, 302, 303)
    health = client.get("/health")
    assert health.status_code == 200
    login = client.get("/login")
    assert login.status_code == 200
    assert 'name="email"' in login.text
    assert 'name="password"' in login.text


def test_login_then_reports_and_loyalty_ok(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "desk")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_PASS)
    client = _client()
    posted = _login(client)
    assert posted.status_code in (302, 303)
    cookie = posted.headers.get("set-cookie", "")
    assert COOKIE_NAME in cookie
    assert "httponly" in cookie.lower()
    assert TEST_PASS not in cookie
    reports = client.get("/reports", follow_redirects=False)
    assert reports.status_code == 200
    loyalty = client.get("/loyalty", follow_redirects=False)
    assert loyalty.status_code == 200
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 200


def test_wrong_password_rejected(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "desk")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_PASS)
    client = _client()
    posted = _login(client, password="wrong-pass")
    assert posted.status_code in (401, 403)
    assert TEST_PASS not in posted.text
    reports = client.get("/reports", follow_redirects=False)
    assert reports.status_code in (401, 302, 303)


def test_wrong_email_rejected(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "desk")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_PASS)
    client = _client()
    posted = _login(client, email="not-glenn@example.com")
    assert posted.status_code in (401, 403)
    reports = client.get("/reports", follow_redirects=False)
    assert reports.status_code in (401, 302, 303)


def test_drinks_still_404s_loyalty_with_password_set(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "drinks")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_PASS)
    client = _client()
    board = client.get("/board")
    assert board.status_code == 200
    loyalty = client.get("/loyalty", follow_redirects=False)
    assert loyalty.status_code == 404
    reports = client.get("/reports", follow_redirects=False)
    assert reports.status_code == 404
    login = client.get("/login", follow_redirects=False)
    assert login.status_code == 404


def test_laptop_unlocked_without_password(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("BAKERY_SERVICE", "laptop")
    client = _client()
    reports = client.get("/reports", follow_redirects=False)
    assert reports.status_code == 200
    loyalty = client.get("/loyalty", follow_redirects=False)
    assert loyalty.status_code == 200


def test_desk_fail_closed_without_password(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("BAKERY_SERVICE", "desk")
    client = _client()
    reports = client.get("/reports", follow_redirects=False)
    assert reports.status_code in (401, 302, 303)
    login = client.get("/login")
    assert login.status_code == 200
    posted = _login(client, password=TEST_PASS)
    assert posted.status_code in (401, 403)
    still = client.get("/loyalty", follow_redirects=False)
    assert still.status_code in (401, 302, 303)


def test_logout_clears_session(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "desk")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_PASS)
    client = _client()
    _login(client)
    assert client.get("/reports", follow_redirects=False).status_code == 200
    out = client.post("/logout", follow_redirects=False)
    assert out.status_code in (302, 303)
    blocked = client.get("/reports", follow_redirects=False)
    assert blocked.status_code in (401, 302, 303)
