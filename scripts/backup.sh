#!/bin/sh
# Nightly Postgres backup loop (runs inside the `backup` compose service).
# Dumps to /backups/kapital-YYYYmmdd-HHMMSS.sql.gz and prunes dumps older than
# RETENTION_DAYS (default 14). Uses the same POSTGRES_* env as the db service.
set -eu

RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
PGHOST="${POSTGRES_HOST:-postgres}"
PGUSER="${POSTGRES_USER:-kapital}"
PGDATABASE="${POSTGRES_DB:-kapital}"
export PGPASSWORD="${POSTGRES_PASSWORD:-}"

mkdir -p /backups

while :; do
    ts="$(date +%Y%m%d-%H%M%S)"
    out="/backups/kapital-${ts}.sql.gz"
    echo "[backup] dumping ${PGDATABASE}@${PGHOST} -> ${out}"
    if pg_dump -h "$PGHOST" -U "$PGUSER" "$PGDATABASE" | gzip > "$out"; then
        echo "[backup] ok ($(du -h "$out" | cut -f1))"
    else
        echo "[backup] FAILED" >&2
        rm -f "$out"
    fi

    # Prune old dumps.
    find /backups -name 'kapital-*.sql.gz' -type f -mtime "+${RETENTION_DAYS}" -delete || true

    # Sleep until ~03:00 the next day, then loop.
    sleep 86400
done
