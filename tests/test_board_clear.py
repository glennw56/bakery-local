"""Board tap-to-clear: query param + localStorage persist. No live Square token."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

if "BAKERY_DB" not in os.environ:
    _fd, _db = tempfile.mkstemp(suffix=".db")
    os.close(_fd)
    os.environ["BAKERY_DB"] = _db

from datetime import datetime, timedelta, timezone  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _recent(minutes_ago: int = 5) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeClient:
    payload: object = None

    def __init__(self, timeout=None):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None):
        return _FakeResp(self.payload)


def test_board_tickets_reads_cleared_query(monkeypatch) -> None:
    payload = [
        [
            _recent(8),
            "ORDER_KEEP",
            ["1 Matcha Latte ", "Matcha Option Strawberry Matcha"],
        ],
        [
            _recent(20),
            "ORDER_CLEAR_ME",
            ["1 Vietnamese Coffee ", "Sweet Level 25%"],
        ],
    ]
    monkeypatch.setenv("GETORDERS_URL", "https://example.test/getorders")
    monkeypatch.setattr("httpx.Client", _FakeClient)
    _FakeClient.payload = payload

    from app.main import app

    client = TestClient(app)
    shown = client.get("/board/tickets?minutes=180")
    assert shown.status_code == 200
    assert "ORDER_KEEP" in shown.text
    assert "ORDER_CLEAR_ME" in shown.text

    hidden = client.get("/board/tickets?minutes=180&cleared=ORDER_CLEAR_ME")
    assert hidden.status_code == 200
    assert "ORDER_KEEP" in hidden.text
    assert "ORDER_CLEAR_ME" not in hidden.text


def test_delete_order_sets_cleared_cookie(monkeypatch) -> None:
    payload = [
        [
            _recent(8),
            "ORDER_COOKIE",
            ["1 Milk Tea ", "Flavor Taro"],
        ]
    ]
    monkeypatch.setenv("GETORDERS_URL", "https://example.test/getorders")
    monkeypatch.setattr("httpx.Client", _FakeClient)
    _FakeClient.payload = payload

    from app.main import app

    client = TestClient(app)
    gone = client.delete("/board/orders/ORDER_COOKIE?minutes=180", headers={"HX-Request": "true"})
    assert gone.status_code == 200
    assert "ORDER_COOKIE" not in gone.text
    assert client.cookies.get("kds_cleared") == "ORDER_COOKIE"


def test_board_cleared_js_writes_local_and_session_storage() -> None:
    js_path = ROOT / "static" / "board-cleared.js"
    text = js_path.read_text(encoding="utf-8")
    assert "kdsRememberCleared" in text
    assert "localStorage.setItem(STORE, value)" in text
    assert "sessionStorage.setItem(STORE, value)" in text
    assert 'htmx:afterRequest' in text

    store: dict[str, dict[str, str]] = {"local": {}, "session": {}}

    def remember(oid: str, max_ids: int = 80) -> None:
        raw = store["local"].get("kds_cleared") or store["session"].get("kds_cleared") or ""
        ids = [part.strip() for part in raw.split(",") if part.strip()][:max_ids]
        if oid and oid not in ids:
            ids.append(oid)
        value = ",".join(ids[:max_ids])
        store["local"]["kds_cleared"] = value
        store["session"]["kds_cleared"] = value

    remember("ORDER_A")
    assert store["local"]["kds_cleared"] == "ORDER_A"
    assert store["session"]["kds_cleared"] == "ORDER_A"
    remember("ORDER_A")
    assert store["local"]["kds_cleared"] == "ORDER_A"
    remember("ORDER_B")
    assert store["local"]["kds_cleared"] == "ORDER_A,ORDER_B"
    assert store["session"]["kds_cleared"] == "ORDER_A,ORDER_B"

    runner = ROOT / "tests" / "fixtures" / "kds_cleared_vm.js"
    for candidate in (
        os.environ.get("NODE_BIN"),
        "/usr/bin/nodejs",
        "/usr/bin/node",
    ):
        if not candidate or not Path(candidate).is_file():
            continue
        result = subprocess.run(
            [candidate, str(runner), str(js_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        return
