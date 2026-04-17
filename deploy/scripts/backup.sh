#!/bin/bash
# Daily backup to GCS
# Add to cron: 0 2 * * * /data/app/deploy/scripts/backup.sh >> /data/bot-logs/backup.log 2>&1

set -euo pipefail

BUCKET="${GCS_BACKUP_BUCKET:-}"
DATA_DIR="${DATA_DIR:-/data/bot-data}"
LOGS_DIR="${LOGS_DIR:-/data/bot-logs}"
DATE=$(date +%Y-%m-%d)

if [ -z "$BUCKET" ]; then
    echo "ERROR: GCS_BACKUP_BUCKET not set"
    exit 1
fi

echo "=== Starting backup: $DATE ==="

echo "Backing up data..."
gsutil -m rsync -r "$DATA_DIR" "gs://$BUCKET/data/"

echo "Backing up logs..."
gsutil -m rsync -r "$LOGS_DIR" "gs://$BUCKET/logs/"

echo "=== Backup complete: $DATE ==="
