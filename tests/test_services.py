"""Service split: drinks never serves loyalty; desk is not the tablet."""

from __future__ import annotations

from pathlib import Path

from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_drinks_board_ok_loyalty_404(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "drinks")
    board = client.get("/board")
    assert board.status_code == 200
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "drinks"
    loyalty = client.get("/loyalty")
    assert loyalty.status_code == 404
    ingest = client.post("/internal/ingest")
    assert ingest.status_code == 401


def test_desk_loyalty_gated_board_404(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "desk")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    loyalty = client.get("/loyalty", follow_redirects=False)
    assert loyalty.status_code in (401, 302, 303)
    if loyalty.status_code in (302, 303):
        assert "/login" in loyalty.headers.get("location", "")
    board = client.get("/board")
    assert board.status_code == 404
    ingest = client.post("/internal/ingest")
    assert ingest.status_code == 404


def test_drinks_board_includes_ding_script(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "drinks")
    page = client.get("/board")
    assert page.status_code == 200
    assert "/static/board-ding.js" in page.text
    assert "Tap to turn on the ding" in page.text
    assert "You will not hear new drinks until you do" in page.text
    assert "apple-mobile-web-app-capable" in page.text
    assert "/static/board-cleared.js" in page.text
    ding_js = Path(__file__).resolve().parents[1] / "static" / "board-ding.js"
    assert "/static/ding.wav" in ding_js.read_text()
