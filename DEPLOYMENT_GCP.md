# GCP 部署指南 — binance-futures-bot

**搭配文件**: SPEC.md v1.1、ARCHITECTURE.md
**目標讀者**: 開發者、部署者
**環境**: Google Cloud Platform (GCP)

---

## 0. 部署藍圖總覽

```
┌──────────────────────────────────────────────────────────────┐
│                        GCP Project                             │
│                                                                │
│  ┌──────────────────────┐       ┌────────────────────────┐    │
│  │  Secret Manager       │       │   Cloud Storage         │    │
│  │  - binance-api-key    │       │   bucket: bot-backup    │    │
│  │  - binance-api-secret │       │   - /data/*.parquet     │    │
│  │  - telegram-token     │       │   - /logs/*.log         │    │
│  │  - telegram-chat-id   │       └────────────────────────┘    │
│  └──────────┬───────────┘                    ▲                  │
│             │ IAM: Secret Accessor           │ gsutil rsync     │
│             ↓                                 │                  │
│  ┌──────────────────────────────────────────┴────────────┐     │
│  │        Compute Engine VM (e2-small)                      │    │
│  │        Static External IP: xxx.xxx.xxx.xxx               │    │
│  │                                                            │    │
│  │   ┌────────────────────────────────────────────────┐     │    │
│  │   │  Docker Compose                                  │     │    │
│  │   │   ├── bot (main process)                        │     │    │
│  │   │   ├── dashboard (Streamlit :8501)                │     │    │
│  │   │   └── watchtower (auto update, optional)         │     │    │
│  │   └────────────────────────────────────────────────┘     │    │
│  │                                                            │    │
│  │   Persistent Disk (50 GB) mounted at /data                 │    │
│  │   systemd: bot.service (auto-start)                        │    │
│  │   cron: 03:00 UTC daily backup                             │    │
│  └────────────────────┬────────────────────────────────────┘    │
│                        │                                          │
│                        │ Cloud Logging                            │
│                        ↓                                          │
│  ┌──────────────────────────────────┐                            │
│  │  Cloud Logging + Monitoring       │                            │
│  │  - Alert: CPU > 80%, Disk > 80%   │                            │
│  │  - Alert: Container restart > 3x  │                            │
│  │  → Pub/Sub → Cloud Function       │                            │
│  │  → Telegram notification          │                            │
│  └──────────────────────────────────┘                            │
└──────────────────────────────────────────────────────────────┘
                            ▲
                            │ API (靜態 IP 白名單)
                            ↓
                ┌──────────────────────┐
                │    Binance Futures    │
                │    (Testnet / Live)   │
                └──────────────────────┘
```

---

## 1. 前置準備

### 1.1 GCP 資源清單(建立前先確認)

| 資源 | 名稱規則 | 備註 |
|------|----------|------|
| Project ID | `binance-bot-prod`(正式)/ `binance-bot-dev`(測試) | 分開以免誤操作 |
| VM 名稱 | `bot-vm-prod` / `bot-vm-paper` | Paper 和 Live 用不同 VM |
| 區域 | `asia-east1-b`(台灣)或 `asia-northeast1-a`(東京) | 東京到幣安延遲較低 |
| 靜態 IP | `bot-static-ip-prod` | 綁定 VM,供幣安 API 白名單 |
| GCS Bucket | `<project-id>-bot-backup` | 備份用,單區域儲存 |
| Secret Manager | 見 §3.1 | 存 API key 等敏感資訊 |
| 服務帳戶 | `bot-sa@<project>.iam.gserviceaccount.com` | VM 使用的身份 |

### 1.2 成本估算(月)

| 項目 | 規格 | 預估(USD) |
|------|------|------------|
| VM | e2-small 常駐 | ~13 |
| Static IP | 綁定 VM | 0(綁定時免費) |
| Persistent Disk | 30GB 系統 + 50GB 資料 | ~4 |
| Cloud Storage | 10 GB 備份 | ~0.3 |
| Secret Manager | 4 secrets | ~0.2 |
| 網路出站 | < 10 GB/月 | ~0.5 |
| **合計** | | **~18 USD/月** |

