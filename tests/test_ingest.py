"""Fixture-only drink ingest tests. No live Square token."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

if "BAKERY_DB" not in os.environ:
    _fd, _db = tempfile.mkstemp(suffix=".db")
    os.close(_fd)
    os.environ["BAKERY_DB"] = _db

from app.db import SessionLocal, init_db  # noqa: E402
from app.drinks import is_drink, tickets_from_order, upsert_tickets  # noqa: E402
from app.models import DrinkTicket  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_order.json"


def test_is_drink_classifier() -> None:
    assert is_drink("Milk Tea")
    assert is_drink("Fruit Tea")
    assert is_drink("Vietnamese Coffee")
    assert is_drink("Viet Coffee")
    assert is_drink("Coffee Latte")
    assert is_drink("Latte")
    assert is_drink("Matcha")
    assert is_drink("Biscoff")
    assert is_drink("Taro Boba")
    assert is_drink("Lemonade")
    assert is_drink("Iced Coffee")
    assert not is_drink("Pink Lemonade Entrement")
    assert not is_drink("Pink Lemonade Entremet")
    assert not is_drink("Coffee Cake")
    assert not is_drink("Croissant")
    assert not is_drink("Cinnamon Roll")
    assert not is_drink("Cookie")
    assert not is_drink("Danish")
    assert not is_drink("Merch")
    assert not is_drink("Wholesale")
    assert not is_drink("Entremet")


def test_fixture_maps_one_drink_skips_pastry() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = tickets_from_order(data["order"], data["payment"])
    assert len(rows) == 1
    row = rows[0]
    assert row["drink_name"] == "Milk Tea"
    mods = json.loads(row["modifiers_json"])
    assert len(mods) == 3
    assert [m["value"] for m in mods] == ["Taro", "Extra Sweet", "Brown Sugar Jelly"]
    assert all(m["group"] == "" for m in mods)
    assert row["qty"] == 1
    assert row["ticket_name"] == "Regular"
    assert row["source"] == "pos"
    assert row["square_order_id"] == "ORDER_FAKE_TEST_001"
    assert row["square_line_uid"] == "LINE_FAKE_MILK_TEA"
    assert "Cinnamon Roll" not in {r["drink_name"] for r in rows}
    assert "Entrement" not in rows[0]["drink_name"]


def test_duplicate_square_line_skipped() -> None:
    init_db()
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = tickets_from_order(data["order"], data["payment"])
    with SessionLocal() as db:
        ins, skip = upsert_tickets(db, rows)
        assert ins == 1
        assert skip == 0
    with SessionLocal() as db:
        ins, skip = upsert_tickets(db, rows)
        assert ins == 0
        assert skip == 1
        count = db.scalar(
            select(func.count()).select_from(DrinkTicket).where(
                DrinkTicket.square_order_id == "ORDER_FAKE_TEST_001"
            )
        )
        assert count == 1
