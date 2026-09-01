"""Fixture-only getorders parser. No live Square token."""

from __future__ import annotations

import json
from pathlib import Path

from app.getorders import tickets_from_payload

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_getorders.json"


def test_getorders_maps_milk_tea_skips_pastry() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = tickets_from_payload(payload)
    assert len(rows) == 1
    row = rows[0]
    assert row["drink_name"] == "Milk Tea"
    assert row["qty"] == 1
    mods = json.loads(row["modifiers_json"])
    assert [m["value"] for m in mods] == [
        "Flavor Taro",
        "Sweet Level Extra Sweet",
        "Boba Brown Sugar Jelly",
    ]
    assert row["source"] == "getorders"
    assert row["square_order_id"] == "ORDER_FAKE_GETORDERS_1"
    assert row["square_line_uid"] == "ORDER_FAKE_GETORDERS_1:0"
