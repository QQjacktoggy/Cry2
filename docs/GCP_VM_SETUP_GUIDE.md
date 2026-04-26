# GCP VM 部署教學 — cry2 Bot

> 記錄 2026-04-23 實際部署過程，含所有踩到的坑與解法。
> 2026-04-24 更新：VM 遷移至 instance-20260424-060848（35.194.254.115）。

---

## 環境資訊

| 項目 | 值 |
|------|-----|
| VM 名稱 | instance-20260424-060848 |
| External IP | 35.194.254.115 |
| Zone | asia-east1-b |
| 機型 | e2-micro (1 vCPU, 1GB RAM) |
| OS | Ubuntu (Debian-based) |
| 磁碟 | 10GB SSD |
| Python | 3.11-slim (Docker image) |

---

## 一、SSH 連線

```bash
gcloud compute ssh instance-20260424-060848 --zone=asia-east1-b
```

第一次連線會出現 host key 確認，輸入 `yes` 後後續不會再問。

---

## 二、必查項目（部署前確認）

### 1. 磁碟空間

```bash
df -h /
```

**坑：e2-micro 的 10GB 磁碟很容易滿。** 如果滿了，build 會失敗、容器無法啟動。

**清理方式：**

```bash
# 清除所有 Docker（危險！會刪所有 image/container）
docker system prune -af

# 清除 apt cache
sudo apt-get clean

# 清除 systemd journal
sudo journalctl --vacuum-size=50M
```

### 2. SWAP 確認

e2-micro 只有 1GB RAM，跑 Docker + Python 容易 OOM，需要 SWAP 支援。

```bash
# 確認 SWAP 狀態
cat /proc/swaps
swapon --show
grep swap /etc/fstab
```

**建立 2GB SWAP（若不存在）：**

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# 開機自動掛載
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 3. Docker Compose 版本

e2-micro 的系統 Docker 不一定包含 Compose v2，需確認：

```bash
docker compose version
```

**若顯示 command not found，手動安裝：**

```bash
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/download/v2.27.0/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version  # 確認
```

---

## 三、Repo 上傳到 VM

因為 SCP 只能傳檔案，若要上傳整個 repo：

```bash
# 方法一：在 VM 上 git clone（推薦）
gcloud compute ssh instance-20260424-060848 --zone=asia-east1-b --command="git clone <repo_url> /home/punktoggy/cry2"

# 方法二：從本機 SCP 上傳單一檔案
# 注意：/home/punktoggy/ 可能只有 punktoggy 有權限，先傳到自己的家目錄
gcloud compute scp myfile.txt instance-20260424-060848:/home/jack_shih/myfile.txt --zone=asia-east1-b

# 再 sudo cp 到目標位置
gcloud compute ssh instance-20260424-060848 --zone=asia-east1-b \
  --command="sudo cp /home/jack_shih/myfile.txt /home/punktoggy/cry2/myfile.txt"
```

**坑：直接 SCP 到 `/home/punktoggy/` 會 permission denied。** 一定要先傳到自己的 home，再 sudo cp。

---

## 四、.env 設定檔

`.env` 只能存在 VM 上（不可 commit 到 git）。

```bash
sudo tee /home/punktoggy/cry2/.env << 'EOF'
BINANCE_API_KEY=你的key
BINANCE_API_SECRET=你的secret
TELEGRAM_BOT_TOKEN=你的bot_token
TELEGRAM_CHAT_ID=你的chat_id
EOF
```

**坑：用 heredoc 透過 SSH 寫入時，值可能被清空。** 確認方式：

```bash
gcloud compute ssh instance-20260424-060848 --zone=asia-east1-b \
  --command="cat /home/punktoggy/cry2/.env"
```

如果值是空的，再次執行上面的 tee 指令。

---

## 五、Dockerfile 常見問題

### 問題 1：`pandas-ta` 無法安裝

`pandas-ta>=0.3.14b1` 在 Python 3.11 + pip 新版無法安裝。

**解法：** 確認 `pandas-ta` 是否真的有在程式碼裡 import。若沒有，直接從 `requirements.txt` 移除。

```bash
grep -r "pandas_ta\|pandas-ta\|import ta" src/ scripts/
```

### 問題 2：`backtest_tool` 模組找不到

```
ModuleNotFoundError: No module named 'backtest_tool'
```

原因是 Dockerfile 沒有 COPY `backtest_tool/` 目錄，且 PYTHONPATH 只有 `/app/src`。

**Dockerfile 修正：**

```dockerfile
COPY src/ src/
COPY config/ config/
COPY scripts/ scripts/
COPY backtest_tool/ backtest_tool/    # ← 必須加

ENV PYTHONPATH=/app/src:/app          # ← /app 讓 backtest_tool 可被找到
```

**docker-compose.yml 也要同步修正（否則 environment 會覆蓋 Dockerfile 的 ENV）：**

```yaml
services:
  bot:
    environment:
      - PYTHONPATH=/app/src:/app      # ← 必須與 Dockerfile 一致
```

### 問題 3：`vectorbt` 模組找不到

```
ModuleNotFoundError: No module named 'vectorbt'
```

`backtest_tool` 的 `engine/__init__.py` 和 `strategies/__init__.py` 在 import 時會引入所有 strategy，包括依賴 `vectorbt` 的策略，即使 bot 本身不需要。

**解法：在 `__init__.py` 加 try/except：**

