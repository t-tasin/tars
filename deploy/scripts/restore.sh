#!/bin/bash
# restore.sh — Restore T.A.R.S. PostgreSQL database from a backup file
# Usage: ./restore.sh <backup-file>
# Supports both .sql and .sql.gz files
set -euo pipefail

BACKUP_FILE="${1:-}"

if [ -z "$BACKUP_FILE" ]; then
    echo "Usage: ./restore.sh <backup-file>"
    echo "Example: ./restore.sh /data/backups/tars_20260310_120000.sql.gz"
    exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "[T.A.R.S.] Error: Backup file not found: $BACKUP_FILE"
    exit 1
fi

echo "============================================"
echo "  WARNING: This will DESTROY the current"
echo "  T.A.R.S. database and restore from:"
echo "  $BACKUP_FILE"
echo "============================================"
echo ""
read -rp "Are you sure? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "[T.A.R.S.] Restore cancelled."
    exit 0
fi

echo "[T.A.R.S.] Stopping tars-backend..."
docker stop tars-backend 2>/dev/null || true

echo "[T.A.R.S.] Dropping and recreating database..."
docker exec tars-db psql -U tars -d postgres -c "DROP DATABASE IF EXISTS tars;"
docker exec tars-db psql -U tars -d postgres -c "CREATE DATABASE tars OWNER tars;"

echo "[T.A.R.S.] Restoring from $BACKUP_FILE..."
if [[ "$BACKUP_FILE" == *.gz ]]; then
    gunzip -c "$BACKUP_FILE" | docker exec -i tars-db psql -U tars -d tars
else
    docker exec -i tars-db psql -U tars -d tars < "$BACKUP_FILE"
fi

echo "[T.A.R.S.] Restarting tars-backend..."
docker start tars-backend

echo "[T.A.R.S.] Restore complete from: $BACKUP_FILE"
