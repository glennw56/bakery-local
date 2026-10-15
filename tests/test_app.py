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


def _passprnt_query(url: str) -> dict:
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    assert parsed.scheme == "starpassprnt"
    assert parsed.netloc == "v1"
    assert parsed.path == "/print/nopreview"
    return parse_qs(parsed.query)


def test_passprnt_prints_one_ticket_per_drink() -> None:
    """Three drinks make three tickets. A failed return re-queues only that drink."""
    _reset_drink_orders()
    placed = _place_drink_order("Table 4", **{"0": 1, "1": 1, "2": 1})
    assert placed.headers.get("X-Print-Order")
    order_id = placed.headers["X-Print-Order"]
    line_ids = [int(part) for part in placed.headers["X-Print-Lines"].split(",")]
    assert len(line_ids) == 3

    feed = client.get("/test/drinks/print-feed")
    assert feed.status_code == 200
    assert [row["id"] for row in feed.json()["drinks"]] == line_ids
    assert {row["order_id"] for row in feed.json()["drinks"]} == {int(order_id)}

    expected = [
        ("Drink 1 of 3", "Fruit Tea", "Strawberry", ("Viet Coffee", "Milk Tea", "Taro")),
        ("Drink 2 of 3", "Viet Coffee", "Oat milk", ("Fruit Tea", "Milk Tea", "Strawberry Bursting")),
        ("Drink 3 of 3", "Milk Tea", "Taro", ("Fruit Tea", "Viet Coffee", "Strawberry Bursting")),
    ]
    printed = []
    for line_id, (number, drink, modifier, absent) in zip(line_ids, expected):
        claim = client.post(f"/test/drinks/lines/{line_id}/print-claim")
        assert claim.status_code == 200
        query = _passprnt_query(claim.json()["passprnt"])
        assert query["size"] == ["406"]
        assert query["cut"] == ["partial"]
        assert query["popup"] == ["disable"]
        assert "2w7" not in claim.json()["passprnt"]
        assert query["back"][0].endswith(f"/test/drinks?print_line={line_id}")
        ticket_html = query["html"][0]
        assert "Table 4" in ticket_html
        assert f"Order {order_id}" in ticket_html
        assert number in ticket_html
        assert drink in ticket_html
        assert modifier in ticket_html
        for other in absent:
            assert other not in ticket_html
        assert "3 drinks" not in ticket_html
        assert "50.8mm" in ticket_html
        assert len(claim.json()["passprnt"]) < 8000
        printed.append(ticket_html)
        again = client.post(f"/test/drinks/lines/{line_id}/print-claim")
        assert again.status_code == 409

    assert client.get("/test/drinks/print-feed").json()["drinks"] == []

    # Clearing a drink on the board does not change the ticket that will print.
    import re

    fruit_id, coffee_id, tea_id = line_ids
    line = re.search(rf'hx-delete="(/test/drinks/lines/{fruit_id})"', placed.text)
    assert line
    client.delete(line.group(1), headers={"HX-Request": "true"})
    preview = client.get(f"/test/drinks/lines/{fruit_id}/ticket")
    assert preview.status_code == 200
    assert preview.text == printed[0]
    assert "Viet Coffee" not in preview.text

    # Releasing one drink leaves the others claimed.
    released = client.post(f"/test/drinks/lines/{tea_id}/print-release")
    assert released.json()["status"] == "pending"
    pending = [row["id"] for row in client.get("/test/drinks/print-feed").json()["drinks"]]
    assert pending == [tea_id]

    client.post(f"/test/drinks/lines/{coffee_id}/print-done")
    client.post(f"/test/drinks/lines/{fruit_id}/print-done")
    client.post(f"/test/drinks/lines/{tea_id}/print-claim")
    client.post(f"/test/drinks/lines/{tea_id}/print-done")
    assert client.get("/test/drinks/print-feed").json()["drinks"] == []
    # A finished drink is not printed again, even if PassPRNT returns an error later.
    client.post(f"/test/drinks/lines/{coffee_id}/print-release")
    assert client.get("/test/drinks/print-feed").json()["drinks"] == []

    live = client.get("/board")
    assert "starpassprnt" not in live.text
    assert "Table 4" not in live.text


def test_new_drink_line_prints_without_repeating_the_order() -> None:
    """The board cannot add a drink to an open order. Queuing again prints only a new line."""
    from app.db import SessionLocal
    from app.models import TestDrinkLine, TestDrinkOrder
    from app.passprnt import enqueue_print_jobs

    _reset_drink_orders()
    placed = _place_drink_order("Window", **{"0": 1})
    first_id = int(placed.headers["X-Print-Lines"])
    client.post(f"/test/drinks/lines/{first_id}/print-claim")
    client.post(f"/test/drinks/lines/{first_id}/print-done")

    with SessionLocal() as db:
        order = db.get(TestDrinkOrder, int(placed.headers["X-Print-Order"]))
        order.lines.append(
            TestDrinkLine(
                drink_name="Matcha",
                modifiers_json='[{"group":"Matcha","value":"Strawberry Matcha"}]',
                qty=1,
            )
        )
        db.commit()
        db.refresh(order)
        created = enqueue_print_jobs(db, order, "2:07 PM")
        assert len(created) == 1
        new_id = created[0].line_id

    feed = client.get("/test/drinks/print-feed").json()["drinks"]
    assert [row["id"] for row in feed] == [new_id]
    claim = client.post(f"/test/drinks/lines/{new_id}/print-claim")
    html = _passprnt_query(claim.json()["passprnt"])["html"][0]
    assert "Matcha" in html
    assert "Drink 2 of 2" in html
    assert "Fruit Tea" not in html
    assert "Strawberry Matcha" in html


