"""Live Square Catalog → Irondale QR drink menu.

SearchCatalogItems (Drink category + Irondale location), then
BatchRetrieveCatalogObjects for modifier lists and images. Token stays
server-side. Browser only sees names, prices, photos, and catalog ids.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from app.drinks import DRINK_CATEGORY_IDS, categories_from_line_item, is_drink

SQUARE_VERSION = "2025-01-23"
ROOT = Path(__file__).resolve().parent.parent
DRINK_PHOTO_DIR = ROOT / "static" / "order" / "drinks"
GENERIC_VARIATION_NAMES = frozenset(
    {"regular", "standard", "default", "item", "regular iced", "regular hot"}
)
_TEA_NEEDLES = ("tea", "matcha", "boba", "lemonade", "chai")
_COFFEE_NEEDLES = (
    "coffee",
    "latte",
    "espresso",
    "mocha",
    "americano",
    "cappuccino",
    "cortado",
    "macchiato",
    "cold brew",
    "cortadito",
)
_PHOTO_STEMS = (
    (("vietnamese", "viet iced", "viet coffee"), "viet-iced-coffee"),
    (("biscoff",), "biscoff-coffee"),
    (("matcha",), "matcha-latte"),
    (("milk tea",), "milk-tea"),
    (("fruit tea",), "fruit-tea"),
    (("hot coffee", "drip coffee"), "hot-coffee"),
    (("coffee",), "hot-coffee"),
)

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"expires": 0.0, "location_id": "", "drinks": None}


class CatalogError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def photo_url(stem: str) -> str:
    """Prefer a real photo if Glenn drops one in static/order/drinks; else the svg."""
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".svg"):
        if (DRINK_PHOTO_DIR / f"{stem}{ext}").is_file():
            return f"/static/order/drinks/{stem}{ext}"
    return f"/static/order/drinks/{stem}.svg"


def photo_for_name(name: str, remote_url: str | None = None) -> str:
    url = (remote_url or "").strip()
    if url.startswith("https://") and " " not in url and len(url) < 2000:
        return url
    n = (name or "").casefold()
    for needles, stem in _PHOTO_STEMS:
        if any(needle in n for needle in needles):
            return photo_url(stem)
    return photo_url("viet-iced-coffee")


def cache_ttl_seconds() -> int:
    raw = (os.environ.get("CATALOG_CACHE_SECONDS") or "90").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 90


def clear_menu_cache() -> None:
    with _cache_lock:
        _cache["expires"] = 0.0
        _cache["drinks"] = None
        _cache["location_id"] = ""


def square_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Square-Version": SQUARE_VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _json_body(response: httpx.Response) -> dict[str, Any]:
    if not getattr(response, "content", None):
        return {}
    try:
        data = response.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _square_error_message(body: Any) -> str:
    if not isinstance(body, dict):
        return "Square catalog failed."
    errors = body.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        code = str(first.get("code") or "").upper()
        detail = str(first.get("detail") or first.get("code") or "").strip()
        if code in ("INSUFFICIENT_SCOPES", "UNAUTHORIZED") or "scope" in detail.casefold():
            return "Square token needs ITEMS_READ to load the drink catalog."
        if detail:
            return detail[:240]
    return "Square catalog failed."


def _raise_if_bad(response: httpx.Response, data: dict[str, Any]) -> None:
    if response.status_code >= 400:
        raise CatalogError(_square_error_message(data), 502)


def present_at_location(obj: dict[str, Any] | None, location_id: str) -> bool:
    if not isinstance(obj, dict) or not location_id:
        return False
    absent = obj.get("absent_at_location_ids") or []
    if location_id in absent:
        return False
    if obj.get("present_at_all_locations"):
        return True
    present = obj.get("present_at_location_ids") or []
    if present:
        return location_id in present
    return True


def _sold_out(variation: dict[str, Any], location_id: str) -> bool:
    data = variation.get("item_variation_data") or {}
    if not isinstance(data, dict):
        return True
    if data.get("sold_out"):
        return True
    for ov in data.get("location_overrides") or []:
        if not isinstance(ov, dict):
            continue
        if ov.get("location_id") == location_id and ov.get("sold_out"):
            return True
    return False


def variation_price_cents(variation: dict[str, Any], location_id: str) -> int | None:
    data = variation.get("item_variation_data") or {}
    if not isinstance(data, dict):
        return None
    pricing = str(data.get("pricing_type") or "FIXED_PRICING").upper()
    for ov in data.get("location_overrides") or []:
        if not isinstance(ov, dict) or ov.get("location_id") != location_id:
            continue
        money = ov.get("price_money") if isinstance(ov.get("price_money"), dict) else None
        if money and money.get("amount") is not None:
            try:
                return int(money.get("amount") or 0)
            except (TypeError, ValueError):
                return None
    money = data.get("price_money") if isinstance(data.get("price_money"), dict) else None
    if money and money.get("amount") is not None:
        try:
            return int(money.get("amount") or 0)
        except (TypeError, ValueError):
            return None
    if pricing == "VARIABLE_PRICING":
        return None
    return 0


def ui_category(name: str, description: str = "") -> str:
    n = f"{name} {description}".casefold()
    tea = any(needle in n for needle in _TEA_NEEDLES)
    coffee = any(needle in n for needle in _COFFEE_NEEDLES)
    if tea and not coffee:
        return "tea"
    if coffee and not tea:
        return "coffee"
    if tea:
        return "tea"
    if coffee:
        return "coffee"
    return "more"


def _item_data(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("item_data")
    return data if isinstance(data, dict) else {}


def _item_is_drink(item: dict[str, Any]) -> bool:
    data = _item_data(item)
    cat_ids, cat_names = categories_from_line_item(
        {
            "name": data.get("name"),
            "category_id": data.get("category_id"),
            "categories": data.get("categories"),
            "catalog_categories": data.get("categories"),
            "item_data": data,
        }
    )
    reporting = data.get("reporting_category")
    if isinstance(reporting, dict):
        rid = reporting.get("id") or reporting.get("category_id")
        if rid:
            cat_ids.append(str(rid).strip())
        rname = reporting.get("name")
        if rname:
            cat_names.append(str(rname).strip())
    if data.get("category_id"):
        cat_ids.append(str(data["category_id"]).strip())
    known = set(DRINK_CATEGORY_IDS)
    if known.intersection({c for c in cat_ids if c}):
        return True
    return is_drink(str(data.get("name") or ""), category_ids=cat_ids, category_names=cat_names)


def _display_name(item_name: str, variation_name: str) -> str:
    item_name = (item_name or "").strip() or "Drink"
    variation_name = (variation_name or "").strip()
    if not variation_name or variation_name.casefold() in GENERIC_VARIATION_NAMES:
        return item_name
    if variation_name.casefold() == item_name.casefold():
        return item_name
    return f"{item_name} — {variation_name}"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _effective_bound(info: dict[str, Any], list_data: dict[str, Any], key: str) -> int:
    raw = info.get(key)
    if raw is None or _int(raw, -1) < 0:
        return max(0, _int(list_data.get(key), 0))
    return max(0, _int(raw, 0))


def _modifier_price_cents(mod: dict[str, Any], location_id: str) -> int:
    data = mod.get("modifier_data") if isinstance(mod.get("modifier_data"), dict) else {}
    for ov in data.get("location_overrides") or []:
        if not isinstance(ov, dict) or ov.get("location_id") != location_id:
            continue
        money = ov.get("price_money") if isinstance(ov.get("price_money"), dict) else None
        if money and money.get("amount") is not None:
            return _int(money.get("amount"), 0)
    money = data.get("price_money") if isinstance(data.get("price_money"), dict) else None
    if money and money.get("amount") is not None:
        return _int(money.get("amount"), 0)
    return 0


def _override_for(info: dict[str, Any], modifier_id: str) -> dict[str, Any]:
    for ov in info.get("modifier_overrides") or []:
        if isinstance(ov, dict) and str(ov.get("modifier_id") or "") == modifier_id:
            return ov
    return {}


def _image_url(item: dict[str, Any], related: dict[str, dict[str, Any]]) -> str | None:
    data = _item_data(item)
    image_ids = list(data.get("image_ids") or [])
    if data.get("image_id"):
        image_ids.insert(0, data["image_id"])
    for image_id in image_ids:
        obj = related.get(str(image_id))
        if not isinstance(obj, dict):
            continue
        image = obj.get("image_data") if isinstance(obj.get("image_data"), dict) else {}
        url = str(image.get("url") or "").strip()
        if url.startswith("https://"):
            return url
    return None


def _collect_modifiers(
    list_obj: dict[str, Any],
    related: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    data = list_obj.get("modifier_list_data") if isinstance(list_obj.get("modifier_list_data"), dict) else {}
    out: list[dict[str, Any]] = []
    for entry in data.get("modifiers") or []:
        mod: dict[str, Any] | None = None
        if isinstance(entry, dict) and (entry.get("type") == "MODIFIER" or entry.get("modifier_data")):
            mod = entry
        elif isinstance(entry, dict) and entry.get("id"):
            found = related.get(str(entry["id"]))
            if isinstance(found, dict):
                mod = found
        elif isinstance(entry, str):
            found = related.get(entry)
            if isinstance(found, dict):
                mod = found
        if not isinstance(mod, dict):
            continue
        if mod.get("is_deleted"):
            continue
        out.append(mod)
    out.sort(key=lambda m: _int((m.get("modifier_data") or {}).get("ordinal"), 0))
    return out


def map_modifier_groups(
    item: dict[str, Any],
    related: dict[str, dict[str, Any]],
    location_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = _item_data(item)
    groups: list[dict[str, Any]] = []
    defaults: dict[str, Any] = {}
    for info in data.get("modifier_list_info") or []:
        if not isinstance(info, dict):
            continue
        if info.get("enabled") is False:
            continue
        list_id = str(info.get("modifier_list_id") or "").strip()
        if not list_id:
            continue
        list_obj = related.get(list_id)
        if not isinstance(list_obj, dict):
            continue
        if list_obj.get("is_deleted"):
            continue
        list_data = list_obj.get("modifier_list_data") if isinstance(list_obj.get("modifier_list_data"), dict) else {}
        modifier_type = str(list_data.get("modifier_type") or list_data.get("type") or "LIST").upper()
        if modifier_type in ("TEXT", "CATALOG_MODIFIER_LIST_TEXT"):
            continue
        options: list[dict[str, Any]] = []
        default_ids: list[str] = []
        for mod in _collect_modifiers(list_obj, related):
            mid = str(mod.get("id") or "").strip()
            mdata = mod.get("modifier_data") if isinstance(mod.get("modifier_data"), dict) else {}
            label = str(mdata.get("name") or "").strip()
            if not mid or not label:
                continue
            override = _override_for(info, mid)
            if override.get("sold_out") or mdata.get("sold_out"):
                continue
            on_default = override.get("on_by_default")
            if on_default is None:
                on_default = mdata.get("on_by_default")
            if on_default:
                default_ids.append(mid)
            options.append(
                {
                    "id": mid,
                    "label": label,
                    "price_cents": _modifier_price_cents(mod, location_id),
                    "catalog_object_id": mid,
                }
            )
        if not options:
            continue
        min_selected = _effective_bound(info, list_data, "min_selected_modifiers")
        max_selected = _effective_bound(info, list_data, "max_selected_modifiers")
        selection = str(list_data.get("selection_type") or "").upper()
        if max_selected == 1 or (selection == "SINGLE" and max_selected <= 1):
            gtype = "single"
            if max_selected == 0 and selection == "SINGLE":
                gtype = "single"
                max_selected = 1
        else:
            gtype = "multi"
        label = str(list_data.get("name") or "Options").strip() or "Options"
        groups.append(
            {
                "id": list_id,
                "label": label,
                "type": gtype,
                "min_selected": min_selected,
                "max_selected": max_selected,
                "required": min_selected >= 1,
                "options": options,
            }
        )
        if gtype == "single":
            if default_ids:
                defaults[list_id] = default_ids[0]
            elif min_selected >= 1:
                defaults[list_id] = options[0]["id"]
            else:
                defaults[list_id] = ""
        else:
            defaults[list_id] = default_ids
    return groups, defaults


def map_catalog_items(
    items: list[dict[str, Any]],
    related: dict[str, dict[str, Any]],
    location_id: str,
) -> list[dict[str, Any]]:
    drinks: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("is_deleted"):
            continue
        if str(item.get("type") or "ITEM").upper() not in ("ITEM", ""):
            continue
        if not present_at_location(item, location_id):
            continue
        data = _item_data(item)
        product = str(data.get("product_type") or "REGULAR").upper()
        if product and product not in ("REGULAR", "RETAIL_ITEM", "BEVERAGE"):
            continue
        if data.get("is_archived"):
            continue
        if not _item_is_drink(item):
            continue
        item_name = str(data.get("name") or "").strip()
        if not item_name:
            continue
        description = str(
            data.get("description_plaintext") or data.get("description") or ""
        ).strip()
        cat_ids, cat_names = categories_from_line_item(
            {"item_data": data, "category_id": data.get("category_id"), "categories": data.get("categories")}
        )
        if data.get("category_id"):
            cat_ids.append(str(data["category_id"]).strip())
        groups, defaults = map_modifier_groups(item, related, location_id)
        photo = photo_for_name(item_name, _image_url(item, related))
        variations = [v for v in (data.get("variations") or []) if isinstance(v, dict)]
        variations.sort(key=lambda v: _int((v.get("item_variation_data") or {}).get("ordinal"), 0))
        if not variations:
            continue
        for var in variations:
            if var.get("is_deleted"):
                continue
            if not present_at_location(var, location_id):
                continue
            if _sold_out(var, location_id):
                continue
            price = variation_price_cents(var, location_id)
            if price is None:
                continue
            vid = str(var.get("id") or "").strip()
            if not vid:
                continue
            vdata = var.get("item_variation_data") if isinstance(var.get("item_variation_data"), dict) else {}
            vname = str(vdata.get("name") or "").strip()
            drinks.append(
                {
                    "id": vid,
                    "item_id": str(item.get("id") or ""),
                    "catalog_object_id": vid,
                    "name": _display_name(item_name, vname),
                    "square_name": item_name,
                    "variation_name": vname,
                    "description": description,
                    "category": ui_category(item_name, description),
                    "category_ids": [c for c in cat_ids if c],
                    "category_names": [c for c in cat_names if c],
                    "price_cents": price,
                    "photo": photo,
                    "defaults": defaults,
                    "groups": groups,
                }
            )
    drinks.sort(key=lambda d: (d.get("category") or "", str(d.get("name") or "").casefold()))
    return drinks


def _related_index(*groups: list[Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for group in groups:
        for obj in group or []:
            if isinstance(obj, dict) and obj.get("id"):
                out[str(obj["id"])] = obj
    return out


def search_drink_items(
    http: httpx.Client,
    *,
    token: str,
    location_id: str,
    api_base: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = ""
    while True:
        payload: dict[str, Any] = {
            "category_ids": sorted(DRINK_CATEGORY_IDS),
            "enabled_location_ids": [location_id],
            "archived_state": "ARCHIVED_STATE_NOT_ARCHIVED",
            "limit": 100,
        }
        if cursor:
            payload["cursor"] = cursor
        response = http.post(
            f"{api_base}/v2/catalog/search-catalog-items",
            headers=square_headers(token),
            json=payload,
        )
        data = _json_body(response)
        _raise_if_bad(response, data)
        batch = data.get("items") or []
        if isinstance(batch, list):
            items.extend([row for row in batch if isinstance(row, dict)])
        cursor = str(data.get("cursor") or "").strip()
        if not cursor:
            break
    return items


def list_catalog_items(
    http: httpx.Client,
    *,
    token: str,
    api_base: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = ""
    while True:
        params: dict[str, str] = {"types": "ITEM"}
        if cursor:
            params["cursor"] = cursor
        response = http.get(
            f"{api_base}/v2/catalog/list",
            headers=square_headers(token),
            params=params,
        )
        data = _json_body(response)
        _raise_if_bad(response, data)
        batch = data.get("objects") or []
        if isinstance(batch, list):
            items.extend([row for row in batch if isinstance(row, dict)])
        cursor = str(data.get("cursor") or "").strip()
        if not cursor:
            break
    return items


def batch_retrieve(
    http: httpx.Client,
    *,
    token: str,
    api_base: str,
    object_ids: list[str],
) -> dict[str, dict[str, Any]]:
    related: dict[str, dict[str, Any]] = {}
    ids = []
    seen: set[str] = set()
    for oid in object_ids:
        key = str(oid or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ids.append(key)
    for offset in range(0, len(ids), 1000):
        chunk = ids[offset : offset + 1000]
        if not chunk:
            continue
        response = http.post(
            f"{api_base}/v2/catalog/batch-retrieve",
            headers=square_headers(token),
            json={"object_ids": chunk, "include_related_objects": True},
        )
        data = _json_body(response)
        _raise_if_bad(response, data)
        related.update(_related_index(data.get("objects"), data.get("related_objects")))
    return related


def _needed_related_ids(items: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for item in items:
        data = _item_data(item)
        for info in data.get("modifier_list_info") or []:
            if isinstance(info, dict) and info.get("modifier_list_id"):
                ids.append(str(info["modifier_list_id"]))
        for image_id in data.get("image_ids") or []:
            ids.append(str(image_id))
        if data.get("image_id"):
            ids.append(str(data["image_id"]))
        for var in data.get("variations") or []:
            if isinstance(var, dict) and var.get("id"):
                ids.append(str(var["id"]))
        if item.get("id"):
            ids.append(str(item["id"]))
    return ids


def fetch_irondale_drinks(
    *,
    token: str,
    location_id: str,
    api_base: str,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    if not token:
        raise CatalogError("Square is not configured.", 503)
    if not location_id:
        raise CatalogError("Irondale location is not configured.", 503)
    own = client is None
    http = client or httpx.Client(timeout=20.0)
    try:
        items = search_drink_items(http, token=token, location_id=location_id, api_base=api_base)
        if not items:
            items = [
                row
                for row in list_catalog_items(http, token=token, api_base=api_base)
                if _item_is_drink(row) and present_at_location(row, location_id)
            ]
        related = batch_retrieve(
            http,
            token=token,
            api_base=api_base,
            object_ids=_needed_related_ids(items),
        )
        # Nested modifiers on retrieved lists should already be in related; pull
        # any modifier ids that were only referenced by id.
        extra_mod_ids: list[str] = []
        for obj in related.values():
            extra_mod_ids.extend(
                str(m.get("id") or m)
                for m in ((obj.get("modifier_list_data") or {}).get("modifiers") or [])
                if (isinstance(m, dict) and m.get("id") and str(m["id"]) not in related)
                or isinstance(m, str)
            )
        if extra_mod_ids:
            related.update(
                batch_retrieve(http, token=token, api_base=api_base, object_ids=extra_mod_ids)
            )
        return map_catalog_items(items, related, location_id)
    finally:
        if own:
            http.close()


def list_irondale_drinks(
    *,
    token: str,
    location_id: str,
    api_base: str,
    client: httpx.Client | None = None,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    ttl = cache_ttl_seconds()
    if client is None and not refresh and ttl > 0:
        with _cache_lock:
            cached = _cache.get("drinks")
            if (
                isinstance(cached, list)
                and _cache.get("location_id") == location_id
                and time.time() < float(_cache.get("expires") or 0)
            ):
                return cached
    drinks = fetch_irondale_drinks(
        token=token,
        location_id=location_id,
        api_base=api_base,
        client=client,
    )
    if client is None and ttl > 0:
        with _cache_lock:
            _cache["drinks"] = drinks
            _cache["location_id"] = location_id
            _cache["expires"] = time.time() + ttl
    return drinks
