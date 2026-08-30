"""Weekend scorecard @Service: this Saturday vs last, drink mix, top 10, CSV.

Routes in app/main.py call this. Numbers come from weekend_days / weekend_items
(aggregated counts only — no Square live pull, no order dumps).
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WeekendDay, WeekendItem

DRINK_MIX_ORDER = [
    "Vietnamese Coffee",
    "Fruit Tea",
    "Matcha Latte",
    "Biscoff Coffee",
    "Milk Tea",
]

VIET_NAME = "Vietnamese Coffee"
MANGO_NAME = "Mango Entrement"


def _zeros() -> dict[str, int]:
    return {
        "tickets": 0,
        "cents": 0,
        "pastry_qty": 0,
        "pastry_cents": 0,
        "drink_qty": 0,
        "drink_cents": 0,
        "boba_modifiers": 0,
    }


def _as_totals(day: WeekendDay | None) -> dict[str, int]:
    if day is None:
        return _zeros()
    return {
        "tickets": int(day.tickets or 0),
        "cents": int(day.cents or 0),
        "pastry_qty": int(day.pastry_qty or 0),
        "pastry_cents": int(day.pastry_cents or 0),
        "drink_qty": int(day.drink_qty or 0),
        "drink_cents": int(day.drink_cents or 0),
        "boba_modifiers": int(day.boba_modifiers or 0),
    }


def _label(d: date | None) -> str:
    if d is None:
        return "—"
    return f"{d.strftime('%A')}, {d.strftime('%b')} {d.day}, {d.year}"


def _short(d: date | None) -> str:
    if d is None:
        return ""
    return d.isoformat()


def _pct(this_n: int, last_n: int) -> str:
    if last_n == 0:
        return "n/a"
    return f"{(this_n - last_n) * 100.0 / last_n:+.1f}%"


def _dollars_plain(cents: int) -> str:
    return f"{cents / 100:.2f}"


def parse_this(raw: str, latest: date | None) -> date | None:
    text = (raw or "").strip()
    if text:
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    return latest


def latest_saturday(db: Session) -> date | None:
    rows = db.scalars(select(WeekendDay.sold_on).order_by(WeekendDay.sold_on.desc())).all()
    for sold_on in rows:
        if sold_on.weekday() == 5:
            return sold_on
    return rows[0] if rows else None


def get_day(db: Session, sold_on: date | None) -> WeekendDay | None:
    if sold_on is None:
        return None
    return db.scalar(select(WeekendDay).where(WeekendDay.sold_on == sold_on))


def _items_by_kind(db: Session, day: WeekendDay | None, kind: str) -> list[WeekendItem]:
    if day is None:
        return []
    stmt = (
        select(WeekendItem)
        .where(WeekendItem.day_id == day.id, WeekendItem.kind == kind)
        .order_by(WeekendItem.rank.asc(), WeekendItem.qty.desc(), WeekendItem.name.asc())
    )
    return list(db.scalars(stmt).all())


def _qty_map(rows: list[WeekendItem]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[row.name] = int(row.qty or 0)
    return out


def _named_qty(mapping: dict[str, int], name: str) -> int:
    if name in mapping:
        return mapping[name]
    needle = name.casefold()
    for key, qty in mapping.items():
        if key.casefold() == needle:
            return qty
    return 0


def pair_sentence(
    this_viet: int,
    this_mango: int,
    last_viet: int,
    last_mango: int,
    this_on: date | None,
) -> str:
    if this_on is None or (this_viet == 0 and this_mango == 0):
        return (
            "Not enough Saturday sales yet to tell whether a mango entremet + "
            "Vietnamese Coffee pair would ride Vietnamese Coffee or mango."
        )
    last_mango_bit = (
        f"mango entremet was {last_mango}"
        if last_mango
        else "mango entremet did not make the top 10"
    )
    if this_viet > this_mango:
        rider = "Vietnamese Coffee"
    elif this_mango > this_viet:
        rider = "mango"
    else:
        return (
            f"A mango entremet + Vietnamese Coffee pair would be a toss-up this Saturday "
            f"({this_viet} Vietnamese Coffee and {this_mango} mango entremets). "
            f"Last Saturday Vietnamese Coffee was {last_viet} and {last_mango_bit}."
        )
    return (
        f"A mango entremet + Vietnamese Coffee pair would be riding {rider} "
        f"({this_viet} Vietnamese Coffee vs {this_mango} mango entremets this Saturday). "
        f"Last Saturday Vietnamese Coffee was {last_viet} and {last_mango_bit}."
    )


def scorecard(db: Session, this_raw: str = "") -> dict[str, Any]:
    latest = latest_saturday(db)
    this_on = parse_this(this_raw, latest)
    last_on = this_on - timedelta(days=7) if this_on else None
    this_day = get_day(db, this_on)
    last_day = get_day(db, last_on)
    this_tot = _as_totals(this_day)
    last_tot = _as_totals(last_day)

    this_drinks = _qty_map(_items_by_kind(db, this_day, "drink"))
    last_drinks = _qty_map(_items_by_kind(db, last_day, "drink"))
    drink_names = list(DRINK_MIX_ORDER)
    extras = sorted(
        {*(this_drinks.keys()), *(last_drinks.keys())} - set(DRINK_MIX_ORDER),
        key=str.casefold,
    )
    drink_mix = []
    for name in drink_names + extras:
        this_n = _named_qty(this_drinks, name)
        last_n = _named_qty(last_drinks, name)
        drink_mix.append(
            {
                "name": name,
                "last": last_n,
                "this": this_n,
                "delta": this_n - last_n,
            }
        )

    this_top_rows = _items_by_kind(db, this_day, "top")
    last_top_map = _qty_map(_items_by_kind(db, last_day, "top"))
    top = []
    for row in this_top_rows[:10]:
        this_n = int(row.qty or 0)
        last_n = _named_qty(last_top_map, row.name)
        top.append(
            {
                "rank": int(row.rank or 0),
                "name": row.name,
                "last": last_n,
                "this": this_n,
                "delta": this_n - last_n,
            }
        )

    this_viet = _named_qty(this_drinks, VIET_NAME)
    last_viet = _named_qty(last_drinks, VIET_NAME)
    this_mango = _named_qty(_qty_map(this_top_rows), MANGO_NAME)
    last_mango = _named_qty(last_top_map, MANGO_NAME)

    csv_qs = urlencode({"this": this_on.isoformat()}) if this_on else ""
    empty = this_day is None and last_day is None
    return {
        "empty": empty,
        "this_on": this_on,
        "last_on": last_on,
        "this_label": _label(this_on),
        "last_label": _label(last_on),
        "this_iso": _short(this_on),
        "last_iso": _short(last_on),
        "this": this_tot,
        "last": last_tot,
        "tickets_delta": this_tot["tickets"] - last_tot["tickets"],
        "tickets_pct": _pct(this_tot["tickets"], last_tot["tickets"]),
        "cents_delta": this_tot["cents"] - last_tot["cents"],
        "cents_pct": _pct(this_tot["cents"], last_tot["cents"]),
        "pastry_qty_delta": this_tot["pastry_qty"] - last_tot["pastry_qty"],
        "pastry_cents_delta": this_tot["pastry_cents"] - last_tot["pastry_cents"],
        "drink_qty_delta": this_tot["drink_qty"] - last_tot["drink_qty"],
        "drink_cents_delta": this_tot["drink_cents"] - last_tot["drink_cents"],
        "boba_delta": this_tot["boba_modifiers"] - last_tot["boba_modifiers"],
        "drink_mix": drink_mix,
        "top": top,
        "pair_sentence": pair_sentence(this_viet, this_mango, last_viet, last_mango, this_on),
        "csv_qs": csv_qs,
    }


def csv_bytes(card: dict[str, Any]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["metric", "last_sat", "this_sat", "delta"])
    this_tot = card["this"]
    last_tot = card["last"]
    rows: list[tuple[str, Any, Any, Any]] = [
        ("sold_on", card["last_iso"], card["this_iso"], ""),
        ("tickets", last_tot["tickets"], this_tot["tickets"], card["tickets_delta"]),
        ("tickets_pct", "", "", card["tickets_pct"]),
        ("gross_dollars", _dollars_plain(last_tot["cents"]), _dollars_plain(this_tot["cents"]), _dollars_plain(card["cents_delta"])),
        ("gross_pct", "", "", card["cents_pct"]),
        ("pastry_units", last_tot["pastry_qty"], this_tot["pastry_qty"], card["pastry_qty_delta"]),
        ("pastry_dollars", _dollars_plain(last_tot["pastry_cents"]), _dollars_plain(this_tot["pastry_cents"]), _dollars_plain(card["pastry_cents_delta"])),
        ("drink_units", last_tot["drink_qty"], this_tot["drink_qty"], card["drink_qty_delta"]),
        ("drink_dollars", _dollars_plain(last_tot["drink_cents"]), _dollars_plain(this_tot["drink_cents"]), _dollars_plain(card["drink_cents_delta"])),
        ("boba_modifiers", last_tot["boba_modifiers"], this_tot["boba_modifiers"], card["boba_delta"]),
    ]
    for drink in card["drink_mix"]:
        rows.append((f"drink:{drink['name']}", drink["last"], drink["this"], drink["delta"]))
    rows.append(("boba", last_tot["boba_modifiers"], this_tot["boba_modifiers"], card["boba_delta"]))
    for item in card["top"]:
        rows.append((f"top:{item['name']}", item["last"], item["this"], item["delta"]))
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")
