#!/bin/bash
# backup.sh — Backup T.A.R.S. PostgreSQL database
# Usage: ./backup.sh
# Creates timestamped, gzip-compressed dumps in /data/backups/
# Retains only the last 7 daily backups
set -euo pipefail

BACKUP_DIR="/data/backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DUMP_FILE="tars_${TIMESTAMP}.sql"
DUMP_PATH="${BACKUP_DIR}/${DUMP_FILE}"

echo "[T.A.R.S.] Starting PostgreSQL backup..."

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Dump the database
docker exec tars-db pg_dump -U tars tars > "$DUMP_PATH"
echo "[T.A.R.S.] Dump created: $DUMP_PATH"

# Compress the dump
gzip "$DUMP_PATH"
COMPRESSED="${DUMP_PATH}.gz"
echo "[T.A.R.S.] Compressed: $COMPRESSED"

# Print backup size
BACKUP_SIZE="$(du -h "$COMPRESSED" | cut -f1)"
echo "[T.A.R.S.] Backup size: $BACKUP_SIZE"

# Delete backups older than 7 days
DELETED_COUNT=0
while IFS= read -r old_backup; do
    rm -f "$old_backup"
    DELETED_COUNT=$((DELETED_COUNT + 1))
done < <(find "$BACKUP_DIR" -name "tars_*.sql.gz" -mtime +7 -type f)

if [ "$DELETED_COUNT" -gt 0 ]; then
    echo "[T.A.R.S.] Cleaned up $DELETED_COUNT old backup(s)."
fi

echo "[T.A.R.S.] Backup complete: $COMPRESSED ($BACKUP_SIZE)"
