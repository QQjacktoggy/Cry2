#!/bin/bash
# Deploy/update the bot on the VM
set -euo pipefail

APP_DIR="${APP_DIR:-/data/app}"

cd "$APP_DIR"

echo "=== Pulling latest code ==="
git pull origin main

echo "=== Rebuilding containers ==="
docker compose build --no-cache

echo "=== Restarting services ==="
docker compose down
docker compose up -d

echo "=== Deployment complete ==="
docker compose ps
