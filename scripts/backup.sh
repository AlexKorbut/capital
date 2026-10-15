#!/bin/sh
# Nightly backup loop (runs inside the `backup` compose service).
# Dumps Postgres to /backups/kapital-YYYYmmdd-HHMMSS.sql.gz and, next to it, archives the `uploads`
# volume (receipt photos on finance expenses, mounted read-only at /uploads) to
# /backups/kapital-uploads-YYYYmmdd-HHMMSS.tar.gz. Both kinds are pruned after RETENTION_DAYS
# (default 14). Uses the same POSTGRES_* env as the db service.
#
# BACKUP_DIR and UPLOADS_SRC exist so the script can be exercised away from the container.
set -eu

RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
UPLOADS_SRC="${UPLOADS_SRC:-/uploads}"
PGHOST="${POSTGRES_HOST:-postgres}"
PGUSER="${POSTGRES_USER:-kapital}"
PGDATABASE="${POSTGRES_DB:-kapital}"
export PGPASSWORD="${POSTGRES_PASSWORD:-}"

mkdir -p "$BACKUP_DIR"

while :; do
    ts="$(date +%Y%m%d-%H%M%S)"
    out="${BACKUP_DIR}/kapital-${ts}.sql.gz"
    echo "[backup] dumping ${PGDATABASE}@${PGHOST} -> ${out}"
    if pg_dump -h "$PGHOST" -U "$PGUSER" "$PGDATABASE" | gzip > "$out"; then
        echo "[backup] ok ($(du -h "$out" | cut -f1))"
    else
        echo "[backup] FAILED" >&2
        rm -f "$out"
    fi

    # Receipt photos: archive the volume next to the dump (skipped when it is not mounted).
    if [ -d "$UPLOADS_SRC" ]; then
        files_out="${BACKUP_DIR}/kapital-uploads-${ts}.tar.gz"
        echo "[backup] archiving receipt files -> ${files_out}"
        # tar exits 1 when a file changed or vanished while it was read (an upload in flight,
        # a half-written *.tmp): the archive is still good. Anything above 1 is a real failure.
        if tar -czf "$files_out" --exclude='*.tmp' -C "$UPLOADS_SRC" .; then
            files_rc=0
        else
            files_rc=$?
        fi
        if [ "$files_rc" -le 1 ]; then
            echo "[backup] ok ($(du -h "$files_out" | cut -f1))"
        else
            echo "[backup] FAILED (receipt files)" >&2
            rm -f "$files_out"
        fi
    fi

    # Prune old dumps and archives.
    find "$BACKUP_DIR" -name 'kapital-*.sql.gz' -type f -mtime "+${RETENTION_DAYS}" -delete || true
    find "$BACKUP_DIR" -name 'kapital-uploads-*.tar.gz' -type f -mtime "+${RETENTION_DAYS}" -delete || true

    # Sleep until ~03:00 the next day, then loop.
    sleep 86400
done
