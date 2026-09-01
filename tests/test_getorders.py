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
    assert [m["value"] for m in row["modifiers"]] == [
        "Flavor Taro",
        "Sweet Level Extra Sweet",
        "Boba Brown Sugar Jelly",
    ]
    assert row["source"] == "getorders"
    assert row["square_order_id"] == "ORDER_FAKE_GETORDERS_1"
    assert row["square_line_uid"] == "ORDER_FAKE_GETORDERS_1:0"


def test_live_views_parse_and_render_no_id() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    from app.getorders import live_ticket_views

    views = live_ticket_views(24 * 60, payload=payload)
    assert views is not None
    assert len(views) == 1
    assert views[0]["id"] is None
    assert views[0]["drink_name"] == "Milk Tea"


def test_live_views_unset_returns_none(monkeypatch) -> None:
    from app import getorders

    monkeypatch.delenv("GETORDERS_URL", raising=False)
    assert getorders.live_ticket_views(180) is None
