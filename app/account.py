"""Square Customers + Loyalty + Orders for the Sunshine phone login.

Drop this file into bakery-local as ``app/account.py`` and register the
``/order/api/account`` routes (see README.md). The Square access token
stays in env / Secret Manager — never in the APK.

This module has no invented customer directory. Every customer id comes
from Square SearchCustomers / CreateCustomer.

Phone login is POST-only and returns a short-lived HMAC session token.
GET status / orders require that token. Profile update (name / email)
uses the session customer_id — never a client-supplied id. Public JSON
never includes email.
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
    from fastapi import Request
except ImportError:  # unit tests of helpers only
    Request = object  # type: ignore

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
NAME_MAX = 100
EMAIL_MAX = 254
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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


def _money_amount(money: Any) -> int:
    if not isinstance(money, dict):
        return 0
    try:
        return int(money.get("amount") or 0)
    except (TypeError, ValueError):
        return 0


def _has_recorded_payment(order: dict[str, Any]) -> bool:
    """True when Square recorded a tender/payment on the Order.

    Square Retrieve Orders: paid orders populate ``tenders[]`` (Tender objects
    with ``id``, ``type``, and often ``payment_id``). Some payloads also expose
    a ``payments`` list; that is checked the same way. Empty lists do not count.
    """
    for key in ("tenders", "payments"):
        rows = order.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("id") or row.get("payment_id") or str(row.get("type") or "").strip():
                return True
    return False


def is_paid_order(order: dict[str, Any]) -> bool:
    """True when this Square Order is paid (Previous Orders / list/detail).

    Square fields used (Orders API Order object; amounts are Square's cents,
    never invented):

    * ``tenders`` / ``payments`` — present after pay. Bakery QR CreatePaymentLink
      tickets stay ``OPEN`` with fulfillment PROPOSED/RESERVED while drinks are
      made; tenders are the paid-making path.
    * ``net_amount_due_money.amount == 0`` — nothing left to collect. Square may
      omit this field after pay; omission alone is not treated as paid.
    * ``state == COMPLETED`` — Square documents completed orders as fully paid
      (terminal). Combined with no remaining due when due is present.

    ``CANCELED`` and ``DRAFT`` are never paid. Remaining due with no tender is
    an unpaid open ticket and must not appear in Previous Orders.
    """
    if not isinstance(order, dict):
        return False
    state = str(order.get("state") or "").upper()
    if state in ("CANCELED", "DRAFT"):
        return False

    has_payment = _has_recorded_payment(order)
    due_blob = order.get("net_amount_due_money")
    due_present = isinstance(due_blob, dict)
    due_amount = _money_amount(due_blob) if due_present else None
    remaining_due = due_present and due_amount is not None and due_amount > 0
    if remaining_due:
        # Unpaid checkout ticket, or a partial tender still owing.
        return False
    if has_payment:
        return True
    if due_present and due_amount == 0:
        return True
    return state == "COMPLETED"


def _money_cents(blob: Any) -> int:
    if not isinstance(blob, dict):
        return 0
    money = blob.get("total_money") if "total_money" in blob else blob
    return _money_amount(money)


def _price_fields(blob: dict[str, Any]) -> dict[str, int]:
    """Public cents from Square money. Prefer total_price_money / total_money, then base."""
    out: dict[str, int] = {}
    total = None
    for key in ("total_price_money", "total_money"):
        if isinstance(blob.get(key), dict):
            total = _money_amount(blob[key])
            break
    base = None
    if isinstance(blob.get("base_price_money"), dict):
        base = _money_amount(blob["base_price_money"])
    if total is not None:
        out["price_cents"] = total
    elif base is not None:
        out["price_cents"] = base
    if base is not None:
        out["base_price_cents"] = base
    return out


def _parse_qty(raw: Any, default: int | None = None) -> int | str | None:
    if raw is None or raw == "":
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        text = str(raw).strip()
        return text or default


def _public_modifier(mod: dict[str, Any]) -> dict[str, Any] | None:
    name = str(mod.get("name") or mod.get("display_name") or "").strip()
    if not name:
        return None
    row: dict[str, Any] = {"name": name}
    quantity = _parse_qty(mod.get("quantity"))
    if quantity is not None:
        row["quantity"] = quantity
    row.update(_price_fields(mod))
    catalog_id = str(mod.get("catalog_object_id") or "").strip()
    if catalog_id:
        row["catalog_object_id"] = catalog_id
    return row


def _line_items(order: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in order.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        qty = _parse_qty(item.get("quantity"), 1)
        if not isinstance(qty, int):
            qty = 1
        row: dict[str, Any] = {"name": name, "qty": max(1, qty)}
        catalog_id = str(item.get("catalog_object_id") or "").strip()
        variation_id = str(
            item.get("catalog_variation_id")
            or item.get("item_variation_id")
            or catalog_id
        ).strip()
        variation_name = str(item.get("variation_name") or "").strip()
        if catalog_id:
            row["catalog_object_id"] = catalog_id
        if variation_id:
            row["catalog_variation_id"] = variation_id
        if variation_name:
            row["variation_name"] = variation_name
        row.update(_price_fields(item))
        modifiers: list[dict[str, Any]] = []
        for mod in item.get("modifiers") or []:
            if not isinstance(mod, dict):
                continue
            public = _public_modifier(mod)
            if public:
                modifiers.append(public)
        row["modifiers"] = modifiers
        items.append(row)
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


def summarize_paid_orders(
    orders: list[dict[str, Any]],
    *,
    queue: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Public Previous Orders list: paid Square tickets only."""
    others = queue if queue is not None else []
    out: list[dict[str, Any]] = []
    for row in orders:
        if not isinstance(row, dict) or not is_paid_order(row):
            continue
        out.append(summarize_order(row, ahead=ahead_count(row, others)))
    return out


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