如需降本,可只在 Paper 階段用 `e2-micro`(~7 USD/月),但 RAM 只有 1GB 較吃緊。

### 1.3 本機工具

```powershell
# Windows 上安裝
winget install Google.CloudSDK
winget install HashiCorp.Terraform  # 選用
winget install Docker.DockerDesktop  # 本機開發用

# 初始化
gcloud init
gcloud auth application-default login
```

---

## 2. 基礎設施建立

### 2.1 方案 A:Terraform(推薦,可重現)

```hcl
# deploy/terraform/main.tf 片段示意
provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

resource "google_compute_address" "bot_ip" {
  name = "bot-static-ip-${var.env}"
}

resource "google_compute_disk" "bot_data" {
  name = "bot-data-disk-${var.env}"
  size = 50
  type = "pd-balanced"
  zone = var.zone
}

resource "google_compute_instance" "bot_vm" {
  name         = "bot-vm-${var.env}"
  machine_type = var.machine_type
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
      size  = 30
    }
  }

  attached_disk {
    source      = google_compute_disk.bot_data.id
    device_name = "bot-data"
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.bot_ip.address
    }
  }

  service_account {
    email  = google_service_account.bot_sa.email
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = file("${path.module}/../scripts/bootstrap_vm.sh")

  tags = ["bot-vm"]
}

resource "google_storage_bucket" "backup" {
  name          = "${var.project_id}-bot-backup"
  location      = var.region
  force_destroy = false
  
  lifecycle_rule {
    condition { age = 90 }
    action    { type = "Delete" }
  }
}

resource "google_compute_firewall" "ssh" {
  name    = "bot-allow-ssh"
  network = "default"
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
  source_ranges = var.allowed_ssh_cidrs   # 你的 IP
  target_tags   = ["bot-vm"]
}

resource "google_compute_firewall" "dashboard" {
  name    = "bot-allow-dashboard"
  network = "default"
  allow {
    protocol = "tcp"
    ports    = ["8501"]
  }
  source_ranges = var.allowed_dashboard_cidrs  # 你的 IP
  target_tags   = ["bot-vm"]
}
```

```bash
cd deploy/terraform
terraform init
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

### 2.2 方案 B:gcloud CLI(快速)

```bash
# 設定變數
PROJECT_ID="binance-bot-prod"
ZONE="asia-northeast1-a"
VM_NAME="bot-vm-paper"

gcloud config set project $PROJECT_ID

# 建立靜態 IP
gcloud compute addresses create bot-static-ip --region=asia-northeast1

# 取得 IP
STATIC_IP=$(gcloud compute addresses describe bot-static-ip --region=asia-northeast1 --format='value(address)')
echo "Static IP: $STATIC_IP  ← 記下來,幣安 API 要加白名單"

# 建立資料磁碟
gcloud compute disks create bot-data-disk --size=50GB --type=pd-balanced --zone=$ZONE

# 建立 VM
gcloud compute instances create $VM_NAME \
  --zone=$ZONE \
  --machine-type=e2-small \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --disk=name=bot-data-disk,device-name=bot-data,mode=rw,boot=no \
  --address=$STATIC_IP \
  --tags=bot-vm \
  --service-account=bot-sa@$PROJECT_ID.iam.gserviceaccount.com \
  --scopes=cloud-platform

# 防火牆(限制來源 IP,換成你的)
YOUR_IP=$(curl -s ifconfig.me)/32
gcloud compute firewall-rules create bot-allow-ssh \
  --allow=tcp:22 --source-ranges=$YOUR_IP --target-tags=bot-vm

gcloud compute firewall-rules create bot-allow-dashboard \
  --allow=tcp:8501 --source-ranges=$YOUR_IP --target-tags=bot-vm

