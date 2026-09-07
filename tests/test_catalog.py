"""Map Square Catalog drinks + modifiers; skip pastry and other-shop items."""

from __future__ import annotations

import os
import tempfile

if "BAKERY_DB" not in os.environ:
    _fd, _db = tempfile.mkstemp(suffix=".db")
    os.close(_fd)
    os.environ["BAKERY_DB"] = _db

from app.catalog import CatalogError, clear_menu_cache, list_irondale_drinks, map_catalog_items
from app.drinks import is_drink
from tests.fixtures.sample_catalog import (
    DRINK_CAT,
    IRONDALE,
    ML_MATCHA,
    ML_MILK,
    ML_SAUCE,
    MOD,
    RELATED,
    VAR,
    CatalogFakeClient,
    search_response,
)


def setup_function() -> None:
    clear_menu_cache()


def teardown_function() -> None:
    clear_menu_cache()


def _drinks():
    related = {obj["id"]: obj for obj in RELATED}
    return map_catalog_items(search_response()["items"], related, IRONDALE)


def test_maps_irondale_drinks_and_full_modifier_lists() -> None:
    drinks = _drinks()
    ids = {d["id"] for d in drinks}
    assert VAR["viet"] in ids
    assert VAR["matcha"] in ids
    assert VAR["lemonade"] in ids
    assert VAR["roll"] not in ids
    assert VAR["soldout"] not in ids
    assert VAR["trussville"] not in ids
    names = {d["square_name"] for d in drinks}
    assert "Biscoff Roll" not in names
    assert "Vietnamese Coffee" in names
    viet = next(d for d in drinks if d["id"] == VAR["viet"])
    labels = {g["label"]: [o["label"] for o in g["options"]] for g in viet["groups"]}
    assert labels["Milk"] == ["Whole", "Oat", "Condensed", "Almond", "Coconut"]
    assert "Sauce" in labels
    assert "Caramel" in labels["Sauce"]
    assert "White chocolate" in labels["Sauce"]
    assert labels["Sweet"] == ["Less", "Normal", "Extra", "Extra extra"]
    milk = next(g for g in viet["groups"] if g["id"] == ML_MILK)
    assert milk["type"] == "single"
    assert milk["required"] is True
    assert milk["options"][0]["catalog_object_id"] == MOD["whole"]
    assert viet["catalog_object_id"] == VAR["viet"]
    assert viet["defaults"][ML_MILK] == MOD["condensed"]
    sauce = next(g for g in viet["groups"] if g["id"] == ML_SAUCE)
    assert sauce["required"] is False
    matcha = next(d for d in drinks if d["id"] == VAR["matcha"])
    matcha_opts = next(g for g in matcha["groups"] if g["id"] == ML_MATCHA)
    assert [o["label"] for o in matcha_opts["options"]] == ["Regular", "Ceremonial", "Extra scoop"]
    assert matcha["photo"].startswith("https://")
    lemonade = next(d for d in drinks if d["id"] == VAR["lemonade"])
    assert lemonade["groups"] == []
    assert lemonade["category"] == "tea"
    for drink in drinks:
        assert is_drink(
            drink["square_name"],
            category_ids=drink["category_ids"],
            category_names=drink["category_names"],
        )
        assert DRINK_CAT in drink["category_ids"]


def test_fetch_uses_search_then_batch_retrieve() -> None:
    fake = CatalogFakeClient()
    drinks = list_irondale_drinks(
        token="sandbox-test-token-not-real",
        location_id=IRONDALE,
        api_base="https://connect.squareup.com",
        client=fake,
    )
    urls = [u for u, _ in fake.calls]
    assert any("search-catalog-items" in u for u in urls)
    assert any("batch-retrieve" in u for u in urls)
    assert {d["id"] for d in drinks} >= {VAR["viet"], VAR["matcha"], VAR["lemonade"]}
    for _url, payload in fake.calls:
        assert payload is None or "sandbox-test-token-not-real" not in str(payload)


def test_insufficient_scopes_mentions_items_read() -> None:
    class ScopeClient:
        def post(self, url, headers=None, json=None):
            class R:
                status_code = 403
                content = b"{}"

                def json(self):
                    return {"errors": [{"code": "INSUFFICIENT_SCOPES", "detail": "missing ITEMS_READ"}]}

            return R()

        def close(self):
            pass

    try:
        list_irondale_drinks(
            token="sandbox-test-token-not-real",
            location_id=IRONDALE,
            api_base="https://connect.squareup.com",
            client=ScopeClient(),
        )
        assert False, "expected CatalogError"
    except CatalogError as exc:
        assert "ITEMS_READ" in exc.message


def test_search_empty_falls_back_to_list() -> None:
    class EmptySearch(CatalogFakeClient):
        def post(self, url, headers=None, json=None):
            self.calls.append((url, json))
            if "search-catalog-items" in url:
                return type("R", (), {"status_code": 200, "content": b"{}", "json": lambda self: {"items": []}})()
            return super().post(url, headers=headers, json=json)

    fake = EmptySearch()
    drinks = list_irondale_drinks(
        token="sandbox-test-token-not-real",
        location_id=IRONDALE,
        api_base="https://connect.squareup.com",
        client=fake,
    )
    assert any("/v2/catalog/list" in u for u, _ in fake.calls)
    assert any(d["id"] == VAR["viet"] for d in drinks)
    assert not any(d["square_name"] == "Biscoff Roll" for d in drinks)
