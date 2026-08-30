"""SQLAlchemy 2.x entities (JPA @Entity). Sessions come from app/db.py.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class SalesDaily(Base):
    """Item lines for the period. A later Square import can land here. No Square keys."""

    __tablename__ = "sales_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sold_on: Mapped[date] = mapped_column(Date, nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    modifier_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SalesSummary(Base):
    """One row per shop day: ticket count and dollars. Sat is the money day."""

    __tablename__ = "sales_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sold_on: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    tickets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class DrinkModifier(Base):
    """Drink × modifier counts for the reports matrix."""

    __tablename__ = "drink_modifiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    drink: Mapped[str] = mapped_column(String(255), nullable=False)
    modifier_group: Mapped[str] = mapped_column(String(255), nullable=False)
    modifier: Mapped[str] = mapped_column(String(255), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MerchItem(Base):
    """Merch on-hand. Prep warning lives here so the report can show it."""

    __tablename__ = "merch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prep_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class DrinkTicket(Base):
    """Kitchen drink tickets. ordered_at is naive UTC. modifiers_json is a JSON list."""

    __tablename__ = "drink_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ordered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    drink_name: Mapped[str] = mapped_column(String(255), nullable=False)
    modifiers_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ticket_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="local")


class LoyaltyMember(Base):
    """One Square loyalty account, denormalized for list/export.

    Phone + points come from LoyaltyAccount. Name/email from Customers API
    (often still blank). Favorite item / spend / visits are denormalized so
    the list stays useful when order joins are sparse. Area_* is NANP
    geography from the phone — not skip-tracing.
    """

    __tablename__ = "loyalty_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    square_loyalty_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    square_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    given_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    family_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lifetime_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    visits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_visit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_visit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lifetime_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    favorite_item: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    favorite_drink: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    favorite_modifier: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    zip_code: Mapped[str] = mapped_column("zip", String(16), nullable=False, default="")
    creation_source: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    email_unsubscribed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    segments_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    area_code: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    area_metro: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    area_state: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    area_region: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")

    events: Mapped[list[LoyaltyEvent]] = relationship(
        back_populates="member",
        cascade="all, delete-orphan",
    )


class LoyaltyEvent(Base):
    """Points ledger for a member. Newest-first is applied at query time."""

    __tablename__ = "loyalty_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("loyalty_members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, default="OTHER")
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    note: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    member: Mapped[LoyaltyMember] = relationship(back_populates="events")