# 建立 GCS bucket
gsutil mb -l asia-northeast1 gs://$PROJECT_ID-bot-backup
```

---

## 3. Secret Manager 設定

### 3.1 建立 Secrets

```bash
# V7.4 testnet 先準備這 4 個（只需建立一次）
for name in binance-testnet-api-key binance-testnet-api-secret telegram-bot-token telegram-chat-id; do
  gcloud secrets create $name --replication-policy=automatic
done

# 若未來要切 live/mainnet，再額外建立：
# gcloud secrets create binance-api-key --replication-policy=automatic
# gcloud secrets create binance-api-secret --replication-policy=automatic

# 寫入值（每次更新）
echo -n "YOUR_BINANCE_TESTNET_API_KEY" | gcloud secrets versions add binance-testnet-api-key --data-file=-
echo -n "YOUR_BINANCE_TESTNET_API_SECRET" | gcloud secrets versions add binance-testnet-api-secret --data-file=-
echo -n "YOUR_TELEGRAM_TOKEN" | gcloud secrets versions add telegram-bot-token --data-file=-
echo -n "YOUR_CHAT_ID" | gcloud secrets versions add telegram-chat-id --data-file=-
```

預設 secret id 對應規則如下：

| Env var | 預設 Secret Manager id |
|---|---|
| `BINANCE_TESTNET_API_KEY` | `binance-testnet-api-key` |
| `BINANCE_TESTNET_API_SECRET` | `binance-testnet-api-secret` |
| `BINANCE_API_KEY` | `binance-api-key` |
| `BINANCE_API_SECRET` | `binance-api-secret` |
| `TELEGRAM_BOT_TOKEN` | `telegram-bot-token` |
| `TELEGRAM_CHAT_ID` | `telegram-chat-id` |

若你的 secret id 不想跟預設規則一致，可在 VM / systemd 設：

```bash
SECRET_NAME_FOR_BINANCE_TESTNET_API_KEY=my-custom-testnet-key
SECRET_NAME_FOR_BINANCE_TESTNET_API_SECRET=my-custom-testnet-secret
```

### 3.2 授權 VM 讀取

```bash
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:bot-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# GCS 備份權限
gsutil iam ch serviceAccount:bot-sa@$PROJECT_ID.iam.gserviceaccount.com:objectAdmin \
  gs://$PROJECT_ID-bot-backup
```

### 3.3 應用程式讀取範例

```python
from bot.config.env import get_secret

api_key = get_secret("BINANCE_TESTNET_API_KEY")
api_secret = get_secret("BINANCE_TESTNET_API_SECRET")
telegram_token = get_secret("TELEGRAM_BOT_TOKEN", required=False)
```

應用程式的實際行為是：

1. 若 env var 已存在，直接使用
2. 若設定了 `GOOGLE_CLOUD_PROJECT`，就用 env var 名稱推導 secret id（例如 `BINANCE_TESTNET_API_KEY` → `binance-testnet-api-key`）
3. 若有 `SECRET_NAME_FOR_<ENV_VAR>` override，優先使用 override 的 secret id
4. 成功讀到後會回填到 `os.environ`，讓後續需要 env 的程式碼也能沿用

---

## 4. VM 初始化

### 4.1 bootstrap_vm.sh(首次設定)

```bash
#!/bin/bash
# deploy/scripts/bootstrap_vm.sh
# 由 Terraform metadata_startup_script 或手動 SSH 後執行

set -e

# 1. 更新系統
apt-get update && apt-get upgrade -y
apt-get install -y ca-certificates curl gnupg lsb-release cron jq

# 2. 安裝 Docker
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 3. 格式化並掛載資料磁碟(只在首次)
DATA_DEV="/dev/disk/by-id/google-bot-data"
if ! blkid $DATA_DEV; then
  mkfs.ext4 -m 0 -E lazy_itable_init=0,lazy_journal_init=0,discard $DATA_DEV
