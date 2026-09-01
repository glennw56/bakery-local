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



def test_groups_two_drinks_one_order() -> None:
    payload = [
        [
            "2026-09-01T18:50:44.246Z",
            "ORDER_GROUP_1",
            ["1 Matcha Latte ", "Matcha Option Strawberry Matcha"],
            ["1 Vietnamese Coffee ", "Sweet Level 25%"],
        ]
    ]
    from app.getorders import live_order_views

    orders = live_order_views(24 * 60, payload=payload)
    assert orders is not None
    assert len(orders) == 1
    assert orders[0]["order_id"] == "ORDER_GROUP_1"
    names = [d["drink_name"] for d in orders[0]["drinks"]]
    assert names == ["Matcha Latte", "Vietnamese Coffee"]


def test_cleared_order_hidden() -> None:
    payload = [
        [
            "2026-09-01T18:50:44.246Z",
            "ORDER_GROUP_1",
            ["1 Matcha Latte ", "Matcha Option Strawberry Matcha"],
        ],
        [
            "2026-09-01T18:12:19.110Z",
            "ORDER_GROUP_2",
            ["1 Vietnamese Coffee ", "Sweet Level 25%"],
        ],
    ]
    from app.getorders import live_order_views

    orders = live_order_views(24 * 60, payload=payload, cleared=["ORDER_GROUP_1"])
    assert orders is not None
    assert [o["order_id"] for o in orders] == ["ORDER_GROUP_2"]
