#!/bin/sh
# Optional Docker entry: seed demo db if the mounted data volume is empty, then serve.
set -eu
cd /app
DB="${BAKERY_DB:-data/app.db}"
if [ ! -f "$DB" ]; then
  echo "No database at $DB — seeding demo data."
  PYTHONPATH=. python -m scripts.seed_demo
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