fi
mkdir -p /data
if ! grep -q "/data" /etc/fstab; then
  echo "$DATA_DEV /data ext4 discard,defaults,nofail 0 2" >> /etc/fstab
fi
mount -a

# 4. 建立應用目錄
mkdir -p /data/bot /data/logs /data/backup_staging
chown -R 1000:1000 /data

# 5. 複製 systemd 服務(假設 repo 已 git clone 到 /opt/bot)
cp /opt/bot/deploy/systemd/bot.service /etc/systemd/system/
cp /opt/bot/deploy/systemd/backup.service /etc/systemd/system/
cp /opt/bot/deploy/systemd/backup.timer /etc/systemd/system/

systemctl daemon-reload
systemctl enable bot.service
systemctl enable backup.timer

echo "Bootstrap complete. Next: clone repo, set secrets, start bot.service"
```

### 4.2 systemd 服務檔案

```ini
# deploy/systemd/bot.service
[Unit]
Description=Binance Futures Bot
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/bot
Environment="GOOGLE_CLOUD_PROJECT=binance-bot-prod"
Environment="BOT_ENV=paper"
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml up -d
ExecStop=/usr/bin/docker compose -f docker-compose.prod.yml down
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```ini
# deploy/systemd/backup.timer
[Unit]
Description=Daily bot data backup to GCS

[Timer]
OnCalendar=*-*-* 03:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# deploy/systemd/backup.service
[Unit]
Description=Run bot backup script

[Service]
Type=oneshot
ExecStart=/opt/bot/deploy/scripts/backup.sh
```

---

## 5. Docker 配置

### 5.1 Dockerfile

```dockerfile
# 多階段建置,減少 image 大小
FROM python:3.11-slim AS builder

WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# --- 執行階段 ---
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && rm -rf /var/lib/apt/lists/*

# 非 root 使用者
RUN useradd -m -u 1000 bot
USER bot

WORKDIR /app
COPY --from=builder --chown=bot:bot /root/.local /home/bot/.local
ENV PATH=/home/bot/.local/bin:$PATH

COPY --chown=bot:bot src /app/src
COPY --chown=bot:bot config /app/config
COPY --chown=bot:bot scripts /app/scripts

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import bot.monitoring.health_check; bot.monitoring.health_check.check()" || exit 1

CMD ["python", "-m", "bot"]
```

### 5.2 docker-compose.prod.yml

```yaml
version: "3.9"

services:
  bot:
    image: gcr.io/${GOOGLE_CLOUD_PROJECT}/binance-bot:latest
    container_name: bot
    restart: unless-stopped
    environment:
      - GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT}
      - BOT_ENV=${BOT_ENV:-paper}
      - TZ=Asia/Taipei
    volumes:
      - /data/bot:/app/data
      - /data/logs:/app/logs
      - /var/run/docker.sock:/var/run/docker.sock:ro
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "5"
    deploy:
      resources:
        limits:
          memory: 1G

  dashboard:
    image: gcr.io/${GOOGLE_CLOUD_PROJECT}/binance-bot:latest
    container_name: dashboard
    restart: unless-stopped
    command: streamlit run src/bot/monitoring/dashboard/app.py --server.port 8501 --server.address 0.0.0.0
    ports:
      - "8501:8501"
    environment:
      - GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT}
      - BOT_ENV=${BOT_ENV:-paper}
    volumes:
      - /data/bot:/app/data:ro
      - /data/logs:/app/logs:ro
    depends_on:
      - bot
```

---

## 6. 備份策略

### 6.1 backup.sh

