#!/bin/bash
# Bootstrap script for GCP VM
# Run after VM creation: gcloud compute ssh binance-bot -- 'bash -s' < deploy/scripts/bootstrap_vm.sh

set -euo pipefail

echo "=== Installing Docker ==="
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker "$USER"

echo "=== Mounting data disk ==="
DISK_DEVICE="/dev/sdb"
MOUNT_POINT="/data"

if ! mountpoint -q "$MOUNT_POINT"; then
    sudo mkdir -p "$MOUNT_POINT"
    if ! sudo blkid "$DISK_DEVICE"; then
        sudo mkfs.ext4 -m 0 -F "$DISK_DEVICE"
    fi
    echo "$DISK_DEVICE $MOUNT_POINT ext4 discard,defaults,nofail 0 2" | sudo tee -a /etc/fstab
    sudo mount "$MOUNT_POINT"
    sudo chmod 755 "$MOUNT_POINT"
fi

echo "=== Setting up directories ==="
sudo mkdir -p /data/bot-data /data/bot-logs
sudo chown -R "$USER:$USER" /data

echo "=== Setting up systemd service ==="
sudo cp /tmp/bot.service /etc/systemd/system/ 2>/dev/null || true
sudo systemctl daemon-reload
sudo systemctl enable bot.service 2>/dev/null || true

echo "=== Bootstrap complete ==="
echo "Next steps:"
echo "1. Clone repo: git clone <repo-url> /data/app"
echo "2. Configure: cp .env.example .env && nano .env"
echo "3. Start: cd /data/app && docker compose up -d"
