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
    assert "grouped by order" in page.text
    assert "hx-delete=" in page.text

    # Newest seed ticket is Milk Tea / Table 3 (1 min ago). It should appear
    # before the oldest Fruit Tea / Walk-in (14 min ago).
    milk_at = page.text.find("Table 3")
    fruit_at = page.text.find("Walk-in")
    assert milk_at != -1 and fruit_at != -1
    assert milk_at < fruit_at

    import re

    ids = re.findall(r'hx-delete="/board/orders/([^"?]+)', page.text)
    assert ids
    first_id = ids[0]
    gone = client.delete(f"/board/orders/{first_id}", headers={"HX-Request": "true"})
    assert gone.status_code == 200
    assert f"/board/orders/{first_id}" not in gone.text
    remaining = client.get("/board")
    assert remaining.status_code == 200
    assert f"/board/orders/{first_id}" not in remaining.text


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
    listed = client.get("/loyalty?q=" + name)
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


def test_weekend_empty_tables_load_and_nonempty_stay_put() -> None:
    """init_db loads sample weekend JSON only when weekend_days is empty."""
    from sqlalchemy import delete, func, select

    from app.db import SessionLocal, init_db
    from app.models import DrinkTicket, LoyaltyMember, SalesSummary, WeekendDay, WeekendItem

    with SessionLocal() as db:
        reports = db.scalar(select(func.count(SalesSummary.id))) or 0
        loyalty = db.scalar(select(func.count(LoyaltyMember.id))) or 0
        tickets = db.scalar(select(func.count(DrinkTicket.id))) or 0
        db.execute(delete(WeekendItem))
        db.execute(delete(WeekendDay))
        db.commit()
        assert (db.scalar(select(func.count(WeekendDay.id))) or 0) == 0

    init_db()

    with SessionLocal() as db:
        loaded = db.scalar(select(func.count(WeekendDay.id))) or 0
        assert loaded >= 2
        assert (db.scalar(select(func.count(SalesSummary.id))) or 0) == reports
        assert (db.scalar(select(func.count(LoyaltyMember.id))) or 0) == loyalty
        assert (db.scalar(select(func.count(DrinkTicket.id))) or 0) == tickets
        day = db.scalars(select(WeekendDay).order_by(WeekendDay.sold_on)).first()
        assert day is not None
        sold_on = day.sold_on
        marker = int(day.tickets) + 999
        day.tickets = marker
        db.commit()

    init_db()

    with SessionLocal() as db:
        day = db.scalar(select(WeekendDay).where(WeekendDay.sold_on == sold_on))
        assert day is not None
        assert day.tickets == marker
        assert (db.scalar(select(func.count(WeekendDay.id))) or 0) == loaded
        assert (db.scalar(select(func.count(SalesSummary.id))) or 0) == reports
        assert (db.scalar(select(func.count(LoyaltyMember.id))) or 0) == loyalty
        assert (db.scalar(select(func.count(DrinkTicket.id))) or 0) == tickets

    page = client.get("/weekend")
    assert page.status_code == 200
    assert "Vietnamese Coffee" in page.text


def _loyalty_table_rows(html: str) -> int:
    import re

    match = re.search(
        r'<table class="sales loyalty-table">.*?<tbody>(.*?)</tbody>',
        html,
        re.S,
    )
    assert match is not None
    return len(re.findall(r"<tr\b", match.group(1)))


def test_loyalty_paginates_and_csv_is_full_filter() -> None:
    import re

    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.loyalty import PAGE_SIZE
    from app.models import LoyaltyMember

    prefix = "PageQUnique"
    extra = PAGE_SIZE + 15
    with SessionLocal() as db:
        have = db.scalar(
            select(func.count(LoyaltyMember.id)).where(LoyaltyMember.given_name == prefix)
        ) or 0
        for i in range(int(have), extra):
            db.add(
                LoyaltyMember(
                    square_loyalty_id=f"pageq-{i:03d}",
                    given_name=prefix,
                    family_name=f"{i:03d}",
                    phone=f"+1205555{i:04d}",
                    points=i,
                    lifetime_points=i,
                    area_code="205",
                    area_metro="Birmingham",
                    area_state="AL",
                    area_region="local",
                )
            )
        db.commit()

    page1 = client.get(f"/loyalty?q={prefix}")
    assert page1.status_code == 200
    assert _loyalty_table_rows(page1.text) == PAGE_SIZE
    assert "Page 1 of 2" in page1.text
    assert "Next" in page1.text
    assert f"q={prefix}" in page1.text
    assert "page=2" in page1.text
    assert re.search(
        rf'stat-label">Members</span>\s*<strong class="stat-value">{extra}</strong>',
        page1.text,
    )
    assert f'<td class="num">{extra}</td>' in page1.text

    page2 = client.get(f"/loyalty?q={prefix}&page=2")
    assert page2.status_code == 200
    assert _loyalty_table_rows(page2.text) == extra - PAGE_SIZE
    assert "Page 2 of 2" in page2.text
    assert "Prev" in page2.text
    assert f"q={prefix}" in page2.text
    assert "page=1" in page2.text
    assert re.search(
        rf'stat-label">Members</span>\s*<strong class="stat-value">{extra}</strong>',
        page2.text,
    )

    csv = client.get(f"/loyalty.csv?q={prefix}")
    assert csv.status_code == 200
    lines = [ln for ln in csv.text.lstrip("\ufeff").splitlines() if ln.strip()]
    assert len(lines) - 1 == extra
