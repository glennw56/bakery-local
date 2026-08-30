#!/bin/sh
# One-time (or anytime) local install. Run from repo root, or via this script's path.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt

mkdir -p data
DB="${BAKERY_DB:-data/app.db}"
if [ ! -f "$DB" ]; then
  echo "No database at $DB — seeding demo data."
  PYTHONPATH=. .venv/bin/python -m scripts.seed_demo
else
  echo "Database already exists at $DB — skip seed."
fi

echo "Setup done. Start with: ./scripts/run.sh"
