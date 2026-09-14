"""Square Customers + Loyalty + Orders for the Sunshine phone login.

Drop this file into bakery-local as ``app/account.py`` and register the
``/order/api/account`` routes (see README.md). The Square access token
stays in env / Secret Manager — never in the APK.

This module has no invented customer directory. Every customer id comes
from Square SearchCustomers / CreateCustomer.
"""

from __future__ import annotations

import os
import re
import uuid
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


class AccountError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


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
    phone = str(customer.get("phone_number") or customer.get("phone") or "").strip()
    return {
        "id": str(customer.get("id") or "").strip(),
        "phone": phone,
        "given_name": str(customer.get("given_name") or "").strip(),
        "family_name": str(customer.get("family_name") or "").strip(),
        "nickname": str(customer.get("nickname") or "").strip(),
        "display_name": display_name(customer),
        "email": str(customer.get("email_address") or customer.get("email") or "").strip(),
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
    join = bool(body.get("join_loyalty", True))
    existing = search_customer_by_phone(phone, client=client)
    if existing:
        return _account_payload(existing, created=False, join_loyalty=join, client=client)
    created = create_customer(phone, body, client=client)
    return _account_payload(created, created=True, join_loyalty=join, client=client)


def get_account(customer_id: str = "", phone: str = "", *, client: httpx.Client | None = None) -> dict[str, Any]:
    e164 = normalize_phone(phone)
    customer = retrieve_customer(customer_id, client=client) if customer_id else None
    if customer is None and e164:
        customer = search_customer_by_phone(e164, client=client)
    if customer is None:
        raise AccountError("Square customer not found.", 404)
    return _account_payload(customer, created=False, join_loyalty=True, client=client)


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

    return JSONResponse({"ok": False, "error": exc.message}, status_code=exc.status_code)


def mount(app) -> None:
    """Register Square customer routes on a FastAPI app (bakery-drinks)."""
    from fastapi import Body, Query

    @app.post("/order/api/account/phone")
    @app.post("/order/api/customer")
    def order_api_customer_write(body: dict = Body(...)):
        try:
            return login_or_signup(body)
        except AccountError as exc:
            return _json_error(exc)

    @app.get("/order/api/account")
    @app.get("/order/api/customer")
    def order_api_customer_read(customer_id: str = Query(""), phone: str = Query("")):
        try:
            return get_account(customer_id, phone)
        except AccountError as exc:
            return _json_error(exc)

    @app.get("/order/api/account/status")
    def order_api_account_status(customer_id: str = Query(""), phone: str = Query("")):
        try:
            return get_status(customer_id, phone)
        except AccountError as exc:
            return _json_error(exc)

    @app.get("/order/api/orders")
    def order_api_orders(customer_id: str = Query(""), phone: str = Query("")):
        try:
            return list_orders(customer_id, phone)
        except AccountError as exc:
            return _json_error(exc)
