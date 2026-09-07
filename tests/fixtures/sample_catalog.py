"""Square Catalog-shaped fixture for Irondale drinks + full modifier lists.

Not live data. IDs are fake. Drink category id matches app.drinks.DRINK_CATEGORY_IDS.
"""

from __future__ import annotations

from typing import Any

from app.drinks import DRINK_CATEGORY_IDS

DRINK_CAT = next(iter(DRINK_CATEGORY_IDS))
ROLL_CAT = "ROLLCATEGORYNOTADRINK01"
IRONDALE = "L4CK6YWGT5XQX"
OTHER_LOC = "L999OTHERSHOP0001"

ML_MILK = "MLMILKIRONDALE000000001"
ML_SWEET = "MLSWEETIRONDALE00000001"
ML_SAUCE = "MLSAUCEIRONDALE00000001"
ML_FLAVOR = "MLFLAVORIRONDALE0000001"
ML_MATCHA = "MLMATCHAIRONDALE0000001"
ML_BOBA = "MLBOBAIRONDALE000000001"
ML_ADDONS = "MLADDONSIRONDALE0000001"

MOD = {
    "whole": "MODMILKWHOLE00000000001",
    "oat": "MODMILKOAT0000000000001",
    "condensed": "MODMILKCONDENSED0000001",
    "almond": "MODMILKALMOND0000000001",
    "coconut": "MODMILKCOCONUT000000001",
    "sweet-less": "MODSWEETLESS00000000001",
    "sweet-normal": "MODSWEETNORMAL000000001",
    "sweet-extra": "MODSWEETEXTRA0000000001",
    "sweet-xx": "MODSWEETXX0000000000001",
    "sauce-none": "MODSAUCENONE00000000001",
    "sauce-caramel": "MODSAUCECARAMEL00000001",
    "sauce-mocha": "MODSAUCEMOCHA0000000001",
    "sauce-white": "MODSAUCEWHITE0000000001",
    "flav-classic": "MODFLAVCLASSIC000000001",
    "flav-taro": "MODFLAVTARO000000000001",
    "flav-brown": "MODFLAVBROWNSUGAR000001",
    "flav-thai": "MODFLAVTHAI000000000001",
    "flav-jasmine": "MODFLAVJASMINE000000001",
    "matcha-reg": "MODMATCHAREG00000000001",
    "matcha-cer": "MODMATCHACER00000000001",
    "matcha-extra": "MODMATCHAEXTRA000000001",
    "boba-none": "MODBOBANONE000000000001",
    "boba": "MODBOBAREGULAR000000001",
    "boba-straw": "MODBOBASTRAWBERRY000001",
    "boba-jelly": "MODBOBAJELLY00000000001",
    "shot": "MODADDONSHOT00000000001",
}

VAR = {
    "viet": "VARVIETNAMESECOFFEE00001",
    "hot": "VARHOTCOFFEE000000000001",
    "biscoff": "VARBISCOFFCOFFEE0000001",
    "matcha": "VARMATCHALATTE000000001",
    "milk-tea": "VARMILKTEA0000000000001",
    "fruit": "VARFRUITTEA000000000001",
    "lemonade": "VARLEMONADE000000000001",
    "soldout": "VARSOLDOUTDRINK00000001",
    "trussville": "VARTRUSSVILLEONLY000001",
    "roll": "VARBISCOFFROLL000000001",
}

ITEM = {
    "viet": "ITEMVIETNAMESECOFFEE0001",
    "hot": "ITEMHOTCOFFEE00000000001",
    "biscoff": "ITEMBISCOFFCOFFEE000001",
    "matcha": "ITEMMATCHALATTE00000001",
    "milk-tea": "ITEMMILKTEA000000000001",
    "fruit": "ITEMFRUITTEA00000000001",
    "lemonade": "ITEMLEMONADE00000000001",
    "soldout": "ITEMSOLDOUTDRINK0000001",
    "trussville": "ITEMTRUSSVILLEONLY00001",
    "roll": "ITEMBISCOFFROLL00000001",
}

IMG_MATCHA = "IMGMATCHALATTE0000000001"


