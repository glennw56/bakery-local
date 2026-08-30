"""Seed reports tables and a live 15-minute drink board.

Demo dollars and item names follow real bakery patterns (Sat is the money day,
wholesale inflates Thu/Mon, Peach often OOS). No Square keys live here.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, func, select  # noqa: E402

from app.area_codes import lookup_phone  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.loyalty import compute_status  # noqa: E402
from app.models import (  # noqa: E402
    DrinkModifier,
    DrinkTicket,
    LoyaltyEvent,
    LoyaltyMember,
    MerchItem,
    SalesDaily,
    SalesSummary,
)

# Two recent weeks ending Sat Aug 29, 2026. Sat ~$2.5–3k / ~100 tickets / ~$27 avg.
# Weekdays: lower ticket count; wholesale inflates Thu/Mon ticket dollars.
SALES_DAYS = [
    # week of Aug 17
    (date(2026, 8, 17), 36, 148000, "Wholesale"),
    (date(2026, 8, 18), 32, 78000, ""),
    (date(2026, 8, 19), 38, 89000, ""),
    (date(2026, 8, 20), 42, 164000, "Wholesale"),
    (date(2026, 8, 21), 58, 132000, ""),
    (date(2026, 8, 22), 104, 281200, "Money day"),
    (date(2026, 8, 23), 74, 192000, ""),
    # week of Aug 24
    (date(2026, 8, 24), 40, 156000, "Wholesale"),
    (date(2026, 8, 25), 34, 81000, ""),
    (date(2026, 8, 26), 41, 94000, ""),
    (date(2026, 8, 27), 44, 171000, "Wholesale"),
    (date(2026, 8, 28), 61, 138000, ""),
    (date(2026, 8, 29), 98, 264600, "Money day"),
]

# Period totals for the reports top-items table. Peach is low because it is often OOS.
TOP_ITEMS = [
    (date(2026, 8, 29), "Viet Coffee", "", 96, 52800),
    (date(2026, 8, 29), "Fruit Tea", "", 68, 40800),
    (date(2026, 8, 29), "Mango Entrement", "", 22, 18700),
    (date(2026, 8, 29), "Cream Cheese Danish", "", 20, 9000),
    (date(2026, 8, 29), "Feta Danish", "", 18, 8100),
    (date(2026, 8, 29), "Peach Entrement", "", 8, 6800),
]

# Drink × modifier matrix: Fruit Tea flavors, Milk Tea + tapioca, Viet coffee sweet/sauce/oat.
DRINK_MODIFIERS = [
    ("Fruit Tea", "Flavor", "Strawberry", 67),
    ("Fruit Tea", "Flavor", "Mango", 38),
    ("Fruit Tea", "Flavor", "Peach", 37),
    ("Fruit Tea", "Boba", "Strawberry Bursting", 41),
    ("Milk Tea", "Flavor", "Brown Sugar", 33),
    ("Milk Tea", "Flavor", "Taro", 22),
    ("Milk Tea", "Flavor", "Thai Tea", 21),
    ("Milk Tea", "Boba", "Tapioca Boba", 54),
    ("Viet Coffee", "Sweet", "25%", 79),
    ("Viet Coffee", "Sweet", "50%", 60),
    ("Viet Coffee", "Sauce", "Salted Caramel", 36),
    ("Viet Coffee", "Milk", "Oat milk", 30),
]

MERCH = [
    ("Tote", 2000, 20, 3, "3-day prep — not a same-day add-on"),
    ("Lucky Money Bag", 1000, 9, 3, "3-day prep — not a same-day add-on"),
]

# ~8 tickets scattered across the last 15 minutes so /board looks alive after seed.
BOARD_TICKETS = [
    (14, "Fruit Tea", "Walk-in", 1, [
        {"group": "Flavor", "value": "Strawberry"},
        {"group": "Boba", "value": "Strawberry Bursting"},
    ]),
    (12, "Viet Coffee", "Counter", 1, [
        {"group": "Sweet", "value": "25%"},
        {"group": "Milk", "value": "Oat milk"},
    ]),
    (10, "Milk Tea", "#104", 1, [
        {"group": "Flavor", "value": "Taro"},
        {"group": "Boba", "value": "Tapioca Boba"},
    ]),
    (8, "Matcha", "Walk-in", 1, [
        {"group": "Matcha", "value": "Strawberry Matcha"},
        {"group": "Milk", "value": "Oat milk"},
    ]),
    (6, "Biscoff", "Counter", 1, [
        {"group": "Milk", "value": "Oat milk"},
    ]),
    (4, "Fruit Tea", "Mobile", 2, [
        {"group": "Flavor", "value": "Peach"},
        {"group": "Boba", "value": "Peach Bursting"},
        {"group": "Ice", "value": "Less Ice"},
    ]),
    (2, "Viet Coffee", "#118", 1, [
        {"group": "Sweet", "value": "50%"},
        {"group": "Sauce", "value": "Salted Caramel"},
        {"group": "Extra Shot", "value": "Double Shot"},
    ]),
    (1, "Milk Tea", "Table 3", 1, [
        {"group": "Flavor", "value": "Brown Sugar"},
        {"group": "Boba", "value": "Tapioca Boba"},
        {"group": "Sweet", "value": "Normal"},
    ]),
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def seed_reports(db) -> None:
    db.execute(delete(SalesSummary))
    db.execute(delete(SalesDaily))
    db.execute(delete(DrinkModifier))
    db.execute(delete(MerchItem))
    db.add_all(
        [
            SalesSummary(sold_on=d, tickets=t, cents=c, note=note)
            for d, t, c, note in SALES_DAYS
        ]
    )
    db.add_all(
        [
            SalesDaily(sold_on=d, item_name=name, modifier_name=mod, qty=qty, cents=cents)
            for d, name, mod, qty, cents in TOP_ITEMS
        ]
    )
    db.add_all(
        [
            DrinkModifier(drink=drink, modifier_group=group, modifier=mod, qty=qty)
            for drink, group, mod, qty in DRINK_MODIFIERS
        ]
    )
    db.add_all(
        [
            MerchItem(name=name, price_cents=price, qty=qty, prep_days=prep, warning=warn)
            for name, price, qty, prep, warn in MERCH
        ]
    )


def refresh_drink_tickets(db) -> None:
    db.execute(delete(DrinkTicket))
    now = _utc_now()
    db.add_all(
        [
            DrinkTicket(
                ordered_at=now - timedelta(minutes=mins),
                drink_name=drink,
                modifiers_json=json.dumps(mods),
                qty=qty,
                ticket_name=ticket_name,
                source="seed",
            )
            for mins, drink, ticket_name, qty, mods in BOARD_TICKETS
        ]
    )



LOYALTY_JSON = ROOT / "data" / "square_loyalty.json"

# Demo members only used when data/square_loyalty.json is missing.
# Phones are fictional 555 numbers; area codes are real NPAs for hometown mix.
# Tuple: given, family, area, rest7, email, pts, life, visits, last_days, first_days,
# enrolled_days, spend_cents, fav_drink, fav_item, fav_mod, source, unsub, tags, zip, n_events
DEMO_MEMBERS = [
    ("Linh", "Nguyen", "205", "5550101", "linh@example.com", 142, 242, 18, 2, 80, 90, 24200, "Fruit Tea", "Fruit Tea", "Strawberry", "LOYALTY", 0, ["LOYAL", "REACHABLE"], "35210", 6),
    ("Minh", "Tran", "205", "5550102", "minh@example.com", 218, 318, 24, 5, 120, 140, 31800, "Viet Coffee", "Viet Coffee", "25%", "LOYALTY", 0, ["LOYAL", "REACHABLE"], "35210", 7),
    ("Hoa", "Pham", "205", "5550103", "", 96, 96, 9, 12, 60, 70, 9600, "Fruit Tea", "Fruit Tea", "Mango", "INSTANT_PROFILE", 0, ["LOYALTY"], "35206", 4),
    ("Mai", "Le", "205", "5550104", "mai.le@example.com", 55, 155, 11, 8, 200, 210, 15500, "Milk Tea", "Milk Tea", "Taro", "MERGE", 0, ["LOYAL", "CHURN_RISK"], "35222", 5),
    ("David", "Nguyen", "205", "5550105", "david.n@example.com", 310, 410, 31, 1, 300, 320, 41000, "Viet Coffee", "Viet Coffee", "Salted Caramel", "LOYALTY", 0, ["LOYAL", "REACHABLE"], "35210", 8),
    ("Sarah", "Brooks", "205", "5550106", "sarah.b@example.com", 12, 12, 2, 4, 10, 14, 1800, "Matcha", "Strawberry Matcha", "Oat milk", "INSTANT_PROFILE", 0, ["REACHABLE"], "35213", 3),
    ("James", "Walker", "205", "5550107", "", 80, 80, 7, 22, 90, 100, 8000, "Fruit Tea", "Fruit Tea", "Peach", "DIRECTORY", 0, ["LOYALTY"], "35126", 4),
    ("Ashley", "Reed", "205", "5550108", "ashley.reed@example.com", 101, 101, 10, 3, 40, 45, 10100, "Fruit Tea", "Fruit Tea", "Strawberry Bursting", "LOYALTY", 0, ["LOYAL", "REACHABLE"], "35210", 5),
    ("Kevin", "Patel", "205", "5550109", "kpatel@example.com", 44, 44, 5, 18, 50, 55, 4400, "Biscoff", "Biscoff", "Oat milk", "MERGE", 0, ["REACHABLE"], "35209", 3),
    ("Thy", "Vo", "205", "5550110", "", 175, 175, 16, 6, 110, 120, 17500, "Fruit Tea", "Fruit Tea", "Mango Bursting", "LOYALTY", 0, ["LOYAL"], "35210", 6),
    ("Marcus", "Hill", "205", "5550111", "marcus.h@example.com", 9, 209, 14, 71, 240, 250, 20900, "Viet Coffee", "Viet Coffee", "50%", "MERGE", 0, ["CHURN_RISK"], "35206", 5),
    ("Elena", "Garcia", "205", "5550112", "elena.g@example.com", 63, 63, 6, 9, 30, 35, 6300, "Milk Tea", "Milk Tea", "Brown Sugar", "INSTANT_PROFILE", 0, ["REACHABLE"], "35222", 3),
    ("Chris", "Adams", "205", "5550113", "", 28, 28, 3, 45, 80, 90, 2800, "Viet Coffee", "Viet Coffee", "Oat milk", "DIRECTORY", 0, ["LOYALTY"], "35210", 3),
    ("Priya", "Shah", "205", "5550114", "priya.shah@example.com", 88, 88, 8, 11, 70, 80, 8800, "Matcha", "Matcha", "Strawberry Matcha", "LOYALTY", 0, ["REACHABLE"], "35213", 4),
    ("Tony", "Dang", "205", "5550115", "tony.dang@example.com", 250, 450, 28, 2, 180, 190, 45000, "Viet Coffee", "Viet Coffee", "Double Shot", "LOYALTY", 0, ["LOYAL", "REACHABLE"], "35210", 8),
    ("Rachel", "Kim", "205", "5550116", "", 0, 0, 0, None, None, 20, 0, "", "", "", "INSTANT_PROFILE", 0, [], "35210", 0),
    ("Ben", "Foster", "205", "5550117", "ben.foster@example.com", 15, 15, 1, 88, 88, 100, 1500, "Fruit Tea", "Fruit Tea", "Strawberry", "MERGE", 1, ["CHURN_RISK"], "35117", 3),
    ("Anna", "Vu", "256", "5550201", "anna.vu@example.com", 120, 120, 9, 7, 60, 70, 12000, "Fruit Tea", "Fruit Tea", "Peach", "LOYALTY", 0, ["LOYAL", "REACHABLE"], "35801", 5),
    ("Jake", "Collins", "256", "5550202", "", 34, 34, 4, 16, 40, 50, 3400, "Viet Coffee", "Viet Coffee", "25%", "INSTANT_PROFILE", 0, ["LOYALTY"], "35806", 3),
    ("Sophie", "Wright", "256", "5550203", "sophie.w@example.com", 0, 0, 0, None, None, 12, 0, "", "", "", "DIRECTORY", 0, [], "35802", 0),
    ("Huy", "Bui", "256", "5550204", "huy.bui@example.com", 205, 205, 15, 4, 100, 110, 20500, "Viet Coffee", "Viet Coffee", "Salted Caramel", "LOYALTY", 0, ["LOYAL"], "35801", 6),
    ("Carla", "Sims", "256", "5550205", "", 72, 72, 6, 65, 90, 95, 7200, "Milk Tea", "Milk Tea", "Tapioca Boba", "MERGE", 0, ["CHURN_RISK"], "35758", 4),
    ("Diego", "Morales", "251", "5550301", "diego.m@example.com", 48, 48, 5, 20, 55, 60, 4800, "Fruit Tea", "Fruit Tea", "Mango", "INSTANT_PROFILE", 0, ["REACHABLE"], "36602", 3),
    ("Lan", "Hoang", "251", "5550302", "", 110, 210, 12, 9, 150, 160, 21000, "Fruit Tea", "Fruit Tea", "Strawberry", "LOYALTY", 0, ["LOYAL"], "36604", 5),
    ("Beth", "Turner", "251", "5550303", "beth.t@example.com", 6, 6, 1, 110, 110, 120, 650, "Matcha", "Matcha", "", "DIRECTORY", 0, ["CHURN_RISK"], "36532", 2),
    ("Omar", "Hassan", "334", "5550401", "omar.h@example.com", 91, 91, 8, 14, 70, 80, 9100, "Viet Coffee", "Viet Coffee", "50%", "LOYALTY", 0, ["REACHABLE"], "36104", 4),
    ("Grace", "Parks", "334", "5550402", "", 160, 160, 13, 3, 90, 100, 16000, "Fruit Tea", "Fruit Tea", "Peach Bursting", "MERGE", 0, ["LOYAL"], "36106", 6),
    ("Will", "Grant", "334", "5550403", "will.grant@example.com", 0, 0, 0, None, None, 8, 0, "", "", "", "INSTANT_PROFILE", 0, [], "36830", 0),
    ("Natalie", "Cho", "334", "5550404", "", 39, 39, 4, 28, 45, 50, 3900, "Milk Tea", "Milk Tea", "Thai Tea", "DIRECTORY", 0, ["LOYALTY"], "36116", 3),
    ("Alex", "Kim", "404", "5550501", "alex.kim@example.com", 77, 77, 6, 15, 50, 55, 7700, "Fruit Tea", "Fruit Tea", "Strawberry", "LOYALTY", 0, ["REACHABLE"], "30308", 4),
    ("Jordan", "Lee", "404", "5550502", "", 130, 230, 11, 6, 80, 90, 23000, "Viet Coffee", "Viet Coffee", "Oat milk", "MERGE", 0, ["LOYAL"], "30309", 5),
    ("Maya", "Patel", "404", "5550503", "maya.p@example.com", 22, 22, 2, 95, 95, 110, 2200, "Matcha", "Matcha", "Oat milk", "INSTANT_PROFILE", 0, ["CHURN_RISK"], "30318", 3),
    ("Chris", "Nguyen", "615", "5550601", "cnguyen@example.com", 58, 58, 5, 19, 40, 45, 5800, "Fruit Tea", "Fruit Tea", "Mango", "LOYALTY", 0, ["REACHABLE"], "37203", 3),
    ("Taylor", "Brooks", "615", "5550602", "", 0, 0, 0, None, None, 30, 0, "", "", "", "DIRECTORY", 0, [], "37206", 0),
    ("Sam", "Rivera", "615", "5550603", "sam.r@example.com", 104, 104, 8, 11, 60, 70, 10400, "Milk Tea", "Milk Tea", "Brown Sugar", "MERGE", 0, ["LOYAL", "REACHABLE"], "37209", 4),
    ("Katie", "Nguyen", "214", "5550701", "katie.n@example.com", 41, 41, 4, 21, 35, 40, 4100, "Viet Coffee", "Viet Coffee", "25%", "INSTANT_PROFILE", 0, ["REACHABLE"], "75201", 3),
    ("Paul", "Nguyen", "214", "5550702", "", 199, 199, 17, 4, 130, 140, 19900, "Viet Coffee", "Viet Coffee", "Salted Caramel", "LOYALTY", 0, ["LOYAL"], "75204", 7),
    ("Riley", "Chen", "214", "5550703", "riley.c@example.com", 8, 8, 1, 75, 75, 80, 800, "Biscoff", "Biscoff", "", "MERGE", 0, ["CHURN_RISK"], "75001", 2),
    ("Ava", "Johnson", "205", "5550118", "ava.j@example.com", 67, 67, 6, 13, 40, 42, 6700, "Fruit Tea", "Fruit Tea", "Strawberry", "LOYALTY", 0, ["REACHABLE"], "35210", 4),
    ("Noah", "Baker", "205", "5550119", "", 0, 100, 8, 130, 200, 210, 10000, "Viet Coffee", "Viet Coffee", "25%", "MERGE", 0, ["CHURN_RISK"], "35205", 4),
]


def _demo_phone(area: str, rest7: str) -> str:
    return f"+1{area}{rest7}"


def _demo_events(member: LoyaltyMember, n_events: int, enrolled_days: int, last_days: int | None, pts: int, life: int) -> list[LoyaltyEvent]:
    if n_events <= 0:
        return []
    now = _utc_now()
    events: list[LoyaltyEvent] = []
    # Walk a simple ledger: accumulate visits, maybe one redeem if lifetime > points.
    span = max(enrolled_days, 10)
    leftover = life
    redeem = max(0, life - pts)
    for i in range(n_events):
        offset = max(0, span - int(span * i / max(n_events, 1)))
        if last_days is not None and i == n_events - 1:
            offset = last_days
        at = now - timedelta(days=offset, hours=i % 5, minutes=12)
        if i == n_events - 2 and redeem >= 100:
            events.append(
                LoyaltyEvent(
                    member=member,
                    at=at,
                    event_type="REDEEM",
                    points=-100 if redeem < 200 else -200,
                    order_id=f"demo-r{i}",
                    note="Redeemed reward",
                )
            )
        else:
            chunk = max(4, leftover // max(1, n_events - i))
            leftover = max(0, leftover - chunk)
            events.append(
                LoyaltyEvent(
                    member=member,
                    at=at,
                    event_type="ACCUMULATE",
                    points=chunk,
                    order_id=f"demo-o{i}",
                    note="Visit",
                )
            )
    return events


def seed_loyalty_demo(db) -> int:
    now = _utc_now()
    n = 0
    for row in DEMO_MEMBERS:
        (
            given, family, area, rest7, email, pts, life, visits, last_days, first_days,
            enrolled_days, spend, fav_drink, fav_item, fav_mod, source, unsub, tags, zip_code, n_events,
        ) = row
        phone = _demo_phone(area, rest7)
        geo = lookup_phone(phone)
        last_at = None if last_days is None else now - timedelta(days=last_days)
        first_at = None if first_days is None else now - timedelta(days=first_days)
        enrolled = now - timedelta(days=enrolled_days)
        member = LoyaltyMember(
            square_loyalty_id=f"demo-{area}-{rest7}",
            square_customer_id=f"CUST-{area}{rest7}",
            given_name=given,
            family_name=family,
            phone=phone,
            email=email,
            points=pts,
            lifetime_points=life,
            enrolled_at=enrolled,
            updated_at=last_at or enrolled,
            visits=visits,
            last_visit_at=last_at,
            first_visit_at=first_at,
            lifetime_cents=spend,
            favorite_item=fav_item,
            favorite_drink=fav_drink,
            favorite_modifier=fav_mod,
            zip_code=zip_code,
            creation_source=source,
            email_unsubscribed=unsub,
            segments_json=json.dumps(tags),
            notes="",
            area_code=geo["area_code"],
            area_metro=geo["area_metro"],
            area_state=geo["area_state"],
            area_region=geo["area_region"],
        )
        member.status = compute_status(member, now)
        db.add(member)
        db.flush()
        for ev in _demo_events(member, n_events, enrolled_days, last_days, pts, life):
            db.add(ev)
        n += 1
    return n


def seed_or_import_loyalty(db) -> int:
    existing = db.scalar(select(func.count(LoyaltyMember.id))) or 0
    if existing:
        print("loyalty_members already have rows; skip those")
        return int(existing)
    if LOYALTY_JSON.is_file():
        from scripts.import_loyalty import import_file, summarize

        stats = import_file(LOYALTY_JSON, db)
        print(summarize(stats, LOYALTY_JSON))
        return int(stats["total"])
    n = seed_loyalty_demo(db)
    print(f"seeded {n} demo loyalty members (no square_loyalty.json on disk)")
    return n


def main() -> None:
    init_db()
    with SessionLocal() as db:
        existing_summary = db.scalar(select(SalesSummary.id).limit(1))
        if existing_summary is None:
            seed_reports(db)
            print("seeded sales_summary, sales_daily, drink_modifiers, merch")
        else:
            print("reports tables already have rows; skip those")
        refresh_drink_tickets(db)
        n_loyal = seed_or_import_loyalty(db)
        db.commit()
        print(f"seeded {len(BOARD_TICKETS)} drink_tickets in the last 15 minutes")
        if n_loyal is not None:
            print(f"loyalty members now in db: {n_loyal}")


if __name__ == "__main__":
    main()
