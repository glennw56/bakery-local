"""Square Customers + Loyalty + Orders for the Sunshine phone login.

Drop this file into bakery-local as ``app/account.py`` and register the
``/order/api/account`` routes (see README.md). The Square access token
stays in env / Secret Manager — never in the APK.

This module has no invented customer directory. Every customer id comes
from Square SearchCustomers / CreateCustomer.

Phone login is POST-only and returns a short-lived HMAC session token.
GET status / orders require that token. Public JSON never includes email.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import httpx
except ImportError:  # unit tests of helpers only
    httpx = None  # type: ignore

try:
    from app import order as order_svc
except ImportError:  # standalone / unit tests
    order_svc = None  # type: ignore

SQUARE_VERSION = "2025-01-23"
DEFAULT_LOCATION_ID = "L4CK6YWGT5XQX"
DEFAULT_API_BASE = "https://connect.squareup.com"
READY_FULFILLMENT = frozenset({"PREPARED", "COMPLETED"})
QUEUE_FULFILLMENT = frozenset({"PROPOSED", "RESERVED"})
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
SESSION_NS = b"bakery-account-session-v1"
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_PHONE_LIMIT = 10
LOGIN_IP_LIMIT = 30
RATE_LIMIT_MAX_KEYS = 10_000


class AccountError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class SlidingWindowLimiter:
    """In-memory sliding window. Per Cloud Run instance; resets on scale-to-zero."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            q = self._hits[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > RATE_LIMIT_MAX_KEYS:
                dead = [k for k, bucket in self._hits.items() if not bucket or bucket[-1] < cutoff]
                for k in dead:
                    self._hits.pop(k, None)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_phone_limiter = SlidingWindowLimiter(LOGIN_PHONE_LIMIT, LOGIN_WINDOW_SECONDS)
_ip_limiter = SlidingWindowLimiter(LOGIN_IP_LIMIT, LOGIN_WINDOW_SECONDS)


def reset_login_rate_limits() -> None:
    _phone_limiter.reset()
    _ip_limiter.reset()


def check_login_rate(ip: str, phone: str) -> None:
    ip_key = (ip or "").strip() or "unknown"
    if not _ip_limiter.hit(ip_key):
        raise AccountError("Too many sign-in attempts. Try again later.", 429)
    if phone and not _phone_limiter.hit(phone):
        raise AccountError("Too many sign-in attempts. Try again later.", 429)


