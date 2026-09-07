"""Irondale QR drink self-order: curated menu + Square CreatePaymentLink.

Square access token stays in env / Secret Manager. Never sent to the browser.
Beta catalog is server-side (six drinks + modifiers), not a live Catalog API
sync. Line item names match POS drink names so getorders / is_drink still
pick them up on the public drink board.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.drinks import is_drink

CHICAGO = ZoneInfo("America/Chicago")
SQUARE_VERSION = "2025-01-23"
DEFAULT_LOCATION_ID = "L4CK6YWGT5XQX"
DEFAULT_API_BASE = "https://connect.squareup.com"
ROOT = Path(__file__).resolve().parent.parent
DRINK_PHOTO_DIR = ROOT / "static" / "order" / "drinks"


def photo_url(stem: str) -> str:
    """Prefer a real photo if Glenn drops one in static/order/drinks; else the svg."""
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".svg"):
        if (DRINK_PHOTO_DIR / f"{stem}{ext}").is_file():
            return f"/static/order/drinks/{stem}{ext}"
    return f"/static/order/drinks/{stem}.svg"
DEMO_MAKING_ID = "demo"
DEMO_READY_ID = "demo-ready"

# Photo paths: drop a jpg/png next to the svg with the same stem to replace art.

DRINKS: list[dict[str, Any]] = [
    {
        "id": "viet-iced-coffee",
        "name": "Viet iced coffee",
        "square_name": "Vietnamese Coffee",
        "description": "espresso, condensed milk, ice",
        "category": "coffee",
        "price_cents": 550,
        "defaults": {"milk": "condensed", "sweet": "normal"},
        "groups": [
            {
                "id": "milk",
                "label": "Milk",
                "type": "single",
                "options": [
                    {"id": "whole", "label": "Whole"},
                    {"id": "oat", "label": "Oat"},
                    {"id": "condensed", "label": "Condensed"},
                ],
            },
            {
                "id": "sweet",
                "label": "Sweet",
                "type": "single",
                "options": [
                    {"id": "less", "label": "Less"},
                    {"id": "normal", "label": "Normal"},
                    {"id": "extra", "label": "Extra"},
                ],
            },
            {
                "id": "addons",
                "label": "Add-ons",
                "type": "multi",
                "options": [
                    {"id": "boba", "label": "Boba", "price_cents": 75},
                    {"id": "extra-shot", "label": "Extra shot", "price_cents": 100},
                ],
            },
        ],
    },
    {
        "id": "hot-coffee",
        "name": "Hot coffee",
        "square_name": "Hot Coffee",
        "description": "drip, to-go cup",
        "category": "coffee",
        "price_cents": 400,
        "defaults": {"milk": "black", "sweet": "normal"},
        "groups": [
            {
                "id": "milk",
                "label": "Milk",
                "type": "single",
                "options": [
                    {"id": "black", "label": "Black"},
                    {"id": "whole", "label": "Whole"},
                    {"id": "oat", "label": "Oat"},
                ],
            },
            {
                "id": "sweet",
                "label": "Sweet",
                "type": "single",
                "options": [
                    {"id": "less", "label": "Less"},
                    {"id": "normal", "label": "Normal"},
                    {"id": "extra", "label": "Extra"},
                ],
            },
            {
                "id": "addons",
                "label": "Add-ons",
                "type": "multi",
                "options": [
                    {"id": "extra-shot", "label": "Extra shot", "price_cents": 100},
                ],
            },
        ],
    },
    {
        "id": "biscoff-coffee",
        "name": "Biscoff coffee",
        "square_name": "Biscoff Coffee",
        "description": "iced, cookie on top",
        "category": "coffee",
        "price_cents": 650,
        "defaults": {"milk": "oat", "sweet": "extra"},
        "groups": [
            {
                "id": "milk",
                "label": "Milk",
                "type": "single",
                "options": [
                    {"id": "whole", "label": "Whole"},
                    {"id": "oat", "label": "Oat"},
                    {"id": "condensed", "label": "Condensed"},
                ],
            },
            {
                "id": "sweet",
                "label": "Sweet",
                "type": "single",
                "options": [
                    {"id": "less", "label": "Less"},
                    {"id": "normal", "label": "Normal"},
                    {"id": "extra", "label": "Extra"},
                ],
            },
            {
                "id": "addons",
                "label": "Add-ons",
                "type": "multi",
                "options": [
                    {"id": "extra-shot", "label": "Extra shot", "price_cents": 100},
                ],
            },
        ],
    },
    {
        "id": "matcha-latte",
        "name": "Matcha latte",
        "square_name": "Matcha Latte",
        "description": "iced matcha, milk",
        "category": "tea",
        "price_cents": 650,
        "defaults": {"milk": "whole", "sweet": "normal"},
        "groups": [
            {
                "id": "milk",
                "label": "Milk",
                "type": "single",
                "options": [
                    {"id": "whole", "label": "Whole"},
                    {"id": "oat", "label": "Oat"},
                ],
            },
            {
                "id": "sweet",
                "label": "Sweet",
                "type": "single",
                "options": [
                    {"id": "less", "label": "Less"},
                    {"id": "normal", "label": "Normal"},
                    {"id": "extra", "label": "Extra"},
                ],
            },
            {
                "id": "addons",
                "label": "Add-ons",
                "type": "multi",
                "options": [
                    {"id": "boba", "label": "Boba", "price_cents": 75},
                ],
            },
        ],
    },
    {
        "id": "milk-tea",
        "name": "Milk tea",
        "square_name": "Milk Tea",
        "description": "boba at the bottom",
        "category": "tea",
        "price_cents": 600,
        "defaults": {"flavor": "classic", "milk": "whole", "sweet": "normal", "addons": ["boba"]},
        "groups": [
            {
                "id": "flavor",
                "label": "Flavor",
                "type": "single",
                "options": [
                    {"id": "classic", "label": "Classic"},
                    {"id": "taro", "label": "Taro"},
                    {"id": "brown-sugar", "label": "Brown sugar"},
                    {"id": "thai", "label": "Thai"},
                ],
            },
            {
                "id": "milk",
                "label": "Milk",
                "type": "single",
                "options": [
                    {"id": "whole", "label": "Whole"},
                    {"id": "oat", "label": "Oat"},
                ],
            },
            {
                "id": "sweet",
                "label": "Sweet",
                "type": "single",
                "options": [
                    {"id": "less", "label": "Less"},
                    {"id": "normal", "label": "Normal"},
                    {"id": "extra", "label": "Extra"},
                ],
            },
            {
                "id": "addons",
                "label": "Add-ons",
                "type": "multi",
                "options": [
                    {"id": "boba", "label": "Boba", "price_cents": 75},
                ],
            },
        ],
    },
    {
        "id": "fruit-tea",
        "name": "Fruit tea",
        "square_name": "Fruit Tea",
        "description": "iced tea, fruit pearls",
        "category": "tea",
        "price_cents": 600,
        "defaults": {"flavor": "strawberry", "sweet": "normal", "addons": ["boba"]},
        "groups": [
            {
                "id": "flavor",
                "label": "Flavor",
                "type": "single",
                "options": [
                    {"id": "strawberry", "label": "Strawberry"},
                    {"id": "mango", "label": "Mango"},
                    {"id": "peach", "label": "Peach"},
                ],
            },
            {
                "id": "sweet",
                "label": "Sweet",
                "type": "single",
                "options": [
                    {"id": "less", "label": "Less"},
                    {"id": "normal", "label": "Normal"},
                    {"id": "extra", "label": "Extra"},
                ],
            },
            {
                "id": "addons",
                "label": "Add-ons",
                "type": "multi",
                "options": [
                    {"id": "boba", "label": "Boba", "price_cents": 75},
                ],
            },
        ],
    },
]

DRINK_BY_ID = {d["id"]: d for d in DRINKS}


class OrderError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def square_token() -> str:
    return (os.environ.get("SQUARE_ACCESS_TOKEN") or "").strip()


def irondale_location_id() -> str:
    """Irondale only for this beta. Prefer the explicit Irondale var."""
    for key in ("SQUARE_LOCATION_ID_IRONDALE", "LOCATION_ID", "SQUARE_LOCATION_ID"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return DEFAULT_LOCATION_ID


def square_api_base() -> str:
    return (os.environ.get("SQUARE_API_BASE") or DEFAULT_API_BASE).rstrip("/")


def bakery_service_name() -> str:
    raw = (os.environ.get("BAKERY_SERVICE") or "laptop").strip().lower()
    if raw in ("drinks", "desk", "laptop"):
        return raw
    return "laptop"


def pay_mode() -> str:
    if square_token():
        return "square"
    if bakery_service_name() == "drinks":
        return "off"
    return "demo"


def public_origin(headers: dict[str, str] | None, fallback_url) -> str:
    forced = (os.environ.get("ORDER_PUBLIC_URL") or "").strip().rstrip("/")
    if forced:
        return forced
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    proto = (hdrs.get("x-forwarded-proto") or getattr(fallback_url, "scheme", "") or "https")
    proto = str(proto).split(",")[0].strip() or "https"
    host = (
        hdrs.get("x-forwarded-host")
        or hdrs.get("host")
        or getattr(fallback_url, "netloc", "")
        or ""
    )
    host = str(host).split(",")[0].strip()
    if not host:
        return proto
    return f"{proto}://{host}"


def square_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Square-Version": SQUARE_VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def menu_payload() -> dict[str, Any]:
    drinks = []
    for drink in DRINKS:
        row = dict(drink)
        row["photo"] = photo_url(str(drink["id"]))
        drinks.append(row)
    return {
        "location": "Irondale",
        "pay_mode": pay_mode(),
        "drinks": drinks,
    }


def _option(drink: dict, group_id: str, option_id: str) -> dict[str, Any] | None:
    for group in drink.get("groups") or []:
        if group.get("id") != group_id:
            continue
        for opt in group.get("options") or []:
            if opt.get("id") == option_id:
                return {**opt, "group": group_id, "group_label": group.get("label") or "", "type": group.get("type")}
    return None


def _normalize_phone(raw: str) -> str:
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


def _pickup_label(pickup: str) -> str:
    return "to go" if pickup == "to-go" else "for here"


def _order_number() -> str:
    now = datetime.now(CHICAGO)
    return str((now.hour * 60 + now.minute) % 90 + 10)


def validate_cart(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise OrderError("Cart is required.")
    name = str(body.get("name") or "").strip()
    if not name or len(name) > 40:
        raise OrderError("Name for pickup is required.")
    pickup = str(body.get("pickup") or "to-go").strip().lower()
    if pickup in ("togo", "to go"):
        pickup = "to-go"
    if pickup in ("forhere", "for here", "here"):
        pickup = "for-here"
    if pickup not in ("to-go", "for-here"):
        raise OrderError("Pickup must be to-go or for here.")
    phone = _normalize_phone(str(body.get("phone") or ""))
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise OrderError("Add a drink first.")
    if len(raw_items) > 12:
        raise OrderError("Too many drinks for one order.")

    lines: list[dict[str, Any]] = []
    total = 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise OrderError("Each item must be an object.")
        drink = DRINK_BY_ID.get(str(raw.get("id") or "").strip())
        if not drink:
            raise OrderError("Unknown drink.")
        try:
            qty = int(raw.get("qty") or 1)
        except (TypeError, ValueError) as exc:
            raise OrderError("Quantity must be a number.") from exc
        if qty < 1 or qty > 9:
            raise OrderError("Quantity must be 1 to 9.")
        selections = raw.get("modifiers") if isinstance(raw.get("modifiers"), dict) else {}
        defaults = drink.get("defaults") or {}
        mods: list[dict[str, Any]] = []
        line_cents = int(drink["price_cents"])
        for group in drink.get("groups") or []:
            gid = str(group.get("id") or "")
            gtype = str(group.get("type") or "single")
            if gtype == "multi":
                chosen = selections.get(gid, defaults.get(gid, []))
                if chosen is None:
                    chosen = []
                if isinstance(chosen, str):
                    chosen = [chosen]
                if not isinstance(chosen, list):
                    raise OrderError(f"Invalid {group.get('label') or gid}.")
                seen: set[str] = set()
                for oid in chosen:
                    key = str(oid).strip()
                    if not key or key in seen:
                        continue
                    opt = _option(drink, gid, key)
                    if not opt:
                        raise OrderError(f"Unknown {group.get('label') or gid} option.")
                    seen.add(key)
                    extra = int(opt.get("price_cents") or 0)
                    line_cents += extra
                    mods.append(opt)
            else:
                oid = str(selections.get(gid) or defaults.get(gid) or "").strip()
                if not oid:
                    raise OrderError(f"Choose {group.get('label') or gid}.")
                opt = _option(drink, gid, oid)
                if not opt:
                    raise OrderError(f"Unknown {group.get('label') or gid} option.")
                extra = int(opt.get("price_cents") or 0)
                line_cents += extra
                mods.append(opt)
        if not is_drink(str(drink["square_name"])):
            raise OrderError("That item is not a drink.")
        detail = " · ".join(str(m["label"]) for m in mods if m.get("label"))
        lines.append(
            {
                "id": drink["id"],
                "name": drink["name"],
                "square_name": drink["square_name"],
                "photo": photo_url(str(drink["id"])),
                "qty": qty,
                "modifiers": mods,
                "detail": detail,
                "unit_cents": line_cents,
                "line_cents": line_cents * qty,
            }
        )
        total += line_cents * qty
    if total < 1:
        raise OrderError("Cart total is empty.")
    return {
        "name": name,
        "pickup": pickup,
        "phone": phone,
        "items": lines,
        "total_cents": total,
        "order_number": _order_number(),
    }


def _mod_labels(mods: list[dict[str, Any]]) -> list[str]:
    return [str(m.get("label") or "").strip() for m in mods if str(m.get("label") or "").strip()]


def build_payment_link_body(
    cart: dict[str, Any],
    *,
    location_id: str,
    redirect_url: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    pickup_note = _pickup_label(cart["pickup"])
    line_items: list[dict[str, Any]] = []
    for line in cart["items"]:
        labels = _mod_labels(line["modifiers"])
        modifiers = [
            {
                "name": label,
                "quantity": "1",
                "base_price_money": {"amount": int(mod.get("price_cents") or 0), "currency": "USD"},
            }
            for mod, label in zip(line["modifiers"], labels)
        ]
        note_bits = [bit for bit in (line.get("detail"), pickup_note, f"Name: {cart['name']}") if bit]
        item: dict[str, Any] = {
            "name": line["square_name"],
            "quantity": str(int(line["qty"])),
            "item_type": "ITEM",
            "base_price_money": {"amount": int(line["unit_cents"]) - sum(int(m.get("price_cents") or 0) for m in line["modifiers"]), "currency": "USD"},
            "note": " · ".join(note_bits)[:500],
        }
        if modifiers:
            item["modifiers"] = modifiers
        line_items.append(item)

    recipient: dict[str, Any] = {"display_name": cart["name"]}
    if cart.get("phone"):
        recipient["phone_number"] = cart["phone"]

    body: dict[str, Any] = {
        "idempotency_key": (idempotency_key or uuid.uuid4().hex)[:192],
        "description": "Sunshine's Irondale QR drinks",
        "payment_note": f"{cart['name']} · {pickup_note} · QR Irondale",
        "checkout_options": {
            "redirect_url": redirect_url,
            "ask_for_shipping_address": False,
            "allow_tipping": False,
            "enable_coupon": False,
            "accepted_payment_methods": {
                "apple_pay": True,
                "google_pay": True,
                "cash_app_pay": False,
            },
        },
        "order": {
            "location_id": location_id,
            "reference_id": f"QR-{cart['order_number']}"[:40],
            "source": {"name": "Sunshine QR"},
            "line_items": line_items,
            "pricing_options": {"auto_apply_taxes": True},
            "fulfillments": [
                {
                    "type": "PICKUP",
                    "state": "PROPOSED",
                    "pickup_details": {
                        "schedule_type": "ASAP",
                        "pickup_at": (datetime.now(timezone.utc) + timedelta(minutes=8)).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                        "prep_time_duration": "PT8M",
                        "recipient": recipient,
                        "note": pickup_note,
                    },
                }
            ],
        },
    }
    if cart.get("phone"):
        body["pre_populated_data"] = {"buyer_phone_number": cart["phone"]}
    return body


def _square_error_message(body: Any) -> str:
    if not isinstance(body, dict):
        return "Square checkout failed."
    errors = body.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        detail = str(first.get("detail") or first.get("code") or "").strip()
        if detail:
            return detail[:240]
    return "Square checkout failed."


def create_payment_link(
    cart: dict[str, Any],
    *,
    origin: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    token = square_token()
    if not token:
        raise OrderError("Square is not configured.", 503)
    location_id = irondale_location_id()
    redirect = f"{origin.rstrip('/')}/order/status"
    payload = build_payment_link_body(cart, location_id=location_id, redirect_url=redirect)
    own = client is None
    http = client or httpx.Client(timeout=20.0)
    try:
        response = http.post(
            f"{square_api_base()}/v2/online-checkout/payment-links",
            headers=square_headers(token),
            json=payload,
        )
    finally:
        if own:
            http.close()
    data = response.json() if response.content else {}
    if response.status_code >= 400 or not isinstance(data, dict):
        raise OrderError(_square_error_message(data), 502)
    link = data.get("payment_link") if isinstance(data.get("payment_link"), dict) else {}
    url = str(link.get("url") or link.get("long_url") or "").strip()
    order_id = str(link.get("order_id") or "").strip()
    if not url or not order_id:
        raise OrderError("Square did not return a checkout URL.", 502)
    if "?" in redirect:
        status_url = f"{redirect}&oid={order_id}"
    else:
        status_url = f"{redirect}?oid={order_id}"
    return {
        "url": url,
        "order_id": order_id,
        "payment_link_id": str(link.get("id") or ""),
        "status_url": status_url,
        "demo": False,
    }


def demo_checkout(cart: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": f"/order/status?oid={DEMO_MAKING_ID}",
        "order_id": DEMO_MAKING_ID,
        "payment_link_id": "",
        "status_url": f"/order/status?oid={DEMO_MAKING_ID}",
        "demo": True,
        "cart": _status_from_cart(cart, status="making"),
    }


def checkout(body: dict[str, Any], *, origin: str, client: httpx.Client | None = None) -> dict[str, Any]:
    cart = validate_cart(body)
    mode = pay_mode()
    if mode == "off":
        raise OrderError("Ordering is not configured yet.", 503)
    if mode == "demo":
        return demo_checkout(cart)
    result = create_payment_link(cart, origin=origin, client=client)
    result["total_cents"] = cart["total_cents"]
    return result


def _photo_for_square_name(name: str) -> str:
    needle = (name or "").casefold()
    for drink in DRINKS:
        if drink["square_name"].casefold() == needle or drink["name"].casefold() == needle:
            return photo_url(str(drink["id"]))
    for drink in DRINKS:
        if drink["square_name"].casefold() in needle or drink["name"].casefold() in needle:
            return photo_url(str(drink["id"]))
    return photo_url(str(DRINKS[0]["id"]))


def _status_from_cart(cart: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "status": status,
        "paid": status in ("making", "ready"),
        "order_id": DEMO_MAKING_ID if status != "ready" else DEMO_READY_ID,
        "order_number": str(cart.get("order_number") or "14"),
        "pickup": _pickup_label(cart.get("pickup") or "to-go"),
        "name": cart.get("name") or "",
        "text_opt_in": bool(cart.get("phone")),
        "items": [
            {
                "name": line["name"],
                "qty": line["qty"],
                "detail": line.get("detail") or "",
                "photo": line.get("photo") or _photo_for_square_name(line["name"]),
            }
            for line in cart.get("items") or []
        ],
        "demo": True,
    }


def _fulfillment_state(order: dict[str, Any]) -> str:
    for ful in order.get("fulfillments") or []:
        if not isinstance(ful, dict):
            continue
        state = str(ful.get("state") or "").upper()
        if state:
            return state
    return ""


def _order_paid(order: dict[str, Any]) -> bool:
    if str(order.get("state") or "").upper() in ("COMPLETED",):
        tenders = order.get("tenders") or []
        if tenders:
            return True
    for tender in order.get("tenders") or []:
        if not isinstance(tender, dict):
            continue
        if str(tender.get("type") or "").upper() in ("CARD", "WALLET", "SQUARE_GIFT_CARD", "BANK_ACCOUNT", "BUY_NOW_PAY_LATER"):
            return True
        if tender.get("id"):
            return True
    net = order.get("net_amounts") or {}
    if isinstance(net, dict) and int((net.get("total_money") or {}).get("amount") or 0) > 0:
        if order.get("tenders"):
            return True
    return str(order.get("state") or "").upper() == "COMPLETED"


def status_from_square_order(order: dict[str, Any], order_id: str) -> dict[str, Any]:
    if str(order.get("state") or "").upper() == "CANCELED":
        label = "canceled"
    else:
        ful = _fulfillment_state(order)
        paid = _order_paid(order)
        if ful in ("PREPARED", "COMPLETED"):
            label = "ready"
        elif paid or ful in ("PROPOSED", "RESERVED"):
            label = "making"
        else:
            label = "pending"

    pickup = "to go"
    name = ""
    phone = ""
    for ful in order.get("fulfillments") or []:
        if not isinstance(ful, dict):
            continue
        details = ful.get("pickup_details") or {}
        if not isinstance(details, dict):
            continue
        note = str(details.get("note") or "").casefold()
        if "here" in note:
            pickup = "for here"
        recipient = details.get("recipient") or {}
        if isinstance(recipient, dict):
            name = str(recipient.get("display_name") or name).strip()
            phone = str(recipient.get("phone_number") or phone).strip()

    items: list[dict[str, Any]] = []
    for item in order.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        nm = str(item.get("name") or "").strip()
        if not is_drink(nm):
            continue
        try:
            qty = int(float(item.get("quantity") or 1))
        except (TypeError, ValueError):
            qty = 1
        details = []
        for mod in item.get("modifiers") or []:
            if isinstance(mod, dict) and mod.get("name"):
                details.append(str(mod["name"]).strip())
        note = str(item.get("note") or "").strip()
        if note and not details:
            details.append(note)
        items.append(
            {
                "name": nm,
                "qty": max(1, qty),
                "detail": " · ".join(details),
                "photo": _photo_for_square_name(nm),
            }
        )

    ref = str(order.get("reference_id") or "")
    number = ref.replace("QR-", "").strip() or "—"
    return {
        "status": label,
        "paid": label in ("making", "ready"),
        "order_id": order_id,
        "order_number": number,
        "pickup": pickup,
        "name": name,
        "text_opt_in": bool(phone),
        "items": items,
        "demo": False,
    }


def retrieve_square_order(order_id: str, client: httpx.Client | None = None) -> dict[str, Any] | None:
    token = square_token()
    oid = (order_id or "").strip()
    if not token or not oid:
        return None
    own = client is None
    http = client or httpx.Client(timeout=15.0)
    try:
        response = http.get(
            f"{square_api_base()}/v2/orders/{oid}",
            headers=square_headers(token),
        )
    finally:
        if own:
            http.close()
    if response.status_code != 200:
        return None
    body = response.json() if response.content else {}
    order = body.get("order") if isinstance(body, dict) else None
    return order if isinstance(order, dict) else None


def retrieve_payment_link_order_id(checkout_id: str, client: httpx.Client | None = None) -> str:
    token = square_token()
    cid = (checkout_id or "").strip()
    if not token or not cid:
        return ""
    own = client is None
    http = client or httpx.Client(timeout=15.0)
    try:
        response = http.get(
            f"{square_api_base()}/v2/online-checkout/payment-links/{cid}",
            headers=square_headers(token),
        )
    finally:
        if own:
            http.close()
    if response.status_code != 200:
        return ""
    body = response.json() if response.content else {}
    link = body.get("payment_link") if isinstance(body, dict) else None
    if not isinstance(link, dict):
        return ""
    return str(link.get("order_id") or "").strip()


def lookup_status(
    *,
    order_id: str = "",
    checkout_id: str = "",
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    oid = (order_id or "").strip()
    cid = (checkout_id or "").strip()
    if oid in (DEMO_MAKING_ID, "demo-making"):
        return {
            "status": "making",
            "paid": True,
            "order_id": DEMO_MAKING_ID,
            "order_number": "14",
            "pickup": "to go",
            "name": "",
            "text_opt_in": False,
            "items": [],
            "demo": True,
        }
    if oid == DEMO_READY_ID:
        return {
            "status": "ready",
            "paid": True,
            "order_id": DEMO_READY_ID,
            "order_number": "14",
            "pickup": "to go",
            "name": "",
            "text_opt_in": False,
            "items": [],
            "demo": True,
        }
    if cid and not oid:
        oid = retrieve_payment_link_order_id(cid, client=client)
    if not oid:
        raise OrderError("Missing order.", 400)
    order = retrieve_square_order(oid, client=client)
    if not order:
        raise OrderError("Order not found yet.", 404)
    return status_from_square_order(order, oid)


def looks_like_square_order_id(order_id: str) -> bool:
    oid = (order_id or "").strip()
    if not oid or oid.startswith("anon-") or oid.startswith("demo"):
        return False
    if oid.isdigit() and len(oid) < 12:
        return False
    return len(oid) >= 8


def mark_pickup_prepared(order_id: str, client: httpx.Client | None = None) -> bool:
    """Kitchen tap-to-clear: mark Square pickup PREPARED so the phone shows Ready."""
    token = square_token()
    oid = (order_id or "").strip()
    if not token or not looks_like_square_order_id(oid):
        return False
    own = client is None
    http = client or httpx.Client(timeout=15.0)
    try:
        order = retrieve_square_order(oid, client=http)
        if not order:
            return False
        fulfillments = [f for f in (order.get("fulfillments") or []) if isinstance(f, dict) and f.get("uid")]
        if not fulfillments:
            return False
        version = order.get("version")
        if version is None:
            return False
        current = str(fulfillments[0].get("state") or "").upper()
        if current in ("PREPARED", "COMPLETED"):
            return True
        uid = str(fulfillments[0]["uid"])
        steps = ["PREPARED"] if current == "RESERVED" else ["RESERVED", "PREPARED"]
        for state in steps:
            response = http.put(
                f"{square_api_base()}/v2/orders/{oid}",
                headers=square_headers(token),
                json={
                    "order": {
                        "version": version,
                        "fulfillments": [{"uid": uid, "state": state}],
                    }
                },
            )
            body = response.json() if response.content else {}
            updated = body.get("order") if isinstance(body, dict) else None
            if response.status_code >= 400 or not isinstance(updated, dict):
                return False
            version = updated.get("version", version)
        return True
    finally:
        if own:
            http.close()
