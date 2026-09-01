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
from app.ingest import apply_tickets, is_drink, tickets_from_order  # noqa: E402
from app.models import DrinkTicket  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_order.json"


def test_is_drink_classifier() -> None:
    assert is_drink("Milk Tea")
    assert is_drink("Fruit Tea")
    assert is_drink("Vietnamese Coffee")
    assert not is_drink("Cinnamon Roll")
    assert not is_drink("Pink Lemonade Entrement")
    assert not is_drink("Coffee Cake")


def test_fixture_maps_one_drink_skips_pastry() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = tickets_from_order(data["order"], data["payment"])
    assert len(rows) == 1
    row = rows[0]
    assert row["drink_name"] == "Milk Tea"
    mods = json.loads(row["modifiers_json"])
    assert [m["value"] for m in mods] == ["Taro", "Extra Sweet", "Brown Sugar Jelly"]
    assert row["source"] == "POS"
    assert row["square_order_id"] == "order_fake_001"
    assert row["square_line_uid"] == "line_drink_001"


def test_duplicate_square_line_skipped() -> None:
    init_db()
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = tickets_from_order(data["order"], data["payment"])
    with SessionLocal() as db:
        first = apply_tickets(db, rows)
        db.commit()
        second = apply_tickets(db, rows)
        db.commit()
        assert first["inserted"] == 1
        assert second["inserted"] == 0
        assert second["skipped"] == 1
        assert db.query(DrinkTicket).filter(DrinkTicket.square_order_id == "order_fake_001").count() == 1
