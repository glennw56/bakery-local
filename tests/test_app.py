"""Smoke tests: dashboard, board, reports, notes, loyalty, seed makes the board live."""

from __future__ import annotations

import os
import tempfile

# Point the app at a throwaway SQLite file before importing it.
_fd, _db = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["BAKERY_DB"] = _db

from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)


def test_dashboard_ok() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Notes" in response.text
    assert "Reports" in response.text
    assert "Drink board" in response.text


def test_board_ok() -> None:
    response = client.get("/board")
    assert response.status_code == 200
    assert "No drinks in the last 15 minutes" in response.text


def test_reports_ok() -> None:
    response = client.get("/reports")
    assert response.status_code == 200


def test_seed_then_board_contains_a_drink() -> None:
    from scripts.seed_demo import main as seed_main

    seed_main()
    response = client.get("/board")
    assert response.status_code == 200
    assert "Fruit Tea" in response.text
    assert "Strawberry" in response.text

    reports = client.get("/reports")
    assert reports.status_code == 200
    assert "Viet Coffee" in reports.text


def test_create_note_then_list_contains_it() -> None:
    title = "Test taro restock"
    body = "Need more taro for Saturday."
    created = client.post("/notes", data={"title": title, "body": body}, follow_redirects=True)
    assert created.status_code == 200

    listed = client.get("/notes")
    assert listed.status_code == 200
    assert title in listed.text
    assert body in listed.text


def test_board_newest_first_and_click_clears() -> None:
    from scripts.seed_demo import main as seed_main

    seed_main()
    page = client.get("/board")
    assert page.status_code == 200
    assert "newest first" in page.text
    assert "hx-delete=" in page.text

    # Newest seed ticket is Milk Tea / Table 3 (1 min ago). It should appear
    # before the oldest Fruit Tea / Walk-in (14 min ago).
    milk_at = page.text.find("Table 3")
    fruit_at = page.text.find("Walk-in")
    assert milk_at != -1 and fruit_at != -1
    assert milk_at < fruit_at

    import re

    ids = re.findall(r'hx-delete="/board/tickets/(\d+)', page.text)
    assert ids
    first_id = ids[0]
    gone = client.delete(f"/board/tickets/{first_id}", headers={"HX-Request": "true"})
    assert gone.status_code == 200
    assert f"/board/tickets/{first_id}" not in gone.text
    remaining = client.get("/board")
    assert remaining.status_code == 200
    assert f"/board/tickets/{first_id}" not in remaining.text


def test_loyalty_ok() -> None:
    response = client.get("/loyalty")
    assert response.status_code == 200
    assert "Loyalty" in response.text


def test_seed_then_loyalty_contains_a_name_and_points() -> None:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import LoyaltyMember
    from scripts.seed_demo import main as seed_main

    seed_main()
    with SessionLocal() as db:
        named = db.scalars(
            select(LoyaltyMember).where(LoyaltyMember.given_name != "").limit(1)
        ).first()
        assert named is not None
        name = named.given_name
        points = named.points
        member_id = named.id
    listed = client.get("/loyalty")
    assert listed.status_code == 200
    assert name in listed.text
    assert str(points) in listed.text

    dossier = client.get(f"/loyalty/{member_id}")
    assert dossier.status_code == 200
    assert name in dossier.text


def test_loyalty_csv_has_header_and_member_line() -> None:
    from scripts.seed_demo import main as seed_main

    seed_main()
    response = client.get("/loyalty.csv")
    assert response.status_code == 200
    text = response.text.lstrip("\ufeff")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines
    header = lines[0].lower()
    assert "area_code" in header
    assert "metro" in header
    assert "state" in header
    assert len(lines) >= 2
    assert "points" in header