def parse_profile_fields(body: dict[str, Any] | None) -> dict[str, str]:
    """Sparse Square UpdateCustomer fields. Client ``email`` → ``email_address``."""
    if not isinstance(body, dict):
        raise AccountError("Enter a name or email.")
    given = str(body.get("given_name") or "").strip()[:NAME_MAX]
    family = str(body.get("family_name") or "").strip()[:NAME_MAX]
    email = str(body.get("email") or "").strip()
    fields: dict[str, str] = {}
    if given:
        fields["given_name"] = given
    if family:
        fields["family_name"] = family
    if email:
        if len(email) > EMAIL_MAX or not _EMAIL_RE.match(email):
            raise AccountError("Enter a valid email address.")
        fields["email_address"] = email
    if not fields:
        raise AccountError("Enter a name or email.")
    return fields


def update_customer(
    customer_id: str,
    fields: dict[str, str],
    *,
    version: int | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    cid = (customer_id or "").strip()
    if not cid:
        raise AccountError("Sign in required.", 401)
    payload: dict[str, Any] = dict(fields)
    if version is not None:
        payload["version"] = version
    updated = _square_json("PUT", f"/v2/customers/{cid}", payload, client=client)
    customer = updated.get("customer")
    if not isinstance(customer, dict) or not customer.get("id"):
        raise AccountError("Square did not update the customer.", 502)
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


def retrieve_order(order_id: str, *, client: httpx.Client | None = None) -> dict[str, Any] | None:
    oid = (order_id or "").strip()
    if not oid:
        return None
    try:
        body = _square_json("GET", f"/v2/orders/{oid}", client=client)
    except AccountError as exc:
        if exc.status_code == 404:
            return None
        raise
    order = body.get("order")
    return order if isinstance(order, dict) else None


def order_belongs_to_session(order: dict[str, Any], customer_id: str, phone: str) -> bool:
    cid = (customer_id or "").strip()
    if cid and str(order.get("customer_id") or "").strip() == cid:
        return True
    want = _phone_digits(phone)
    got = _phone_digits(_order_phone(order))
    return bool(want and got and want == got)


def get_order(
    order_id: str,
    customer_id: str,
    phone: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Retrieve one Square order if it belongs to the session customer. Read-only."""
    oid = (order_id or "").strip()
    if not oid:
        raise AccountError("Order not found.", 404)
    order = retrieve_order(oid, client=client)
    if order is None:
        raise AccountError("Order not found.", 404)
    if not order_belongs_to_session(order, customer_id, phone):
        raise AccountError("This order is not on this account.", 403)
    if not is_paid_order(order):
        raise AccountError("Order not found.", 404)
    return summarize_order(order)


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
    # Previous Orders is paid-only. open_orders stays the in-progress set
    # (pending/making), including unpaid checkout tickets used for live status.
    open_orders = [row for row in summaries if row.get("status") in ("pending", "making")]
    return {
        "ok": True,
        "created": created,
        "customer": public,
        "loyalty": loyalty,
        "orders": summarize_paid_orders(raw_orders, queue=queue),
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


def update_profile(
    customer_id: str,
    phone: str,
    body: dict[str, Any] | None,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Update the session-bound Square customer. Ignores body customer_id."""
    cid = (customer_id or "").strip()
    if not cid:
        raise AccountError("Sign in required.", 401)
    fields = parse_profile_fields(body)
    existing = retrieve_customer(cid, client=client)
    if existing is None:
        raise AccountError("Square customer not found.", 404)
    version: int | None = None
    raw_version = existing.get("version")
    if raw_version is not None:
        try:
            version = int(raw_version)
        except (TypeError, ValueError):
            version = None
    customer = update_customer(cid, fields, version=version, client=client)
    e164 = normalize_phone(phone) or str(customer.get("phone_number") or "").strip()
    payload = _account_payload(customer, created=False, join_loyalty=False, client=client)
    token, expires_at = mint_session_token(cid, e164)
    payload["session_token"] = token
    payload["expires_at"] = expires_at
    return payload


AVATAR_VERSION = 1
AVATAR_ATTR_KEY = "sunshine_avatar"
APPROVED_AVATAR = {
    "skin": ("fair", "peach", "tan", "deep", "rich"),
    "hair": ("bangs", "wavy", "short", "bun", "none"),
    "hair_color": ("brown", "wine", "black", "honey", "cream"),
    "outfit": ("blush", "wine", "cream", "apricot"),
    "apron": ("none", "grey", "blush", "wine"),
    "hat": ("none", "sun", "beanie", "bow"),
    "accessory": ("none", "glasses", "flower", "scarf"),
}
RESERVED_USERNAMES = {
    "sunshine",
    "admin",
    "staff",
    "bakery",
    "ronald",
    "system",
    "moderator",
    "support",
}


def default_avatar() -> dict[str, Any]:
    return {
        "v": AVATAR_VERSION,
        "skin": "peach",
        "hair": "bangs",
        "hair_color": "brown",
        "outfit": "blush",
        "apron": "grey",
        "hat": "sun",
        "accessory": "glasses",
    }


def sanitize_avatar(raw: Any) -> dict[str, Any]:
    recipe = default_avatar()
    if not isinstance(raw, dict):
        return recipe
    for key, allowed in APPROVED_AVATAR.items():
        value = str(raw.get(key) or recipe[key]).strip().lower()
        recipe[key] = value if value in allowed else recipe[key]
    recipe["v"] = AVATAR_VERSION
    return recipe


def normalize_username(raw: str) -> str:
    return "".join(ch for ch in (raw or "").strip().lower() if ch.isalnum() or ch == "_")


def username_error(raw: str) -> str:
    name = normalize_username(raw)
    if len(name) < 3 or len(name) > 20:
        return "Username must be 3–20 letters, numbers, or _."
    if name in RESERVED_USERNAMES or any(name.startswith(r) for r in RESERVED_USERNAMES):
        return "That username is reserved."
    return ""


def display_name_error(raw: str) -> str:
    name = (raw or "").strip()
    if not name or len(name) > 24:
        return "Display name must be 1–24 characters."
    if "@" in name:
        return "Do not use an email as a display name."
    if name.lower() in RESERVED_USERNAMES:
        return "That display name is reserved."
    return ""


def public_game_profile(player_id: str, username: str, display: str, avatar: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_id": player_id,
        "username": normalize_username(username),
        "display_name": (display or "Sunshine Guest").strip()[:24],
        "avatar": sanitize_avatar(avatar),
        "displays": [],
    }


def avatar_store_path() -> str:
    return os.environ.get(
        "SUNSHINE_AVATAR_STORE",
        os.path.join(os.path.dirname(__file__), "avatar_store.json"),
    )


def load_avatar_store() -> dict[str, Any]:
    path = avatar_store_path()
    if not os.path.isfile(path):
        return {"accounts": {}}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"accounts": {}}
    return data if isinstance(data, dict) else {"accounts": {}}


def save_avatar_store(store: dict[str, Any]) -> None:
    path = avatar_store_path()
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=2)
    os.replace(tmp, path)