def _session_key() -> bytes:
    secret = (os.environ.get("SESSION_SECRET") or "").strip()
    if secret:
        return hmac.new(SESSION_NS, secret.encode("utf-8"), hashlib.sha256).digest()
    token = (os.environ.get("SQUARE_ACCESS_TOKEN") or "").strip()
    if token:
        return hmac.new(SESSION_NS, token.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(SESSION_NS, b"dev", hashlib.sha256).digest()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def _mac_ok(got: str, expected: str) -> bool:
    if not isinstance(got, str) or not isinstance(expected, str):
        return False
    if len(got) != len(expected):
        return False
    return hmac.compare_digest(got, expected)


def mint_session_token(
    customer_id: str,
    phone: str,
    *,
    now: int | None = None,
    ttl: int = SESSION_TTL_SECONDS,
) -> tuple[str, str]:
    exp = int(now if now is not None else time.time()) + int(ttl)
    payload = json.dumps(
        {"v": 1, "cid": customer_id, "phone": phone, "exp": exp},
        separators=(",", ":"),
        sort_keys=True,
    )
    blob = _b64url_encode(payload.encode("utf-8"))
    mac = hmac.new(_session_key(), blob.encode("ascii"), hashlib.sha256).hexdigest()
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{blob}.{mac}", expires_at


def verify_session_token(token: str, *, now: int | None = None) -> dict[str, Any]:
    raw = (token or "").strip()
    if not raw or "." not in raw:
        raise AccountError("Sign in required.", 401)
    blob, mac = raw.rsplit(".", 1)
    expected = hmac.new(_session_key(), blob.encode("ascii"), hashlib.sha256).hexdigest()
    if not _mac_ok(mac, expected):
        raise AccountError("Session expired or invalid.", 401)
    try:
        data = json.loads(_b64url_decode(blob).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise AccountError("Session expired or invalid.", 401)
    if not isinstance(data, dict) or data.get("v") != 1:
        raise AccountError("Session expired or invalid.", 401)
    try:
        exp = int(data.get("exp") or 0)
    except (TypeError, ValueError):
        raise AccountError("Session expired or invalid.", 401)
    clock = int(now if now is not None else time.time())
    if exp < clock:
        raise AccountError("Session expired or invalid.", 401)
    cid = str(data.get("cid") or "").strip()
    phone = str(data.get("phone") or "").strip()
    if not cid or not phone:
        raise AccountError("Session expired or invalid.", 401)
    return {"customer_id": cid, "phone": phone, "exp": exp}


def token_from_headers(authorization: str = "", x_session_token: str = "") -> str:
    auth = (authorization or "").strip()
    if len(auth) >= 7 and auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return (x_session_token or "").strip()


def wants_loyalty(body: dict[str, Any] | None) -> bool:
    """Loyalty enroll only when the client explicitly opts in."""
    if not isinstance(body, dict):
        return False
    raw = body.get("join_loyalty", False)
    if raw is True:
        return True
    if isinstance(raw, (int, float)) and raw == 1:
        return True
    if isinstance(raw, str) and raw.strip().lower() in ("1", "true", "yes"):
        return True
    return False


def client_ip(headers: dict[str, str] | None, host: str = "") -> str:
    if headers:
        forwarded = (headers.get("x-forwarded-for") or headers.get("X-Forwarded-For") or "").strip()
        if forwarded:
            return forwarded.split(",")[0].strip()
    return (host or "").strip()


def normalize_phone(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    digits = re.sub(r"\D+", "", text)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if text.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    return ""


def display_name(customer: dict[str, Any]) -> str:
    nick = str(customer.get("nickname") or "").strip()
    if nick:
        return nick
    given = str(customer.get("given_name") or "").strip()
    family = str(customer.get("family_name") or "").strip()
    if given and family:
        return f"{given} {family}"
    if given:
        return given
    if family:
        return family
    return str(customer.get("display_name") or "").strip()


def customer_public(customer: dict[str, Any]) -> dict[str, Any]:
    """APK-facing customer JSON. Email is never included."""
    phone = str(customer.get("phone_number") or customer.get("phone") or "").strip()
    return {
        "id": str(customer.get("id") or "").strip(),
        "phone": phone,
        "given_name": str(customer.get("given_name") or "").strip(),
        "family_name": str(customer.get("family_name") or "").strip(),
        "nickname": str(customer.get("nickname") or "").strip(),
        "display_name": display_name(customer),
    }


def _money_cents(blob: Any) -> int:
    if not isinstance(blob, dict):
        return 0
    money = blob.get("total_money") if "total_money" in blob else blob
    if not isinstance(money, dict):
        return 0
    try:
        return int(money.get("amount") or 0)
    except (TypeError, ValueError):
        return 0


def _line_items(order: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in order.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        try:
            qty = int(float(item.get("quantity") or 1))
        except (TypeError, ValueError):
            qty = 1
        items.append({"name": name, "qty": max(1, qty)})
    return items


def order_name(order: dict[str, Any]) -> str:
    items = _line_items(order)
    if not items:
        return "Order"
    first = items[0]["name"]
    extra = len(items) - 1
    return first if extra < 1 else f"{first} + {extra} more"


def fulfillment_state(order: dict[str, Any]) -> str:
    for ful in order.get("fulfillments") or []:
        if not isinstance(ful, dict):
            continue
        state = str(ful.get("state") or "").upper()
        if state:
            return state
    return ""


def order_status_label(order: dict[str, Any]) -> str:
    if str(order.get("state") or "").upper() == "CANCELED":
        return "canceled"
    if str(order.get("state") or "").upper() == "COMPLETED":
        return "ready"
    ful = fulfillment_state(order)
    if ful in READY_FULFILLMENT:
        return "ready"
    if ful in QUEUE_FULFILLMENT or str(order.get("state") or "").upper() == "OPEN":
        return "making"
    return "pending"


def is_in_queue(order: dict[str, Any]) -> bool:
    state = str(order.get("state") or "").upper()
    if state in ("CANCELED", "COMPLETED"):
        return False
    ful = fulfillment_state(order)
    if ful in READY_FULFILLMENT:
        return False
    return state == "OPEN" or ful in QUEUE_FULFILLMENT


def ahead_count(this_order: dict[str, Any], others: list[dict[str, Any]]) -> int:
    this_id = str(this_order.get("id") or "")
    this_at = str(this_order.get("created_at") or "")
    n = 0
    for other in others:
        if not isinstance(other, dict):
            continue
        if str(other.get("id") or "") == this_id:
            continue
        if not is_in_queue(other):
            continue
        other_at = str(other.get("created_at") or "")
        if other_at and this_at and other_at < this_at:
            n += 1
    return n


def summarize_order(order: dict[str, Any], *, ahead: int | None = None) -> dict[str, Any]:
    net = order.get("net_amounts") if isinstance(order.get("net_amounts"), dict) else {}
    total = _money_cents(net) or _money_cents(order.get("total_money"))
    items = _line_items(order)
    ref = str(order.get("reference_id") or "").replace("QR-", "").strip()
    row = {
        "id": str(order.get("id") or ""),
        "order_id": str(order.get("id") or ""),
        "order_number": ref,
        "name": order_name(order),
        "date": str(order.get("created_at") or "")[:10],
        "created_at": str(order.get("created_at") or ""),
        "total_cents": total,
        "status": order_status_label(order),
        "items": items,
    }
    if ahead is not None:
        row["ahead"] = ahead
        row["ahead_count"] = ahead
    return row


def square_token() -> str:
    if order_svc is not None and hasattr(order_svc, "square_token"):
        return str(order_svc.square_token() or "").strip()
    return (os.environ.get("SQUARE_ACCESS_TOKEN") or "").strip()


def location_id() -> str:
    if order_svc is not None and hasattr(order_svc, "irondale_location_id"):
        return str(order_svc.irondale_location_id() or DEFAULT_LOCATION_ID)
    for key in ("SQUARE_LOCATION_ID_IRONDALE", "LOCATION_ID", "SQUARE_LOCATION_ID"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return DEFAULT_LOCATION_ID


def square_api_base() -> str:
    if order_svc is not None and hasattr(order_svc, "square_api_base"):
        return str(order_svc.square_api_base())
    return (os.environ.get("SQUARE_API_BASE") or DEFAULT_API_BASE).rstrip("/")


def square_headers(token: str) -> dict[str, str]:
    if order_svc is not None and hasattr(order_svc, "square_headers"):
        return order_svc.square_headers(token)
    return {
        "Authorization": f"Bearer {token}",
        "Square-Version": SQUARE_VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _require_token() -> str:
    token = square_token()
    if not token:
        raise AccountError(
            "Square customer lookup is not configured on this drinks service.",
            503,
        )
    return token


def _square_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    token = _require_token()
    own = client is None
    http = client or httpx.Client(timeout=20.0)
    try:
        response = http.request(
            method,
            f"{square_api_base()}{path}",
            headers=square_headers(token),
            json=payload,
        )
    finally:
        if own:
            http.close()
    body: Any = {}
    if response.content:
        try:
            body = response.json()
        except ValueError:
            body = {}
    if response.status_code >= 400:
        err = "Square request failed."
        code = "UNKNOWN"
        if isinstance(body, dict):
            errors = body.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                err = str(errors[0].get("detail") or errors[0].get("code") or err)
                code = str(errors[0].get("code") or "")
        if response.status_code in (401, 403) or code in ("UNAUTHORIZED", "FORBIDDEN", "INSUFFICIENT_SCOPES"):
            raise AccountError(
                "Square token needs CUSTOMERS_READ, CUSTOMERS_WRITE, ORDERS_READ"
                " (LOYALTY_READ / LOYALTY_WRITE to enroll).",
                403,
            )
        raise AccountError(err, 502 if response.status_code >= 500 else response.status_code)
    return body if isinstance(body, dict) else {}


def search_customer_by_phone(phone: str, *, client: httpx.Client | None = None) -> dict[str, Any] | None:
    body = _square_json(
        "POST",
        "/v2/customers/search",
        {"query": {"filter": {"phone_number": {"exact": phone}}}},
        client=client,
    )
    customers = body.get("customers") if isinstance(body.get("customers"), list) else []
    for row in customers:
        if isinstance(row, dict) and row.get("id"):
            return row
    return None


def create_customer(phone: str, body: dict[str, Any], *, client: httpx.Client | None = None) -> dict[str, Any]:
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "phone_number": phone,
    }
    given = str(body.get("given_name") or "").strip()
    family = str(body.get("family_name") or "").strip()
    if given:
        payload["given_name"] = given[:100]
    if family:
        payload["family_name"] = family[:100]
    created = _square_json("POST", "/v2/customers", payload, client=client)
    customer = created.get("customer")
    if not isinstance(customer, dict) or not customer.get("id"):
        raise AccountError("Square did not create a customer.", 502)
    return customer


def retrieve_customer(customer_id: str, *, client: httpx.Client | None = None) -> dict[str, Any] | None:
    cid = (customer_id or "").strip()
    if not cid:
        return None
    try:
        body = _square_json("GET", f"/v2/customers/{cid}", client=client)
    except AccountError:
        return None
    customer = body.get("customer")
    return customer if isinstance(customer, dict) else None


def _loyalty_program_id(client: httpx.Client | None = None) -> str:
    body = _square_json("GET", "/v2/loyalty/programs", client=client)
    programs = body.get("programs") if isinstance(body.get("programs"), list) else []
    for prog in programs:
        if isinstance(prog, dict) and str(prog.get("status") or "").upper() == "ACTIVE" and prog.get("id"):
            return str(prog["id"])
    for prog in programs:
        if isinstance(prog, dict) and prog.get("id"):
            return str(prog["id"])
    return ""


def _search_loyalty(phone: str, program_id: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    body = _square_json(
        "POST",
        "/v2/loyalty/accounts/search",
        {"query": {"mappings": [{"phone_number": phone}]}},
        client=client,
    )
    accounts = body.get("loyalty_accounts") if isinstance(body.get("loyalty_accounts"), list) else []
    for row in accounts:
        if isinstance(row, dict) and (not program_id or str(row.get("program_id") or "") == program_id):
            return row
    return {}


def enroll_loyalty(phone: str, join: bool, *, client: httpx.Client | None = None) -> dict[str, Any]:
    try:
        program_id = _loyalty_program_id(client)
    except AccountError:
        return {"enrolled": False, "account_id": "", "points": 0, "program_id": ""}
    if not program_id:
        return {"enrolled": False, "account_id": "", "points": 0, "program_id": ""}
    existing = {}
    try:
        existing = _search_loyalty(phone, program_id, client=client)
    except AccountError:
        existing = {}
    if existing.get("id"):
        return {
            "enrolled": True,
            "account_id": str(existing.get("id") or ""),
            "points": int(existing.get("balance") or 0),
            "program_id": program_id,
        }
    if not join:
        return {"enrolled": False, "account_id": "", "points": 0, "program_id": program_id}
    try:
        created = _square_json(
            "POST",
            "/v2/loyalty/accounts",
            {
                "idempotency_key": str(uuid.uuid4()),
                "loyalty_account": {
                    "program_id": program_id,
                    "mapping": {"phone_number": phone},
                },
            },
            client=client,
        )
    except AccountError:
        return {"enrolled": False, "account_id": "", "points": 0, "program_id": program_id}
    account = created.get("loyalty_account") if isinstance(created.get("loyalty_account"), dict) else {}
    return {
        "enrolled": bool(account.get("id")),
        "account_id": str(account.get("id") or ""),
        "points": int(account.get("balance") or 0),
        "program_id": program_id,
    }


def _search_orders(query: dict[str, Any], *, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    body = _square_json(
        "POST",
        "/v2/orders/search",
        {
            "location_ids": [location_id()],
            "query": query,
            "limit": 50,
        },
        client=client,
    )
    rows = body.get("orders") if isinstance(body.get("orders"), list) else []
    return [row for row in rows if isinstance(row, dict)]


def _phone_digits(raw: str) -> str:
    return re.sub(r"\D+", "", raw or "")


def _order_phone(order: dict[str, Any]) -> str:
    for ful in order.get("fulfillments") or []:
        if not isinstance(ful, dict):
            continue
        details = ful.get("pickup_details") or {}
        if not isinstance(details, dict):
            continue
        recipient = details.get("recipient") or {}
        if isinstance(recipient, dict):
            phone = str(recipient.get("phone_number") or "")
            if phone:
                return phone
    return ""


def customer_orders(customer_id: str, phone: str, *, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    if customer_id:
        rows = _search_orders(
            {
                "filter": {"customer_filter": {"customer_ids": [customer_id]}},
                "sort": {"sort_field": "CREATED_AT", "sort_order": "DESC"},
            },
            client=client,
        )
        for row in rows:
            found[str(row.get("id") or "")] = row
    if phone:
        start = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%dT00:00:00Z")
        recent = _search_orders(
            {
                "filter": {
                    "date_time_filter": {"created_at": {"start_at": start}},
                },
                "sort": {"sort_field": "CREATED_AT", "sort_order": "DESC"},
            },
            client=client,
        )
        want = _phone_digits(phone)
        for row in recent:
            oid = str(row.get("id") or "")
            if not oid or oid in found:
                continue
            if customer_id and str(row.get("customer_id") or "") == customer_id:
                found[oid] = row
                continue
            if want and _phone_digits(_order_phone(row)) == want:
                found[oid] = row
    rows = [row for oid, row in found.items() if oid]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return rows[:20]


def open_queue_orders(*, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    return _search_orders(
        {
            "filter": {"state_filter": {"states": ["OPEN"]}},
            "sort": {"sort_field": "CREATED_AT", "sort_order": "ASC"},
        },
        client=client,
    )


def _account_payload(
    customer: dict[str, Any],
    *,
    created: bool,
    join_loyalty: bool,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    public = customer_public(customer)
    phone = public["phone"] or normalize_phone(str(customer.get("phone_number") or ""))
    loyalty = enroll_loyalty(phone, join_loyalty, client=client) if phone else {
        "enrolled": False,
        "account_id": "",
        "points": 0,
        "program_id": "",
    }
    raw_orders = customer_orders(public["id"], phone, client=client)
    queue = open_queue_orders(client=client)
    summaries = [summarize_order(row, ahead=ahead_count(row, queue)) for row in raw_orders]
    open_orders = [row for row in summaries if row.get("status") in ("pending", "making")]
    return {
        "ok": True,
        "created": created,
        "customer": public,
        "loyalty": loyalty,
        "orders": summaries,
        "open_orders": open_orders,
    }


def login_or_signup(body: dict[str, Any], *, client: httpx.Client | None = None) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise AccountError("Phone is required.")
    phone = normalize_phone(str(body.get("phone") or ""))
    if not phone:
        raise AccountError("Enter a US phone number (10 digits).")
    join = wants_loyalty(body)
    existing = search_customer_by_phone(phone, client=client)
    if existing:
        payload = _account_payload(existing, created=False, join_loyalty=join, client=client)
    else:
        created = create_customer(phone, body, client=client)
        payload = _account_payload(created, created=True, join_loyalty=join, client=client)
    cid = str(payload["customer"].get("id") or "").strip()
    token, expires_at = mint_session_token(cid, phone)
    payload["session_token"] = token
    payload["expires_at"] = expires_at
    return payload


def get_account(customer_id: str = "", phone: str = "", *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Read-only account lookup. Never enrolls loyalty (join_loyalty stays false)."""
    e164 = normalize_phone(phone)
    customer = retrieve_customer(customer_id, client=client) if customer_id else None
    if customer is None and e164:
        customer = search_customer_by_phone(e164, client=client)
    if customer is None:
        raise AccountError("Square customer not found.", 404)
    return _account_payload(customer, created=False, join_loyalty=False, client=client)


def get_status(customer_id: str = "", phone: str = "", *, client: httpx.Client | None = None) -> dict[str, Any]:
    payload = get_account(customer_id, phone, client=client)
    return {
        "ok": True,
        "customer": payload["customer"],
        "open_orders": payload["open_orders"],
        "orders": payload["orders"],
    }


def list_orders(customer_id: str = "", phone: str = "", *, client: httpx.Client | None = None) -> dict[str, Any]:
    payload = get_account(customer_id, phone, client=client)
    return {
        "ok": True,
        "customer": payload["customer"],
        "orders": payload["orders"],
        "open_orders": payload["open_orders"],
    }


def _json_error(exc: AccountError):
    from fastapi.responses import JSONResponse

    headers = {}
    if exc.status_code == 429:
        headers["Retry-After"] = str(LOGIN_WINDOW_SECONDS)
    return JSONResponse(
        {"ok": False, "error": exc.message},
        status_code=exc.status_code,
        headers=headers,
    )


def session_from_request(request) -> dict[str, Any]:
    token = token_from_headers(
        request.headers.get("authorization") or "",
        request.headers.get("x-session-token") or "",
    )
    if not token:
        raise AccountError("Sign in required.", 401)
    return verify_session_token(token)


def mount(app) -> None:
    """Register Square customer routes on a FastAPI app (bakery-drinks)."""
    from fastapi import Body, Request

    @app.post("/order/api/account/phone")
    @app.post("/order/api/customer")
    def order_api_customer_write(request: Request, body: dict = Body(...)):
        try:
            phone = normalize_phone(str((body or {}).get("phone") or ""))
            host = request.client.host if request.client else ""
            check_login_rate(client_ip(dict(request.headers), host), phone)
            return login_or_signup(body)
        except AccountError as exc:
            return _json_error(exc)

    def _read_for_session(request: Request) -> dict[str, Any]:
        session = session_from_request(request)
        return get_account(session["customer_id"], session["phone"])

    @app.get("/order/api/account")
    @app.get("/order/api/customer")
    def order_api_customer_read(request: Request):
        try:
            return _read_for_session(request)
        except AccountError as exc:
            return _json_error(exc)

    @app.get("/order/api/account/status")
    def order_api_account_status(request: Request):
        try:
            session = session_from_request(request)
            return get_status(session["customer_id"], session["phone"])
        except AccountError as exc:
            return _json_error(exc)

    @app.get("/order/api/orders")
    def order_api_orders(request: Request):
        try:
            session = session_from_request(request)
            return list_orders(session["customer_id"], session["phone"])
        except AccountError as exc:
            return _json_error(exc)