def _mod(oid: str, name: str, amount: int = 0, *, on: bool = False, ordinal: int = 0) -> dict[str, Any]:
    return {
        "type": "MODIFIER",
        "id": oid,
        "is_deleted": False,
        "present_at_all_locations": True,
        "modifier_data": {
            "name": name,
            "ordinal": ordinal,
            "on_by_default": on,
            "price_money": {"amount": amount, "currency": "USD"},
        },
    }


def _mod_list(lid: str, name: str, selection: str, modifiers: list[dict[str, Any]], *, min_sel: int = 0, max_sel: int = 1) -> dict[str, Any]:
    return {
        "type": "MODIFIER_LIST",
        "id": lid,
        "is_deleted": False,
        "present_at_all_locations": True,
        "modifier_list_data": {
            "name": name,
            "selection_type": selection,
            "modifier_type": "LIST",
            "min_selected_modifiers": min_sel,
            "max_selected_modifiers": max_sel,
            "modifiers": modifiers,
        },
    }


def _info(list_id: str, *, min_sel: int = -1, max_sel: int = -1, enabled: bool = True, overrides: list | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "modifier_list_id": list_id,
        "min_selected_modifiers": min_sel,
        "max_selected_modifiers": max_sel,
        "enabled": enabled,
        "modifier_overrides": overrides or [],
    }
    return row


def _variation(vid: str, item_id: str, name: str, amount: int, *, sold_out: bool = False, locations: list[str] | None = None) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "type": "ITEM_VARIATION",
        "id": vid,
        "is_deleted": False,
        "item_variation_data": {
            "item_id": item_id,
            "name": name,
            "ordinal": 0,
            "pricing_type": "FIXED_PRICING",
            "price_money": {"amount": amount, "currency": "USD"},
            "sold_out": sold_out,
        },
    }
    if locations is None:
        obj["present_at_all_locations"] = True
    else:
        obj["present_at_all_locations"] = False
        obj["present_at_location_ids"] = locations
    return obj


def _item(
    iid: str,
    name: str,
    *,
    description: str,
    category_id: str,
    variations: list[dict[str, Any]],
    modifier_lists: list[dict[str, Any]],
    image_ids: list[str] | None = None,
    locations: list[str] | None = None,
) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "type": "ITEM",
        "id": iid,
        "is_deleted": False,
        "item_data": {
            "name": name,
            "description": description,
            "product_type": "REGULAR",
            "category_id": category_id,
            "categories": [{"id": category_id, "ordinal": 0}],
            "variations": variations,
            "modifier_list_info": modifier_lists,
        },
    }
    if image_ids:
        obj["item_data"]["image_ids"] = image_ids
    if locations is None:
        obj["present_at_all_locations"] = True
    else:
        obj["present_at_all_locations"] = False
        obj["present_at_location_ids"] = locations
    return obj