def _avatar_row_from_blob(blob: Any) -> dict[str, Any]:
    if not isinstance(blob, dict):
        return {}
    return {
        "player_id": str(blob.get("player_id") or "").strip(),
        "username": normalize_username(str(blob.get("username") or "")),
        "display_name": str(blob.get("display_name") or "Sunshine Guest").strip()[:24],
        "avatar": sanitize_avatar(blob.get("avatar") if isinstance(blob.get("avatar"), dict) else {}),
        "customized": bool(blob.get("customized", True)),
        "updated_unix": int(blob.get("updated_unix") or time.time()),
    }


def _empty_avatar_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "player_id": "",
        "customized": False,
        "source": "none",
        "public": public_game_profile("", "", "Sunshine Guest", default_avatar()),
    }


def _avatar_payload(row: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "ok": True,
        "player_id": row.get("player_id", ""),
        "customized": bool(row.get("customized", False)),
        "source": source,
        "public": public_game_profile(
            str(row.get("player_id") or ""),
            str(row.get("username") or ""),
            str(row.get("display_name") or "Sunshine Guest"),
            row.get("avatar") if isinstance(row.get("avatar"), dict) else {},
        ),
    }


def _ensure_avatar_definition(*, client: httpx.Client | None = None) -> None:
    try:
        _square_json(
            "POST",
            "/v2/customers/custom-attribute-definitions",
            {
                "custom_attribute_definition": {
                    "key": AVATAR_ATTR_KEY,
                    "name": "Sunshine Explore look",
                    "description": "COS avatar recipe JSON (player_id, username, display, recipe).",
                    "visibility": "VISIBILITY_READ_WRITE_VALUES",
                    "schema": {
                        "$ref": "https://developer-production-s.squarecdn.com/schemas/v1/common.json#squareup.common.String"
                    },
                }
            },
            client=client,
        )
    except AccountError as exc:
        if exc.status_code in (409, 400):
            return
        raise


