"""Fixture-only getreports parser. No live Square token. Phones in fixtures are fake."""

from __future__ import annotations

import json
from pathlib import Path

from app.getreports import (
    members_from_payload,
    reports_from_payload,
    scorecard_from_payload,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_getreports.json"
LOYALTY = Path(__file__).resolve().parent / "fixtures" / "sample_getreports_loyalty.json"


def test_reports_maps_today_and_week_top_items() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ctx = reports_from_payload(payload)
    assert ctx["today_tickets"] == 21
    assert ctx["today_cents"] == 46732
    assert len(ctx["summaries"]) == 2
    assert ctx["summaries"][0].note == "Today"
    assert ctx["summaries"][0].tickets == 21
    assert ctx["summaries"][1].note == "Week to date"
    assert ctx["summaries"][1].tickets == 46
    names = [row[0] for row in ctx["top_items"]]
    assert "Vietnamese Coffee" in names
    assert "Birthday Cake Macaron" in names
    assert ctx["modifiers_by_drink"] == {}
    assert ctx["merch"] == []
    # 2026-09-01 is a Tuesday → today counts as weekday
    assert ctx["weekday_tickets"] == 21
    assert ctx["weekend_tickets"] == 0


def test_live_reports_unset_returns_none(monkeypatch) -> None:
    from app import getreports

    monkeypatch.delenv("GETREPORTS_URL", raising=False)
    assert getreports.live_reports_context() is None
    assert getreports.live_scorecard() is None
    assert getreports.live_members() is None


def test_gated_loyalty_is_empty() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["loyalty"]["gated"] is True
    assert members_from_payload(payload) == []


def test_loyalty_maps_fake_phones_and_hometown() -> None:
    payload = json.loads(LOYALTY.read_text(encoding="utf-8"))
    rows = members_from_payload(payload)
    assert len(rows) == 2
    by_id = {r.id: r for r in rows}
    hi = by_id["LOY_FAKE_1"]
    assert hi.points == 150
    assert hi.phone == "+15555550100"
    local = by_id["LOY_FAKE_2"]
    assert local.area_code == "205"
    assert local.area_region == "local"
    from app.getreports import filter_members

    high = filter_members(rows, segment="high")
    assert [m.id for m in high] == ["LOY_FAKE_1"]
    local_rows = filter_members(rows, segment="local")
    assert [m.id for m in local_rows] == ["LOY_FAKE_2"]


def test_scorecard_today_vs_week_drink_mix() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    card = scorecard_from_payload(payload)
    assert card["empty"] is False
    assert card["this"]["tickets"] == 21
    assert card["last"]["tickets"] == 46
    mix = {row["name"]: row for row in card["drink_mix"]}
    assert mix["Vietnamese Coffee"]["this"] == 5
    assert mix["Vietnamese Coffee"]["last"] == 6
    top_names = [row["name"] for row in card["top"]]
    assert top_names[0] == "Vietnamese Coffee"
    assert "Vietnamese Coffee" in card["pair_sentence"]