MILK_LIST = _mod_list(
    ML_MILK,
    "Milk",
    "SINGLE",
    [
        _mod(MOD["whole"], "Whole", on=True, ordinal=0),
        _mod(MOD["oat"], "Oat", 100, ordinal=1),
        _mod(MOD["condensed"], "Condensed", ordinal=2),
        _mod(MOD["almond"], "Almond", 100, ordinal=3),
        _mod(MOD["coconut"], "Coconut", 100, ordinal=4),
    ],
    min_sel=1,
    max_sel=1,
)
SWEET_LIST = _mod_list(
    ML_SWEET,
    "Sweet",
    "SINGLE",
    [
        _mod(MOD["sweet-less"], "Less", ordinal=0),
        _mod(MOD["sweet-normal"], "Normal", on=True, ordinal=1),
        _mod(MOD["sweet-extra"], "Extra", ordinal=2),
        _mod(MOD["sweet-xx"], "Extra extra", ordinal=3),
    ],
    min_sel=1,
    max_sel=1,
)
SAUCE_LIST = _mod_list(
    ML_SAUCE,
    "Sauce",
    "SINGLE",
    [
        _mod(MOD["sauce-none"], "No sauce", on=True, ordinal=0),
        _mod(MOD["sauce-caramel"], "Caramel", 50, ordinal=1),
        _mod(MOD["sauce-mocha"], "Mocha", 50, ordinal=2),
        _mod(MOD["sauce-white"], "White chocolate", 50, ordinal=3),
    ],
    min_sel=0,
    max_sel=1,
)
FLAVOR_LIST = _mod_list(
    ML_FLAVOR,
    "Flavor",
    "SINGLE",
    [
        _mod(MOD["flav-classic"], "Classic", on=True, ordinal=0),
        _mod(MOD["flav-taro"], "Taro", ordinal=1),
        _mod(MOD["flav-brown"], "Brown sugar", ordinal=2),
        _mod(MOD["flav-thai"], "Thai", ordinal=3),
        _mod(MOD["flav-jasmine"], "Jasmine", ordinal=4),
    ],
    min_sel=1,
    max_sel=1,
)
MATCHA_LIST = _mod_list(
    ML_MATCHA,
    "Matcha option",
    "SINGLE",
    [
        _mod(MOD["matcha-reg"], "Regular", on=True, ordinal=0),
        _mod(MOD["matcha-cer"], "Ceremonial", 75, ordinal=1),
        _mod(MOD["matcha-extra"], "Extra scoop", 100, ordinal=2),
    ],
    min_sel=1,
    max_sel=1,
)
BOBA_LIST = _mod_list(
    ML_BOBA,
    "Boba",
    "SINGLE",
    [
        _mod(MOD["boba-none"], "No boba", ordinal=0),
        _mod(MOD["boba"], "Boba", 75, on=True, ordinal=1),
        _mod(MOD["boba-straw"], "Strawberry bursting", 75, ordinal=2),
        _mod(MOD["boba-jelly"], "Brown sugar jelly", 75, ordinal=3),
    ],
    min_sel=0,
    max_sel=1,
)
ADDONS_LIST = _mod_list(
    ML_ADDONS,
    "Add-ons",
    "MULTIPLE",
    [_mod(MOD["shot"], "Extra shot", 100, ordinal=0)],
    min_sel=0,
    max_sel=0,
)

COFFEE_MODS = [
    _info(ML_MILK, min_sel=1, max_sel=1, overrides=[{"modifier_id": MOD["condensed"], "on_by_default": True}]),
    _info(ML_SWEET, min_sel=1, max_sel=1),
    _info(ML_SAUCE, min_sel=0, max_sel=1),
    _info(ML_ADDONS, min_sel=0, max_sel=0),
]
TEA_MODS = [
    _info(ML_FLAVOR, min_sel=1, max_sel=1),
    _info(ML_MILK, min_sel=1, max_sel=1),
    _info(ML_SWEET, min_sel=1, max_sel=1),
    _info(ML_BOBA, min_sel=0, max_sel=1),
]

