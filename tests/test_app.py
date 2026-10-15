"""Smoke tests: dashboard, board, reports, notes, loyalty, seed makes the board live.

The drink-order tests hit /test/drinks only. They must not change /board.
"""

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


def test_weekend_ok() -> None:
    from scripts.seed_demo import main as seed_main

    seed_main()
    response = client.get("/weekend")
    assert response.status_code == 200
    assert "Aug 29" in response.text
    assert "Vietnamese Coffee" in response.text


def _reset_drink_orders() -> None:
    cleared = client.post("/test/drinks/reset", headers={"HX-Request": "true"})
    assert cleared.status_code == 200


def _place_drink_order(ticket_name: str, **qty_by_key: int):
    data = {"ticket_name": ticket_name}
    for key, qty in qty_by_key.items():
        data[f"qty_{key}"] = str(qty)
    response = client.post("/test/drinks/orders", data=data, headers={"HX-Request": "true"})
    assert response.status_code == 200, response.text
    return response


def _tickets(text: str) -> list[str]:
    import re

    return re.findall(r'data-ticket="([^"]+)"', text)


def _waiting(text: str) -> list[str]:
    import re

    return re.findall(r'data-waiting="([^"]+)"', text)


def _line_url(text: str, order: str, drink: str) -> str:
    import re

    match = re.search(
        rf'data-order="{re.escape(order)}"\s+data-drink="{re.escape(drink)}"[\s\S]*?hx-delete="(/test/drinks/lines/\d+)"',
        text,
    )
    assert match, text
    return match.group(1)


def test_drink_order_page_uses_the_kitchen_menu() -> None:
    page = client.get("/test/drinks")
    assert page.status_code == 200
    assert "TEST" in page.text
    assert "3 oldest open orders" in page.text
    assert "oldest first" in page.text
    for name in ("Fruit Tea", "Viet Coffee", "Milk Tea", "Matcha", "Biscoff"):
        assert name in page.text
    assert "Strawberry Bursting" in page.text
    assert "Salted Caramel" in page.text
    assert "Strawberry Matcha" in page.text

    live = client.get("/board")
    assert live.status_code == 200
    assert "newest first" in live.text
    assert "3 oldest open orders" not in live.text


def test_drink_order_rejects_empty_and_ignores_off_menu() -> None:
    _reset_drink_orders()
    empty = client.post(
        "/test/drinks/orders",
        data={"ticket_name": "Nobody", "qty_0": "0", "drink_name": "Croissant"},
        headers={"HX-Request": "true"},
    )
    assert empty.status_code == 200
    assert "Add at least one drink" in empty.text
    assert 'data-ticket="Nobody"' not in empty.text

    bad_qty = client.post(
        "/test/drinks/orders",
        data={"ticket_name": "Nobody", "qty_0": "nope"},
        headers={"HX-Request": "true"},
    )
    assert "whole number" in bad_qty.text
    assert 'data-ticket="Nobody"' not in bad_qty.text

    placed = _place_drink_order("Counter", **{"0": 1})
    # An off-menu name posted beside a real drink does not become a line.
    sneaky = client.post(
        "/test/drinks/orders",
        data={
            "ticket_name": "Sneaky",
            "qty_1": "1",
            "drink_name": "Croissant",
            "qty_99": "4",
        },
        headers={"HX-Request": "true"},
    )
    assert sneaky.status_code == 200
    assert 'data-ticket="Sneaky"' in sneaky.text
    assert "Croissant" not in sneaky.text
    assert "Viet Coffee" in sneaky.text
    assert "Counter" in placed.text or "Counter" in sneaky.text