def test_passprnt_back_url_is_https_behind_the_cloud_run_proxy() -> None:
    """Cloud Run sends X-Forwarded-Proto. The back URL must use https."""
    import socket
    import subprocess
    import time
    import urllib.parse
    import urllib.request
    from pathlib import Path
    from urllib.parse import unquote

    import httpx

    entry = Path("scripts/docker-entrypoint.sh").read_text()
    run = Path("scripts/run.sh").read_text()
    for script in (entry, run):
        assert "--proxy-headers" in script
        assert "--forwarded-allow-ips" in script

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    log = open(f"/tmp/uvicorn-proxy-{port}.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            ".venv/bin/uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--proxy-headers",
            "--forwarded-allow-ips",
            "*",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONPATH": "."},
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            if proc.poll() is not None:
                log.flush()
                raise AssertionError(Path(log.name).read_text()[-2000:])
            try:
                urllib.request.urlopen(base + "/", timeout=0.4)
                break
            except Exception:
                time.sleep(0.15)
        else:
            raise AssertionError("uvicorn did not start")

        def place(name: str) -> str:
            body = urllib.parse.urlencode({"ticket_name": name, "qty_0": "1"}).encode()
            req = urllib.request.Request(
                base + "/test/drinks/orders",
                data=body,
                headers={"HX-Request": "true"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                return resp.headers["X-Print-Lines"].split(",")[0]

        public = "sunshine-drink-board-test.a.run.app"
        https_id = place("Proxy")
        claimed = httpx.post(
            f"{base}/test/drinks/lines/{https_id}/print-claim",
            headers={"X-Forwarded-Proto": "https", "Host": public},
        )
        assert claimed.status_code == 200
        back = claimed.json()["passprnt"].split("back=")[1]
        assert unquote(back).startswith(f"https://{public}/test/drinks?print_line={https_id}")

        local_id = place("Local")
        plain = httpx.post(f"{base}/test/drinks/lines/{local_id}/print-claim")
        assert plain.status_code == 200
        local_back = unquote(plain.json()["passprnt"].split("back=")[1])
        assert local_back.startswith(f"http://127.0.0.1:{port}/test/drinks?print_line={local_id}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def _passprnt_parts(url: str) -> tuple[str, list[str]]:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    keys = [part.split("=", 1)[0] for part in parsed.query.split("&") if part]
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}", keys


def test_drink_ticket_url_matches_the_working_test_print() -> None:
    """Order slips use the same short starpassprnt link shape as Test print."""
    from urllib.parse import parse_qs, unquote

    page = client.get("/test/drinks")
    assert '<a class="kds-test-print" id="test-print" href="starpassprnt://' in page.text
    assert '<a id="print-next"' in page.text
    assert "<button" not in page.text.split('id="print-banner"', 1)[1].split("</div>", 1)[0]
    sample = page.text.split('id="test-print" href="', 1)[1].split('"', 1)[0].replace("&amp;", "&")

    _reset_drink_orders()
    placed = _place_drink_order("Table 4", **{"0": 1, "1": 1})
    line_id = placed.headers["X-Print-Lines"].split(",")[0]
    feed = client.get("/test/drinks/print-feed")
    drink = next(row["passprnt"] for row in feed.json()["drinks"] if row["id"] == int(line_id))

    sample_base, sample_keys = _passprnt_parts(sample)
    drink_base, drink_keys = _passprnt_parts(drink)
    assert sample_base == drink_base == "starpassprnt://v1/print/nopreview"
    assert sample_keys == drink_keys == ["html", "size", "cut", "popup", "back"]
    assert "%3C" in sample and "%3C" in drink
    assert "<html" not in sample and "<html" not in drink
    # A real drink is a little longer than the sample, not a different kind of URL.
    assert len(drink) < 4000
    assert len(drink) <= len(sample) + 1200

    sample_html = unquote(parse_qs(sample.split("?", 1)[1])["html"][0])
    drink_html = unquote(parse_qs(drink.split("?", 1)[1])["html"][0])
    for ticket in (sample_html, drink_html):
        assert "style=" in ticket
        assert "<style" not in ticket
        assert "<img" not in ticket
        assert "base64" not in ticket
        assert "50.8mm" in ticket
    assert "Test print" in sample_html
    assert "Fruit Tea" in drink_html
    assert "Drink 1 of 2" in drink_html
    assert "Viet Coffee" not in drink_html

    script = client.get("/static/print-tablet.js")
    source = script.text
    assert "sendBeacon" in source
    assert "location.href" not in source
    assert "XMLHttpRequest" not in source


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