```bash
#!/bin/bash
# deploy/scripts/backup.sh
# 由 systemd backup.timer 每日 03:00 UTC 執行

set -e

PROJECT_ID=$(gcloud config get-value project)
BUCKET="gs://${PROJECT_ID}-bot-backup"
DATE=$(date +%Y%m%d)

# 資料備份(增量)
gsutil -m rsync -r -d /data/bot $BUCKET/data/latest/
gsutil -m rsync -r /data/logs $BUCKET/logs/$DATE/

# 每週壓縮快照(週日)
if [ $(date +%u) -eq 7 ]; then
  tar czf /tmp/weekly-$DATE.tar.gz -C /data bot logs
  gsutil cp /tmp/weekly-$DATE.tar.gz $BUCKET/weekly/
  rm /tmp/weekly-$DATE.tar.gz
fi

# 清理 30 天前的本機日誌
find /data/logs -name "*.log" -mtime +30 -delete

echo "Backup complete: $DATE"
```

### 6.2 還原流程

```bash
# 全量還原(災難復原)
gsutil -m rsync -r gs://$PROJECT_ID-bot-backup/data/latest/ /data/bot/

# 指定日期還原
gsutil cp gs://$PROJECT_ID-bot-backup/weekly/weekly-20260417.tar.gz /tmp/
tar xzf /tmp/weekly-20260417.tar.gz -C /data/
```

---

## 7. 部署流程

### 7.1 首次部署清單

```
[ ] 1. 建立 GCP Project
[ ] 2. 啟用必要 API: compute, secretmanager, storage, logging, monitoring
[ ] 3. 建立服務帳戶 bot-sa 並授權
[ ] 4. Terraform apply 或 gcloud 建立資源
[ ] 5. 記下靜態 IP → 幣安 API 設定白名單(Testnet 與 Live 分別設)
[ ] 6. Secret Manager 寫入 4 個 secrets
[ ] 7. SSH 進 VM → git clone repo 到 /opt/bot
[ ] 8. 執行 deploy/scripts/bootstrap_vm.sh
[ ] 9. 從本機 push Docker image 到 Artifact Registry / GCR
[ ] 10. SSH VM:docker compose pull && systemctl start bot
[ ] 11. 檢查 docker logs bot、systemctl status bot
[ ] 12. 開啟 Dashboard http://<static-ip>:8501
[ ] 13. 驗證 Telegram 通知
[ ] 14. 觸發一次手動備份驗證 GCS 寫入成功
[ ] 15. 驗證 systemd 重啟:sudo reboot 後確認自動恢復
```

### 7.2 日常更新流程

```bash
# 本機或 CI
export IMAGE_TAG=v74-testnet-$(git rev-parse --short HEAD)
export CONFIG_VERSION=v74-testnet-$(git rev-parse --short HEAD)
docker build -t gcr.io/$PROJECT_ID/binance-bot:$IMAGE_TAG .
docker push gcr.io/$PROJECT_ID/binance-bot:$IMAGE_TAG

# 發佈對應的 config bundle（範例）
# gsutil cp deploy/config/$CONFIG_VERSION.tar.gz gs://$BUCKET/config-bundles/

# SSH 進 VM
cd /opt/bot
export IMAGE_TAG=$IMAGE_TAG
export CONFIG_VERSION=$CONFIG_VERSION
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --remove-orphans

# 驗證
docker compose logs -f bot --tail=50
```

### 7.2A Testnet 資料回流閉環（V7.4 優先）

在 GCP VM 接上 Binance Testnet 後，**目標不是只讓 bot 跑起來，而是讓每次 paper run 都能自然回流成下一輪優化資料**。最低要求如下：

| 類別 | 必收資料 | 用途 |
|------|----------|------|
| 交易資料 | fills、paired trades、commission、realized PnL | 還原真實成交與策略績效 |
| 決策資料 | `signal_generated`、`signal_rejected`、reject reason、requested leverage | 分析策略是不是常被風控卡掉或訊號品質不佳 |
| 運行資料 | WS disconnect/reconnect、API latency、reconcile 次數與差異、risk halt、kill switch | 分析 runtime 問題是不是拖累策略 |
| 狀態資料 | equity snapshot、position snapshot、health snapshot | 做 rolling review 與異常定位 |
| 版本資料 | `run_id`、`deployment_id`、`config_fingerprint`、`git_sha`/image tag、version、environment | 確保回看資料時知道是「哪一版策略」產生的，且同版重啟仍可聚合 |