ITEMS = [
    _item(
        ITEM["viet"],
        "Vietnamese Coffee",
        description="espresso, condensed milk, ice",
        category_id=DRINK_CAT,
        variations=[_variation(VAR["viet"], ITEM["viet"], "Regular", 550)],
        modifier_lists=COFFEE_MODS,
    ),
    _item(
        ITEM["hot"],
        "Hot Coffee",
        description="drip, to-go cup",
        category_id=DRINK_CAT,
        variations=[_variation(VAR["hot"], ITEM["hot"], "Regular", 400)],
        modifier_lists=[
            _info(ML_MILK, min_sel=1, max_sel=1, overrides=[{"modifier_id": MOD["whole"], "on_by_default": True}]),
            _info(ML_SWEET, min_sel=1, max_sel=1),
            _info(ML_ADDONS, min_sel=0, max_sel=0),
        ],
    ),
    _item(
        ITEM["biscoff"],
        "Biscoff Coffee",
        description="iced, cookie on top",
        category_id=DRINK_CAT,
        variations=[_variation(VAR["biscoff"], ITEM["biscoff"], "Regular", 650)],
        modifier_lists=COFFEE_MODS,
    ),
    _item(
        ITEM["matcha"],
        "Matcha Latte",
        description="iced matcha, milk",
        category_id=DRINK_CAT,
        variations=[_variation(VAR["matcha"], ITEM["matcha"], "Regular", 650)],
        modifier_lists=[
            _info(ML_MILK, min_sel=1, max_sel=1),
            _info(ML_SWEET, min_sel=1, max_sel=1),
            _info(ML_MATCHA, min_sel=1, max_sel=1),
            _info(ML_ADDONS, min_sel=0, max_sel=0),
        ],
        image_ids=[IMG_MATCHA],
    ),
    _item(
        ITEM["milk-tea"],
        "Milk Tea",
        description="boba at the bottom",
        category_id=DRINK_CAT,
        variations=[_variation(VAR["milk-tea"], ITEM["milk-tea"], "Regular", 600)],
        modifier_lists=TEA_MODS,
    ),
    _item(
        ITEM["fruit"],
        "Fruit Tea",
        description="iced tea, fruit pearls",
        category_id=DRINK_CAT,
        variations=[_variation(VAR["fruit"], ITEM["fruit"], "Regular", 600)],
        modifier_lists=[
            _info(ML_FLAVOR, min_sel=1, max_sel=1),
            _info(ML_SWEET, min_sel=1, max_sel=1),
            _info(ML_BOBA, min_sel=0, max_sel=1),
        ],
    ),
    _item(
        ITEM["lemonade"],
        "Lemonade",
        description="fresh lemonade",
        category_id=DRINK_CAT,
        variations=[_variation(VAR["lemonade"], ITEM["lemonade"], "Regular", 450)],
        modifier_lists=[],
    ),
    _item(
        ITEM["soldout"],
        "Sold Out Latte",
        description="should not appear",
        category_id=DRINK_CAT,
        variations=[_variation(VAR["soldout"], ITEM["soldout"], "Regular", 600, sold_out=True)],
        modifier_lists=COFFEE_MODS,
    ),
    _item(
        ITEM["trussville"],
        "Trussville Cold Brew",
        description="other shop only",
        category_id=DRINK_CAT,
        variations=[_variation(VAR["trussville"], ITEM["trussville"], "Regular", 500, locations=[OTHER_LOC])],
        modifier_lists=COFFEE_MODS,
        locations=[OTHER_LOC],
    ),
    _item(
        ITEM["roll"],
        "Biscoff Roll",
        description="pastry, not a drink",
        category_id=ROLL_CAT,
        variations=[_variation(VAR["roll"], ITEM["roll"], "Regular", 700)],
        modifier_lists=[],
    ),
]

RELATED = [
    MILK_LIST,
    SWEET_LIST,
    SAUCE_LIST,
    FLAVOR_LIST,
    MATCHA_LIST,
    BOBA_LIST,
    ADDONS_LIST,
    {
        "type": "IMAGE",
        "id": IMG_MATCHA,
        "image_data": {
            "url": "https://items-images-production.s3.us-west-2.amazonaws.com/files/fake-matcha.jpg",
            "name": "Matcha Latte",
        },
    },
]


def search_response() -> dict[str, Any]:
    return {"items": ITEMS}


def batch_response() -> dict[str, Any]:
    return {"objects": RELATED, "related_objects": []}


class CatalogFakeClient:
    """httpx-like client: Catalog search/batch plus optional payment-link POST."""

    def __init__(self, *, payment_link: dict[str, Any] | None = None):
        self.payment_link = payment_link
        self.calls: list[tuple[str, dict | None]] = []

    def post(self, url, headers=None, json=None):
        self.calls.append((url, json))
        if "search-catalog-items" in url:
            return _Resp(200, search_response())
        if "batch-retrieve" in url:
            return _Resp(200, batch_response())
        if "online-checkout/payment-links" in url:
            if not self.payment_link:
                return _Resp(500, {"errors": [{"detail": "no payment link stub"}]})
            return _Resp(200, {"payment_link": self.payment_link})
        return _Resp(404, {"errors": [{"detail": f"unexpected POST {url}"}]})

    def get(self, url, headers=None, params=None):
        self.calls.append((url, params))
        if "/v2/catalog/list" in url:
            return _Resp(200, {"objects": ITEMS})
        return _Resp(404, {})

    def close(self):
        pass


class _Resp:
    def __init__(self, status_code: int, body: dict[str, Any]):
        self.status_code = status_code
        self._body = body
        self.content = b"{}"

    def json(self):
        return self._body
