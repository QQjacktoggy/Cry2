#!/usr/bin/env bash
# monitor_gcp.sh — 連到 GCP VM 觀察 jackbot_v1 交易行為
#
# 用法:
#   bash scripts/monitor_gcp.sh              # 一次性快照
#   bash scripts/monitor_gcp.sh logs         # 即時 log tail
#   bash scripts/monitor_gcp.sh watch        # 每 30 秒刷新帳戶/掛單狀態
#   bash scripts/monitor_gcp.sh exec <cmd>   # 在容器內執行任意指令

set -euo pipefail

VM=instance-20260424-060848
ZONE=asia-east1-b
CONTAINER=jackbot-v1
JACKBOT_DIR=/home/punktoggy/cry2/jackbot

SSH="gcloud compute ssh ${VM} --zone=${ZONE} --command"

# ── helpers ──────────────────────────────────────────────────────────

sep() { printf '\n\033[1;36m══ %s ══\033[0m\n' "$*"; }

docker_exec() {
    $SSH "docker exec ${CONTAINER} $*"
}

# ── subcommands ──────────────────────────────────────────────────────

cmd_snapshot() {
    sep "VM 基本狀態"
    $SSH "
        echo '--- disk ---' && df -h / &&
        echo '--- memory ---' && free -h &&
        echo '--- uptime ---' && uptime
    "

    sep "Docker 容器"
    $SSH "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.RunningFor}}\t{{.Image}}'"

    sep "jackbot-v1 最近 50 行 log"
    $SSH "docker logs ${CONTAINER} --tail 50 2>&1" || true

    sep "jackbot-v1 即時帳戶 / 掛單 / 持倉"
    $SSH "docker exec ${CONTAINER} python scripts/monitor.py 2>&1" || true
}

cmd_logs() {
    sep "jackbot-v1 即時 log（Ctrl+C 結束）"
    gcloud compute ssh "${VM}" --zone="${ZONE}" -- \
        "docker logs ${CONTAINER} -f 2>&1"
}

cmd_watch() {
    INTERVAL="${2:-30}"
    sep "持續監控模式：每 ${INTERVAL} 秒刷新（Ctrl+C 結束）"
    gcloud compute ssh "${VM}" --zone="${ZONE}" -- \
        "docker exec ${CONTAINER} python scripts/monitor.py --watch --interval ${INTERVAL} 2>&1"
}

cmd_exec() {
    shift
    INNER_CMD="$*"
    sep "在 ${CONTAINER} 內執行: ${INNER_CMD}"
    $SSH "docker exec ${CONTAINER} ${INNER_CMD}"
}

cmd_restart() {
    sep "重啟 jackbot-v1"
    $SSH "cd ${JACKBOT_DIR} && docker compose restart jackbot"
}

cmd_status_json() {
    sep "jackbot-v1 狀態 JSON"
    $SSH "docker exec ${CONTAINER} python scripts/run.py --status 2>&1" || true
}

# ── dispatch ──────────────────────────────────────────────────────────

SUBCMD="${1:-snapshot}"

case "$SUBCMD" in
    snapshot|"")    cmd_snapshot ;;
    logs)           cmd_logs ;;
    watch)          cmd_watch "$@" ;;
    exec)           cmd_exec "$@" ;;
    restart)        cmd_restart ;;
    status)         cmd_status_json ;;
    *)
        echo "用法: $0 [snapshot|logs|watch|exec <cmd>|restart|status]"
        exit 1
        ;;
esac
