#!/bin/sh
# Copy the sqlite file to data/backups/app-YYYYMMDD-HHMM.db (UTC stamp).
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

DB="${BAKERY_DB:-data/app.db}"
if [ ! -f "$DB" ]; then
  echo "No database at $DB — nothing to back up." >&2
  exit 1
fi

mkdir -p data/backups
stamp="$(date -u +%Y%m%d-%H%M)"
dest="data/backups/app-${stamp}.db"
cp "$DB" "$dest"
echo "Copied $DB -> $dest"
