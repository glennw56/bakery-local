"""Service split: drinks never serves loyalty; desk is not the tablet."""

from __future__ import annotations

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


def test_desk_loyalty_ok_board_404(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "desk")
    from scripts.seed_demo import main as seed_main

    seed_main()
    loyalty = client.get("/loyalty")
    assert loyalty.status_code == 200
    board = client.get("/board")
    assert board.status_code == 404
    ingest = client.post("/internal/ingest")
    assert ingest.status_code == 404


def test_drinks_board_includes_ding_script(monkeypatch) -> None:
    monkeypatch.setenv("BAKERY_SERVICE", "drinks")
    page = client.get("/board")
    assert page.status_code == 200
    assert "/static/board-ding.js" in page.text