**建議 bundle 結構：**

```text
/data/review_bundles/{run_id}/
  paper_trades.db
  health.json
  logs.jsonl
  config.snapshot.yaml
  deploy_meta.json
  account_snapshot.json
```

**建議流程：**

1. 每次 deploy 產生新的 `deployment_id`；每次 restart 產生新的 `run_id`
2. Runtime 持續寫 journal / health / structured logs
3. 每日或每次停止時輸出 review bundle
4. 自動同步到 GCS，供本機或分析腳本拉回
5. 用 analyzer 腳本生成 per-strategy PnL、rolling Sharpe、reject summary、reconcile anomalies、runtime incident report

**關鍵原則：**
- 只存 trade journal 不夠；沒有 signal / reject / runtime 事件，就很難知道策略差還是工程差
- review bundle 必須是**版本可追溯**的，否則回頭優化時無法對應到實際 deploy 狀態

### 7.2B 策略更新 / 發布 / 回滾流程（避免把 GCP VM 弄壞）

**不要直接 SSH 到 VM 手改 code、YAML，或在 VM 上 `git pull` 應用程式碼。** 正確做法是讓 VM 永遠只吃「版本化產物」。

**推薦流程：**

1. 本機：修改策略 / config
2. 本機：跑 `--dry-run`、pytest、必要 backtest / regression
3. 建 image + config bundle，寫入 `git_sha` / image tag / config fingerprint
4. 部署到 **testnet VM**
5. 驗證 health gate：Secret Manager、WS、user data stream、reconcile、journal、health probe 全部正常
6. 觀察一段時間並匯出 review bundle
7. 判讀結果後決定保留、繼續觀察或 rollback

**Rollback 最低要求：**

- deploy 前自動備份當前 `paper_trades.db`、health snapshot、config snapshot
- 保留前一版 image tag 與 config bundle
- 提供固定 rollback 指令或腳本，不靠人工即席操作
- 如果 schema 改動可能破壞舊資料，先做 migration / backward compatibility 設計；不接受直接覆蓋 VM 上現有資料

**推薦 promotion gate（testnet 階段）**：

- 啟動 smoke / health 全綠
- journal 持續寫入正常
- reconcile 無異常膨脹
- signal reject rate / runtime incidents 在可接受範圍
- review bundle 可成功回拉並分析

### 7.3 緊急停止

```bash
# 遠端 Telegram:發送 /killswitch 指令(若已實作)

# 或 SSH:
ssh bot-vm-prod
sudo systemctl stop bot
# 程式進程內的 shutdown hook 會市價平倉所有部位

# 檢查倉位確實平倉
docker compose run --rm bot python scripts/kill_switch.py --verify
```

---

## 8. 監控與告警

### 8.1 Cloud Monitoring 告警規則

在 `deploy/monitoring/alert_policies.yaml` 定義,手動或透過 Terraform 建立:

| 告警 | 閾值 | 動作 |
|------|------|------|
| VM CPU | > 80% 持續 5 分鐘 | Telegram + Email |
| VM 磁碟 | 使用率 > 80% | Telegram |
| Docker 容器崩潰 | 5 分鐘內 restart ≥ 3 次 | Telegram 高優先 |
| Uptime Check(Dashboard) | 連續 2 次失敗 | Telegram |
| 自訂指標:當日虧損 | < -3% | Telegram 高優先(雙保險,應用層已熔斷) |

### 8.2 Log-based 指標

在 Cloud Logging 建立指標:

```
# 錯誤日誌計數
resource.type="gce_instance"
jsonPayload.level="ERROR"
  → metric: bot_error_count

# 成交事件計數
jsonPayload.event_type="FillEvent"
  → metric: bot_fill_count
```