`backtest_tool/engine/__init__.py`：
```python
from backtest_tool.engine.cost_model import CostModel
try:
    from backtest_tool.engine.param_scanner import ParamScanner
    from backtest_tool.engine.portfolio_optimizer import PortfolioOptimizer
    from backtest_tool.engine.runner import BacktestResult, BacktestRunner, MultiBacktestResult
except ImportError:
    ParamScanner = None
    PortfolioOptimizer = None
    BacktestResult = None
    BacktestRunner = None
    MultiBacktestResult = None
try:
    from backtest_tool.engine.regime import (...)
except ImportError:
    MarketRegimeDetector = None
    # ...
```

`backtest_tool/strategies/__init__.py`：
```python
try:
    from backtest_tool.strategies.arb_family import (...)
    # ... 所有 vbt 相關 import
    STRATEGY_MAP = {...}
except ImportError:
    STRATEGY_MAP = {}
```

修改後需要 **rebuild image**：

```bash
gcloud compute ssh instance-20260424-060848 --zone=asia-east1-b \
  --command="cd /home/punktoggy/cry2 && docker compose build bot 2>&1 | tail -20"
```

---

## 六、Paper Trading 設定（Mainnet Keys）

`scripts/run_paper.py` 預設用 testnet（`config/environments/paper.yaml`），需要 Binance Testnet API key。

若使用 Mainnet keys（真實帳號，小額資本模擬），修改 `paper.yaml`：

```yaml
exchange:
  name: binance
  mode: live
  base_url: https://fapi.binance.com       # mainnet futures
  ws_url: wss://fstream.binance.com
  api_key_env: BINANCE_API_KEY             # 對應 .env 的 key 名稱
  api_secret_env: BINANCE_API_SECRET

execution:
  executor: live
  slippage_model: fixed_bps
  slippage_bps: 2

initial_capital: 150
```

在 VM 上修改：
```bash
gcloud compute ssh instance-20260424-060848 --zone=asia-east1-b \
  --command="sudo nano /home/punktoggy/cry2/config/environments/paper.yaml"
```

---

## 七、啟動與驗證

```bash
# 啟動所有容器
gcloud compute ssh instance-20260424-060848 --zone=asia-east1-b \
  --command="cd /home/punktoggy/cry2 && docker compose up -d"

# 查看 bot logs
gcloud compute ssh instance-20260424-060848 --zone=asia-east1-b \
  --command="docker logs binance-bot --tail 40"

# 確認所有容器狀態
gcloud compute ssh instance-20260424-060848 --zone=asia-east1-b \
  --command="docker compose -f /home/punktoggy/cry2/docker-compose.yml ps"

# 確認重啟次數（應為 0）
gcloud compute ssh instance-20260424-060848 --zone=asia-east1-b \
  --command="docker inspect binance-bot --format='Restarts={{.RestartCount}} Status={{.State.Status}}'"
```

**正常啟動的 log 樣式：**
```
PREFLIGHT PASSED
✅ secret:BINANCE_API_KEY
✅ secret:BINANCE_API_SECRET
✅ writable:data/...
lifecycle.startup  capital=150.0  environment=paper  version=v72
starting_paper_trading  capital=150.0
```

---

## 八、Systemd 自動重啟（開機自啟）

```bash
sudo tee /etc/systemd/system/cry2-bot.service << 'EOF'
[Unit]
Description=Cry2 Binance Bot
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/punktoggy/cry2
ExecStart=/usr/local/lib/docker/cli-plugins/docker-compose up -d
ExecStop=/usr/local/lib/docker/cli-plugins/docker-compose down
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable cry2-bot.service
sudo systemctl status cry2-bot.service
```

---

## 九、除錯技巧

### 容器修改（不重 build，臨時 patch）

```bash
# 上傳檔案到 VM
gcloud compute scp myfile.py instance-20260424-060848:/home/jack_shih/myfile.py --zone=asia-east1-b

# 複製進容器（立即生效）
docker cp /home/jack_shih/myfile.py binance-bot:/app/path/to/myfile.py

# 重啟容器
docker restart binance-bot
```

⚠️ `docker cp` 的修改在 `docker compose up --force-recreate` 後會消失，需要 rebuild image 才能永久保留。

### 確認 image 內容

```bash
docker run --rm cry2-bot ls /app/
docker run --rm cry2-bot ls /app/backtest_tool/
```

### 確認容器實際環境變數

```bash
docker exec binance-bot env | grep BINANCE
docker exec binance-bot env | grep PYTHON
```

### 確認容器用的是哪個 image

```bash
docker inspect binance-bot --format='Image={{.Image}}'
docker images cry2-bot  # 比對 SHA
```

---

## 十、常用一鍵指令

```bash
# 完整狀態檢查
gcloud compute ssh instance-20260424-060848 --zone=asia-east1-b --command="
  echo '=== Disk ===' && df -h / &&
  echo '=== Swap ===' && free -h &&
  echo '=== Containers ===' && docker compose -f /home/punktoggy/cry2/docker-compose.yml ps &&
  echo '=== Bot logs ===' && docker logs binance-bot --tail 20 2>&1
"

# 重建並重啟 bot
gcloud compute ssh instance-20260424-060848 --zone=asia-east1-b --command="
  cd /home/punktoggy/cry2 &&
  docker compose build bot 2>&1 | tail -5 &&
  docker compose up -d --force-recreate bot
"
```
