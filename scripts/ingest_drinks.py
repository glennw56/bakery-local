"""Laptop-only Square drink ingest into local sqlite.

Tablet still polls GET /board/tickets. This script (or --watch) runs on the
shop laptop. Token lives in .env, never git.

    PYTHONPATH=. .venv/bin/python -m scripts.ingest_drinks
    PYTHONPATH=. .venv/bin/python -m scripts.ingest_drinks --watch
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal, init_db  # noqa: E402
from app.ingest import (  # noqa: E402
    WATCH_INTERVAL_SEC,
    ingest_once,
    load_env_file,
    square_access_token,
    square_location_id,
)


def main() -> None:
    load_env_file(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Ingest paid Square drinks into local sqlite")
    parser.add_argument("--minutes", type=int, default=20, help="lookback window (default 20)")
    parser.add_argument("--watch", action="store_true", help="loop on the laptop")
    parser.add_argument("--sleep", type=int, default=WATCH_INTERVAL_SEC, help="watch interval seconds")
    args = parser.parse_args()
    token = square_access_token()
    if not token:
        raise SystemExit("set SQUARE_ACCESS_TOKEN in .env (not git)")
    init_db()
    while True:
        with SessionLocal() as db, httpx.Client(timeout=20.0) as client:
            stats = ingest_once(
                db,
                token=token,
                location_id=square_location_id(),
                client=client,
                lookback_minutes=args.minutes,
            )
        print(
            "ingest drinks "
            f"inserted={stats['inserted']} skipped={stats['skipped']} "
            f"orders={stats['orders']}"
        )
        if not args.watch:
            break
        time.sleep(max(5, args.sleep))


if __name__ == "__main__":
    main()
