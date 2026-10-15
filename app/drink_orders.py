"""Drinks-only test counter.

The live kitchen board (/board) is one drink ticket at a time, newest first.
This module groups the same demo drinks into orders for /test/drinks.

The test board shows the three oldest open orders. Clearing a drink removes
that line only. The order is deleted when its last drink is gone, and the
next oldest order takes the open slot.

Rows live in test_drink_orders / test_drink_lines. Nothing here reads or
writes drink_tickets.
"""

from __future__ import annotations

import json

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import TestDrinkLine, TestDrinkOrder

BOARD_SLOTS = 3
MAX_QTY = 9


class OrderRejected(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def menu_from_demo(demo_drinks: list) -> list[dict]:
    """One menu row per kitchen demo drink, same name and modifiers."""
    menu: list[dict] = []
    for index, entry in enumerate(demo_drinks):
        name, modifiers, _ticket_name = entry
        menu.append(
            {
                "key": str(index),
                "drink_name": name,
                "modifiers": [
                    {
                        "group": str(mod.get("group") or ""),
                        "value": str(mod.get("value") or ""),
                    }
                    for mod in modifiers
                    if mod.get("value")
                ],
                "slug": str(name).lower().replace(" ", "-"),
            }
        )
    return menu


def lines_from_quantities(menu: list[dict], quantities: dict[str, str]) -> list[dict]:
    """Build order lines from menu keys only. Off-menu names are ignored."""
    lines: list[dict] = []
    for item in menu:
        raw = quantities.get(item["key"], "0")
        text = str(raw).strip() if raw is not None else "0"
        if text == "":
            text = "0"
        try:
            qty = int(text)
        except (TypeError, ValueError) as exc:
            raise OrderRejected(
                f"Quantity for {item['drink_name']} has to be a whole number."
            ) from exc
        if qty < 0 or qty > MAX_QTY:
            raise OrderRejected(
                f"Quantity for {item['drink_name']} has to be 0–{MAX_QTY}."
            )
        if qty == 0:
            continue
        lines.append(
            {
                "drink_name": item["drink_name"],
                "modifiers": item["modifiers"],
                "qty": qty,
            }
        )
    if not lines:
        raise OrderRejected("Add at least one drink from the menu.")
    return lines


def normalize_ticket_name(raw: str) -> str:
    return " ".join(str(raw or "").split())[:80]


def list_open_orders(db: Session) -> list[TestDrinkOrder]:
    return list(
        db.scalars(
            select(TestDrinkOrder)
            .options(selectinload(TestDrinkOrder.lines))
            .order_by(TestDrinkOrder.ordered_at.asc(), TestDrinkOrder.id.asc())
        ).all()
    )


def create_test_order(
    db: Session,
    *,
    ordered_at,
    ticket_name: str,
    lines: list[dict],
) -> TestDrinkOrder:
    order = TestDrinkOrder(ordered_at=ordered_at, ticket_name=ticket_name)
    for line in lines:
        order.lines.append(
            TestDrinkLine(
                drink_name=line["drink_name"],
                modifiers_json=json.dumps(line["modifiers"]),
                qty=int(line["qty"]),
            )
        )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def clear_drink_line(db: Session, line_id: int) -> None:
    """Remove one drink. Drop the order only when nothing is left on it."""
    line = db.get(TestDrinkLine, line_id)
    if line is None:
        return
    order_id = line.order_id
    if line.qty > 1:
        line.qty -= 1
        db.commit()
        return
    db.delete(line)
    db.flush()
    left = db.scalar(
        select(func.count(TestDrinkLine.id)).where(TestDrinkLine.order_id == order_id)
    )
    if not left:
        db.execute(delete(TestDrinkOrder).where(TestDrinkOrder.id == order_id))
    db.commit()


def reset_test_orders(db: Session) -> None:
    db.execute(delete(TestDrinkLine))
    db.execute(delete(TestDrinkOrder))
    db.commit()