def _read_square_avatar(customer_id: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    body = _square_json(
        "GET",
        f"/v2/customers/{customer_id}/custom-attributes/{AVATAR_ATTR_KEY}",
        client=client,
    )
    attr = body.get("custom_attribute") if isinstance(body.get("custom_attribute"), dict) else {}
    raw = attr.get("value")
    if isinstance(raw, dict):
        return _avatar_row_from_blob(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return _avatar_row_from_blob(parsed)
    return {}


def _write_square_avatar(
    customer_id: str,
    row: dict[str, Any],
    *,
    client: httpx.Client | None = None,
) -> None:
    _ensure_avatar_definition(client=client)
    # Square UpsertCustomerCustomAttribute is POST (not PUT).
    _square_json(
        "POST",
        f"/v2/customers/{customer_id}/custom-attributes/{AVATAR_ATTR_KEY}",
        {"custom_attribute": {"value": json.dumps(row, separators=(",", ":"))}},
        client=client,
    )


def upsert_account_avatar(
    customer_id: str,
    body: dict[str, Any],
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    cid = (customer_id or "").strip()
    if not cid:
        raise AccountError("Sign in again.", 401)
    user_err = username_error(str(body.get("username") or ""))
    if user_err:
        raise AccountError(user_err)
    name_err = display_name_error(str(body.get("display_name") or "Sunshine Guest"))
    if name_err:
        raise AccountError(name_err)
    player_id = str(body.get("player_id") or "").strip() or f"plr_{uuid.uuid4().hex[:16]}"
    recipe = sanitize_avatar(body.get("avatar_recipe") or body.get("avatar"))
    username = normalize_username(str(body.get("username") or ""))
    row = {
        "player_id": player_id,
        "username": username,
        "display_name": str(body.get("display_name") or "Sunshine Guest").strip()[:24],
        "avatar": recipe,
        "customized": True,
        "updated_unix": int(time.time()),
    }
    store = load_avatar_store()
    accounts = store.get("accounts") if isinstance(store.get("accounts"), dict) else {}
    for other_id, other in accounts.items():
        if other_id == cid or not isinstance(other, dict):
            continue
        if normalize_username(str(other.get("username") or "")) == username:
            raise AccountError("That username is already used.")
    accounts[cid] = row
    store["accounts"] = accounts
    save_avatar_store(store)
    source = "file"
    if square_token():
        try:
            _write_square_avatar(cid, row, client=client)
            source = "square"
        except AccountError:
            # File write already succeeded. Cloud Run disk is ephemeral; Square
            # is the forever store when CUSTOMERS_WRITE + custom attributes work.
            source = "file"
    return _avatar_payload(row, source)


def get_account_avatar(customer_id: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    cid = (customer_id or "").strip()
    if square_token():
        try:
            row = _read_square_avatar(cid, client=client)
            if row:
                return _avatar_payload(row, "square")
        except AccountError as exc:
            if exc.status_code not in (404, 400):
                raise
    store = load_avatar_store()
    accounts = store.get("accounts") if isinstance(store.get("accounts"), dict) else {}
    row = accounts.get(cid) if isinstance(accounts.get(cid), dict) else {}
    if not row:
        return _empty_avatar_payload()
    return _avatar_payload(_avatar_row_from_blob(row), "file")


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
    from fastapi import Body

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

    @app.get("/order/api/orders/{order_id}")
    def order_api_order_detail(request: Request, order_id: str):
        try:
            session = session_from_request(request)
            return {
                "ok": True,
                "order": get_order(order_id, session["customer_id"], session["phone"]),
            }
        except AccountError as exc:
            return _json_error(exc)

    @app.post("/order/api/account/profile")
    @app.post("/order/api/customer/profile")
    def order_api_account_profile(request: Request, body: dict = Body(...)):
        try:
            session = session_from_request(request)
            return update_profile(session["customer_id"], session["phone"], body)
        except AccountError as exc:
            return _json_error(exc)

    @app.get("/order/api/account/avatar")
    def order_api_account_avatar_get(request: Request):
        try:
            session = session_from_request(request)
            return get_account_avatar(session["customer_id"])
        except AccountError as exc:
            return _json_error(exc)

    @app.put("/order/api/account/avatar")
    @app.post("/order/api/account/avatar")
    @app.patch("/order/api/account/avatar")
    def order_api_account_avatar_put(request: Request, body: dict = Body(...)):
        try:
            session = session_from_request(request)
            return upsert_account_avatar(session["customer_id"], body or {})
        except AccountError as exc:
            return _json_error(exc)
