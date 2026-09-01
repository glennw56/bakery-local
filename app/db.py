"""SQLite DataSource: engine, sessions, and table bootstrap.

WAL mode lets the UI keep serving while a later import writes the same file.
BAKERY_DB overrides the file path (default data/app.db) — analogous to
spring.datasource.url pointing at an H2/SQLite file.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_DB = DATA_DIR / "app.db"


def db_path() -> Path:
    return Path(os.environ.get("BAKERY_DB", DEFAULT_DB))


def _make_engine():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _enable_wal(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def migrate_schema() -> None:
    """Add Square ingest columns on an existing drink_tickets table (ALTER)."""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    if "drink_tickets" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("drink_tickets")}
    with engine.begin() as conn:
        if "square_order_id" not in cols:
            conn.execute(text("ALTER TABLE drink_tickets ADD COLUMN square_order_id VARCHAR(64)"))
        if "square_line_uid" not in cols:
            conn.execute(text("ALTER TABLE drink_tickets ADD COLUMN square_line_uid VARCHAR(64)"))
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_drink_tickets_square_line "
                "ON drink_tickets(square_order_id, square_line_uid) "
                "WHERE square_order_id IS NOT NULL AND square_line_uid IS NOT NULL"
            )
        )


def init_db() -> None:
    """Create tables if they do not exist. Called on app startup."""
    from app import models  # noqa: F401 — register mappers

    db_path().parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    migrate_schema()
