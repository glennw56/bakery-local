#!/bin/sh
# Optional Docker entry: seed demo db if the mounted data volume is empty, then serve.
set -eu
cd /app
DB="${BAKERY_DB:-data/app.db}"
if [ ! -f "$DB" ]; then
  echo "No database at $DB — seeding demo data."
  PYTHONPATH=. python -m scripts.seed_demo
fi
# Cloud Run terminates HTTPS and forwards HTTP. Trust X-Forwarded-Proto so
# request.base_url is https and PassPRNT's back URL returns to the board.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips='*'
