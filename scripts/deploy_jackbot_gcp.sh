#!/usr/bin/env bash
# deploy_jackbot_gcp.sh — 一鍵部署 jackbot_v1 + dashboard 到 GCP VM
#
# 使用方式（本機執行，需要 gcloud 已登入）:
#   bash scripts/deploy_jackbot_gcp.sh          # 完整部署
#   bash scripts/deploy_jackbot_gcp.sh bot       # 只更新 jackbot
#   bash scripts/deploy_jackbot_gcp.sh dashboard # 只更新 dashboard
#   bash scripts/deploy_jackbot_gcp.sh firewall  # 只開防火牆

set -euo pipefail

# ── 設定 ─────────────────────────────────────────────────────────────
VM=instance-20260424-060848
ZONE=asia-east1-b
REMOTE_DIR=/home/punktoggy/cry2
JACKBOT_DIR=${REMOTE_DIR}/jackbot
GCP_TAG=bot-vm
DASHBOARD_PORT=8080
YOUR_IP=${MY_IP:-$(curl -s https://ifconfig.me 2>/dev/null || curl -s https://api4.my-ip.io/ip)}

SSH_CMD="gcloud compute ssh ${VM} --zone=${ZONE} --command"

sep() { printf '\n\033[1;33m════ %s ════\033[0m\n' "$*"; }
ok()  { printf '\033[0;32m  ✔ %s\033[0m\n' "$*"; }
info(){ printf '\033[0;36m  → %s\033[0m\n' "$*"; }

# ── 1. 確認 VM 可連線 ─────────────────────────────────────────────────
check_vm() {
    sep "確認 VM 連線"
    gcloud compute instances describe "${VM}" --zone="${ZONE}" \
        --format="value(status)" | grep -q RUNNING \
        && ok "VM ${VM} 執行中" \
        || { echo "VM 未執行，請先啟動"; exit 1; }
}

# ── 2. 拉最新程式碼 ──────────────────────────────────────────────────
pull_code() {
    sep "更新程式碼"
    $SSH_CMD "
        set -e
        cd ${REMOTE_DIR}
        git fetch origin
        git checkout claude/monitor-jackbot-gcp-66lOA 2>/dev/null || true
        git pull origin claude/monitor-jackbot-gcp-66lOA
    "
    ok "程式碼已更新"
}

# ── 3. Build & 重啟 jackbot ──────────────────────────────────────────
deploy_bot() {
    sep "Build & 重啟 jackbot-v1"
    $SSH_CMD "
        set -e
        cd ${JACKBOT_DIR}
        docker compose build jackbot
        docker compose up -d --no-deps --force-recreate jackbot
        sleep 3
        docker inspect jackbot-v1 --format='Restarts={{.RestartCount}} Status={{.State.Status}}'
    "
    ok "jackbot-v1 已重啟"
}

# ── 4. Build & 重啟 dashboard ────────────────────────────────────────
deploy_dashboard() {
    sep "Build & 重啟 jackbot-dashboard"
    $SSH_CMD "
        set -e
        cd ${JACKBOT_DIR}
        docker compose build dashboard
        docker compose up -d --no-deps --force-recreate dashboard
        sleep 3
        docker inspect jackbot-dashboard --format='Restarts={{.RestartCount}} Status={{.State.Status}}'
    "
    ok "jackbot-dashboard 已重啟（port ${DASHBOARD_PORT}）"
}

# ── 5. 開防火牆 ─────────────────────────────────────────────────────
open_firewall() {
    sep "GCP 防火牆 (port ${DASHBOARD_PORT})"
    info "本機 IP: ${YOUR_IP}"

    # 若規則已存在則更新，否則建立
    if gcloud compute firewall-rules describe jackbot-dashboard --quiet 2>/dev/null; then
        gcloud compute firewall-rules update jackbot-dashboard \
            --allow="tcp:${DASHBOARD_PORT}" \
            --source-ranges="${YOUR_IP}/32"
        ok "防火牆規則已更新"
    else
        gcloud compute firewall-rules create jackbot-dashboard \
            --allow="tcp:${DASHBOARD_PORT}" \
            --source-ranges="${YOUR_IP}/32" \
            --target-tags="${GCP_TAG}" \
            --description="Jackbot V1 dashboard"
        ok "防火牆規則已建立"
    fi

    EXTERNAL_IP=$(gcloud compute instances describe "${VM}" --zone="${ZONE}" \
        --format="value(networkInterfaces[0].accessConfigs[0].natIP)")
    echo ""
    printf '\033[1;32m  ✔ Dashboard URL: http://%s:%s\033[0m\n' "${EXTERNAL_IP}" "${DASHBOARD_PORT}"
}

# ── 6. 健康確認 ─────────────────────────────────────────────────────
health_check() {
    sep "健康確認"
    $SSH_CMD "
        echo '--- containers ---'
        docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
        echo ''
        echo '--- jackbot-v1 last 20 lines ---'
        docker logs jackbot-v1 --tail 20 2>&1
        echo ''
        echo '--- dashboard last 10 lines ---'
        docker logs jackbot-dashboard --tail 10 2>&1
    "
}

# ── Main ─────────────────────────────────────────────────────────────
TARGET="${1:-all}"

check_vm

case "${TARGET}" in
    all)
        pull_code
        deploy_bot
        deploy_dashboard
        open_firewall
        health_check
        ;;
    bot)
        pull_code
        deploy_bot
        ;;
    dashboard)
        pull_code
        deploy_dashboard
        open_firewall
        ;;
    firewall)
        open_firewall
        ;;
    health)
        health_check
        ;;
    pull)
        pull_code
        ;;
    *)
        echo "用法: $0 [all|bot|dashboard|firewall|health|pull]"
        exit 1
        ;;
esac

sep "完成"