def test_three_oldest_orders_and_per_drink_clear() -> None:
    _reset_drink_orders()
    _place_drink_order("Lane-1", **{"0": 1})
    _place_drink_order("Lane-2", **{"3": 1, "4": 1})
    _place_drink_order("Lane-3", **{"1": 1})
    _place_drink_order("Lane-4", **{"2": 1})
    board = _place_drink_order("Lane-5", **{"5": 1})
    assert _tickets(board.text) == ["Lane-1", "Lane-2", "Lane-3"]
    assert _waiting(board.text) == ["Lane-4", "Lane-5"]
    assert "Matcha" in board.text
    assert "Biscoff" in board.text

    matcha = _line_url(board.text, "Lane-2", "Matcha")
    partial = client.delete(matcha, headers={"HX-Request": "true"})
    assert partial.status_code == 200
    assert _tickets(partial.text) == ["Lane-1", "Lane-2", "Lane-3"]
    assert _waiting(partial.text) == ["Lane-4", "Lane-5"]
    assert "Matcha" not in partial.text
    assert _line_url(partial.text, "Lane-2", "Biscoff")

    biscoff = _line_url(partial.text, "Lane-2", "Biscoff")
    slid = client.delete(biscoff, headers={"HX-Request": "true"})
    assert slid.status_code == 200
    assert _tickets(slid.text) == ["Lane-1", "Lane-3", "Lane-4"]
    assert _waiting(slid.text) == ["Lane-5"]
    assert "Lane-2" not in slid.text
    assert "Biscoff" not in slid.text

    lane1 = _line_url(slid.text, "Lane-1", "Fruit Tea")
    done = client.delete(lane1, headers={"HX-Request": "true"})
    assert _tickets(done.text) == ["Lane-3", "Lane-4", "Lane-5"]
    assert _waiting(done.text) == []


def test_queue_fifo_three_oldest_partial_clear_and_slide_in() -> None:
    """Queue rules: 3 oldest in FIFO order, one drink stays on its order, full clear slides the next in.

    Order names are not alphabetical, so a name sort would not match this sequence.
    Six orders are placed. Only the three oldest are on the board.
    """
    _reset_drink_orders()
    _place_drink_order("Zed", **{"0": 1})
    _place_drink_order("Amy", **{"3": 1, "4": 1})
    _place_drink_order("Mia", **{"1": 1})
    _place_drink_order("Bob", **{"2": 1})
    _place_drink_order("Ned", **{"5": 1})
    board = _place_drink_order("Cal", **{"6": 1})

    assert _tickets(board.text) == ["Zed", "Amy", "Mia"]
    assert _waiting(board.text) == ["Bob", "Ned", "Cal"]
    assert "5 open" not in board.text
    assert "6 open" in board.text
    assert "3 waiting" in board.text

    partial = client.delete(
        _line_url(board.text, "Amy", "Matcha"),
        headers={"HX-Request": "true"},
    )
    assert partial.status_code == 200
    assert _tickets(partial.text) == ["Zed", "Amy", "Mia"]
    assert _waiting(partial.text) == ["Bob", "Ned", "Cal"]
    assert "Matcha" not in partial.text
    assert _line_url(partial.text, "Amy", "Biscoff")

    slid = client.delete(
        _line_url(partial.text, "Amy", "Biscoff"),
        headers={"HX-Request": "true"},
    )
    assert slid.status_code == 200
    assert "Amy" not in slid.text
    assert "Matcha" not in slid.text
    assert "Biscoff" not in slid.text
    assert _tickets(slid.text) == ["Zed", "Mia", "Bob"]
    assert _waiting(slid.text) == ["Ned", "Cal"]


def test_clearing_one_of_a_double_leaves_the_order() -> None:
    _reset_drink_orders()
    placed = _place_drink_order("Double", **{"0": 2})
    assert "×2" in placed.text
    url = _line_url(placed.text, "Double", "Fruit Tea")
    once = client.delete(url, headers={"HX-Request": "true"})
    assert 'data-ticket="Double"' in once.text
    assert "×1" in once.text
    assert "×2" not in once.text
    url = _line_url(once.text, "Double", "Fruit Tea")
    twice = client.delete(url, headers={"HX-Request": "true"})
    assert 'data-ticket="Double"' not in twice.text


def test_drink_orders_do_not_land_on_the_live_board() -> None:
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import DrinkTicket

    _reset_drink_orders()
    with SessionLocal() as db:
        before = db.scalar(select(func.count(DrinkTicket.id))) or 0
    _place_drink_order("Lane-1", **{"0": 1})
    live = client.get("/board")
    assert "newest first" in live.text
    assert "Lane-1" not in live.text
    assert "3 oldest open orders" not in live.text
    with SessionLocal() as db:
        after = db.scalar(select(func.count(DrinkTicket.id))) or 0
    assert after == before


def test_weekend_csv_has_header() -> None:
    from scripts.seed_demo import main as seed_main

    seed_main()
    response = client.get("/weekend.csv")
    assert response.status_code == 200
    text = response.text.lstrip("\ufeff")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines
    header = lines[0].lower()
    assert "metric" in header
    assert "last_sat" in header
    assert "this_sat" in header
    assert "delta" in header
