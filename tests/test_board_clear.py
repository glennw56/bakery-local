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

from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


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
            "2026-09-05T18:50:44.246Z",
            "ORDER_KEEP",
            ["1 Matcha Latte ", "Matcha Option Strawberry Matcha"],
        ],
        [
            "2026-09-05T18:12:19.110Z",
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
            "2026-09-05T18:50:44.246Z",
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


def _run_board_cleared_js(script: str) -> subprocess.CompletedProcess | None:
    node = os.environ.get("NODE") or "node"
    js_path = ROOT / "static" / "board-cleared.js"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(script)
        runner = handle.name
    try:
        return subprocess.run(
            [node, runner, str(js_path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None


_CLEARED_JS_HARNESS = r"""
const fs = require("fs");
const store = { local: {}, session: {} };
function memory(kind) {
  return {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(store[kind], k) ? store[kind][k] : null),
    setItem: (k, v) => { store[kind][k] = String(v); },
  };
}
const listeners = [];
function makeCard(oid) {
  const el = {
    className: "kds-ticket",
    attrs: { "data-order-id": oid },
    parent: null,
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    closest(sel) {
      if (sel === "#ticket-list .kds-ticket[data-order-id]" || sel === ".kds-ticket[data-order-id]") return this;
      return null;
    },
    remove() {
      if (this.parent) {
        this.parent.children = this.parent.children.filter((c) => c !== this);
        this.parent = null;
      }
    },
  };
  return el;
}
function makeList(cards) {
  const list = {
    id: "ticket-list",
    children: cards.slice(),
    querySelectorAll(sel) {
      if (sel === ".kds-ticket[data-order-id]") return this.children.filter((c) => c.className.indexOf("kds-ticket") !== -1);
      return [];
    },
  };
  cards.forEach((c) => { c.parent = list; });
  return list;
}
const window = {};
let list = makeList([]);
const document = {
  body: {
    addEventListener: (name, fn, opts) => { listeners.push({ name, fn, opts }); },
  },
  getElementById: (id) => (id === "ticket-list" ? list : null),
};
const localStorage = memory("local");
const sessionStorage = memory("session");
const src = fs.readFileSync(process.argv[1], "utf8");
eval(src);
"""


def test_board_cleared_js_writes_local_and_session_storage() -> None:
    script = _CLEARED_JS_HARNESS + r"""
window.kdsRememberCleared("ORDER_A");
if (store.local.kds_cleared !== "ORDER_A") process.exit(2);
if (store.session.kds_cleared !== "ORDER_A") process.exit(3);
window.kdsRememberCleared("ORDER_A");
if (store.local.kds_cleared !== "ORDER_A") process.exit(4);
window.kdsRememberCleared("ORDER_B");
if (store.local.kds_cleared !== "ORDER_A,ORDER_B") process.exit(5);
if (store.session.kds_cleared !== "ORDER_A,ORDER_B") process.exit(6);
if (window.kdsLoadCleared().join(",") !== "ORDER_A,ORDER_B") process.exit(7);
"""
    js_path = ROOT / "static" / "board-cleared.js"
    result = _run_board_cleared_js(script)
    if result is None:
        text = js_path.read_text(encoding="utf-8")
        assert "localStorage.setItem(STORE, value)" in text
        assert "sessionStorage.setItem(STORE, value)" in text
        assert "kdsRememberCleared" in text
        return
    assert result.returncode == 0, result.stderr or result.stdout


def test_board_cleared_js_persists_on_tap() -> None:
    script = _CLEARED_JS_HARNESS + r"""
const card = makeCard("ORDER_TAP");
list = makeList([card]);
const tap = { target: card };
listeners.filter((l) => l.name === "pointerup" || l.name === "click").forEach((l) => l.fn(tap));
if (store.local.kds_cleared !== "ORDER_TAP") process.exit(2);
if (store.session.kds_cleared !== "ORDER_TAP") process.exit(3);
if (list.children.length !== 1) process.exit(4);
"""
    js_path = ROOT / "static" / "board-cleared.js"
    result = _run_board_cleared_js(script)
    if result is None:
        text = js_path.read_text(encoding="utf-8")
        assert "pointerup" in text
        assert "rememberTap" in text
        return
    assert result.returncode == 0, result.stderr or result.stdout


def test_board_cleared_js_hides_cleared_cards_after_swap() -> None:
    script = _CLEARED_JS_HARNESS + r"""
store.local.kds_cleared = "ORDER_GONE";
store.session.kds_cleared = "ORDER_GONE";
const gone = makeCard("ORDER_GONE");
const keep = makeCard("ORDER_KEEP");
list = makeList([gone, keep]);
const swap = listeners.find((l) => l.name === "htmx:afterSwap");
if (!swap) process.exit(2);
swap.fn({ detail: { target: { id: "not-ticket-list" } } });
if (list.children.length !== 1) process.exit(3);
if (list.children[0].getAttribute("data-order-id") !== "ORDER_KEEP") process.exit(4);
const bounce = makeCard("ORDER_GONE");
list = makeList([bounce, makeCard("ORDER_KEEP")]);
window.kdsFilterCleared();
if (list.children.length !== 1) process.exit(5);
if (list.children[0].getAttribute("data-order-id") !== "ORDER_KEEP") process.exit(6);
"""
    js_path = ROOT / "static" / "board-cleared.js"
    result = _run_board_cleared_js(script)
    if result is None:
        text = js_path.read_text(encoding="utf-8")
        assert "htmx:afterSwap" in text
        assert "kdsFilterCleared" in text
        assert ".kds-ticket[data-order-id]" in text
        return
    assert result.returncode == 0, result.stderr or result.stdout