---

## 9. 災難復原 (DR) 手冊

### 9.1 VM 無回應

```bash
# 1. 嘗試 SSH
gcloud compute ssh bot-vm-prod --zone=$ZONE

# 2. 不行就從 Console 重啟
gcloud compute instances reset bot-vm-prod --zone=$ZONE

# 3. 等待 systemd 自動恢復容器(約 2 分鐘)
# 4. 驗證:Telegram 應收到啟動通知
```

### 9.2 VM 永久毀損 / 區域故障

```bash
# 在新區域用 Terraform 重建
cd deploy/terraform
terraform apply -var="zone=asia-east1-b"

# 掛載資料磁碟(如果原磁碟可用就附加,否則從 GCS 還原)
# 新 IP 要重新到幣安 API 改白名單!!

# 從 GCS 還原
gsutil -m rsync -r gs://$PROJECT_ID-bot-backup/data/latest/ /data/bot/
systemctl start bot
```

### 9.3 API Key 外洩

```bash
# 1. 幣安後台立即刪除該 API key
# 2. 建立新 key,設定同樣權限(僅合約、無提現、IP 白名單)
# 3. 更新 Secret Manager
echo -n "NEW_API_KEY" | gcloud secrets versions add binance-api-key --data-file=-
# 4. 重啟容器讓新值生效
ssh bot-vm-prod "docker compose restart bot"
# 5. 檢查帳戶是否有異常活動
```

---

## 10. 常見問題

### Q1: 本機 Windows 可以跑嗎?
可以,但**僅限於開發與回測**。實盤必須放 GCP,本機遇到斷網、睡眠、停電都會害死持倉。本機跑時會自動 fallback 到 `.env`,不需要 GCP 認證。

### Q2: e2-small (2GB RAM) 夠嗎?
對 4 個策略、5-10 個標的、1m-4h 時間週期的情況,夠用。若之後加更多策略或跑 ML,升級到 `e2-medium` (4GB) 或 `e2-standard-2` (8GB)。

### Q3: 為什麼選東京不選台灣?
到幣安 API 的延遲:東京 ~20ms,台灣 ~40-60ms。對日線/4H 策略差異可忽略,對 1m 策略有感。如果只跑中長線,選台灣便宜又方便 SSH。

### Q4: 靜態 IP 會不會突然換?
不會,但要記得:
- VM 停止(非重啟)時會保留
- VM 刪除時如果 IP 未 reserve 會被回收
- 幣安 API 白名單只能加 IP 不能加域名,所以一定要用靜態 IP

### Q5: 備份要多久做一次?
- 交易資料 (`/data/bot`):每日增量
- 日誌 (`/data/logs`):每日完整
- 每週壓縮一次完整快照保留 90 天
- 重要:**把 .env.testnet / .env.prod 也備份到加密的地方**(不是 GCS bucket,是離線備份)

---

## 11. 安全檢查表(上線前必過)

```
[ ] 幣安 API key 關閉「提現」權限
[ ] 幣安 API key IP 白名單只有 GCP 靜態 IP
[ ] GCP VM 防火牆 SSH 限制來源 IP
[ ] Dashboard (8501) 限制來源 IP 或改走 IAP
[ ] Secret Manager 中無明碼 secret 備份在本機檔案
[ ] .env 檔案已加入 .gitignore 且 Git 歷史無洩漏(用 git log -p -- .env 檢查)
[ ] 服務帳戶權限最小化(Secret Accessor + Storage Object Admin + Logs Writer)
[ ] 啟用 Cloud Audit Logs 追蹤 Secret Manager 存取
[ ] Kill Switch Telegram 指令要求二次確認
[ ] run_live.py 啟動前輸入確認字串
```

---

**本文件為 v1.1 規格配套文件。異動請同步更新 SPEC.md。**
