# 幣安合約量化交易系統 — binance-futures-bot

> **版本 v1.0** | Python 3.11+ | Binance USDT 永續合約

自動化的幣安 USDT 永續合約量化交易框架，支援歷史回測、模擬交易（Testnet）與實盤交易，並提供 Telegram 即時推送和 Streamlit 監控儀表板。

---

## 目錄

- [功能特色](#功能特色)
- [系統需求](#系統需求)
- [快速開始](#快速開始)
- [環境設定](#環境設定)
- [配置說明](#配置說明)
- [使用方法](#使用方法)
  - [下載歷史資料](#1-下載歷史資料)
  - [執行回測](#2-執行回測)
  - [模擬交易（Paper Trading）](#3-模擬交易paper-trading)
  - [實盤交易](#4-實盤交易)
  - [啟動監控儀表板](#5-啟動監控儀表板)
  - [緊急停止](#6-緊急停止)
- [Docker 部署](#docker-部署)
- [策略說明](#策略說明)
- [風控機制](#風控機制)
- [監控與通知](#監控與通知)
- [GCP 雲端部署](#gcp-雲端部署)
- [開發指南](#開發指南)
- [重要聲明](#重要聲明)

---

## 功能特色

| 功能 | 說明 |
|------|------|
| **多策略並行** | 4 種策略同時運行，各自獨立倉位與資金池 |
| **回測引擎** | 模擬手續費、滑價、資金費率、強平，回測與實盤邏輯共用 |
| **風控熔斷** | 單日/單週虧損限制、槓桿上限、維持保證金監控 |
| **Telegram 推送** | 開倉/平倉/熔斷/系統事件即時通知 |
| **Streamlit 儀表板** | 即時查看資產曲線、持倉、日誌 |
| **Docker 化** | 一鍵啟動，支援 GCP 雲端部署 |
| **WebSocket 自動重連** | 斷線後自動補齊缺失 K 線資料 |

---

## 系統需求

- Python **3.11+**
- pip 23+
- （可選）Docker & Docker Compose
- 幣安帳戶及 API 金鑰（回測不需要；Paper Trading 需 Testnet 金鑰）

---

## 快速開始

```bash
# 1. 複製儲存庫
git clone https://github.com/QQjacktoggy/Cry2.git
cd Cry2

# 2. 建立虛擬環境
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. 安裝依賴
pip install -e ".[dev]"

# 4. 複製環境變數範本
cp .env.example .env
# 填入你的 API 金鑰（見「環境設定」）

# 5. 下載歷史資料（回測用）
python scripts/download_data.py --symbols BTCUSDT ETHUSDT --start 2024-01-01

# 6. 執行回測
python scripts/run_backtest.py --start 2024-01-01 --end 2024-12-31
```

---

## 環境設定

複製 `.env.example` 為 `.env` 並填入對應值：

```ini
# 幣安正式網路（實盤交易用）
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here

# 幣安 Testnet（模擬交易用）
# 申請：https://testnet.binancefuture.com/
BINANCE_TESTNET_API_KEY=your_testnet_api_key_here
BINANCE_TESTNET_API_SECRET=your_testnet_api_secret_here

# Telegram 推播（可選，關閉推播可留空）
# 申請：在 Telegram 找 @BotFather 建立 Bot
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here
```

> ⚠️ **安全提示**：`.env` 已加入 `.gitignore`，請勿將真實金鑰提交至 Git。

---

## 配置說明

所有配置位於 `config/` 目錄：

```
config/
├── config.yaml          # 主配置（環境、路徑、日誌、Telegram）
├── strategies.yaml      # 策略參數
├── risk_limits.yaml     # 風控參數
├── symbols.yaml         # 交易標的清單
└── environments/
    ├── backtest.yaml    # 回測環境
    ├── paper.yaml       # Testnet 模擬環境
    └── live.yaml        # 正式實盤環境
```

### `config/config.yaml` 主要選項

```yaml
environment: paper   # backtest | paper | live

paths:
  data_dir: ./data          # K 線資料儲存目錄
  logs_dir: ./logs          # 日誌目錄
  results_dir: ./data/backtest_results  # 回測報告目錄

logging:
  level: INFO   # DEBUG | INFO | WARNING | ERROR

telegram:
  enabled: true
```

### `config/strategies.yaml` 策略開關

每個策略可透過 `enabled: true/false` 單獨開啟或關閉，並可調整所有參數（無需修改程式碼）。

---

## 使用方法

### 1. 下載歷史資料

```bash
# 下載預設標的（BTCUSDT、ETHUSDT）的多個時間框架
python scripts/download_data.py

# 自訂標的、時間框架與起始日期
python scripts/download_data.py \
  --symbols BTCUSDT ETHUSDT SOLUSDT \
  --timeframes 1m 4h 1d \
  --start 2023-01-01

# 同時下載資金費率歷史（資金費率套利策略需要）
python scripts/download_data.py --funding

# 指定資料儲存目錄
python scripts/download_data.py --data-dir ./data
```

**參數說明：**

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `--symbols` | `BTCUSDT ETHUSDT` | 交易對清單 |
| `--timeframes` | `1m 4h 1d` | K 線週期（`1m`、`5m`、`15m`、`1h`、`4h`、`1d`） |
| `--start` | `2024-01-01` | 起始日期（YYYY-MM-DD） |
| `--end` | 當前時間 | 結束日期 |
| `--funding` | 不下載 | 是否額外下載資金費率 |
| `--data-dir` | `./data` | 資料儲存路徑 |

---

### 2. 執行回測

```bash
# 使用預設配置執行回測（2024-01-01 ~ 2025-12-31）
python scripts/run_backtest.py

# 自訂時間段
python scripts/run_backtest.py --start 2024-01-01 --end 2024-06-30

# 只回測單一策略
python scripts/run_backtest.py --strategy trend_donchian

# 指定配置檔
python scripts/run_backtest.py --config config/config.yaml
```

**可用策略名稱：**`funding_arb`、`grid_futures`、`trend_donchian`、`mean_reversion_bb`

**回測輸出範例：**

```
============================================================
BACKTEST RESULTS
============================================================
Run ID:           bt_20240101_20241231_abc123
Period:           2024-01-01 → 2024-12-31
Symbols:          BTCUSDT, ETHUSDT
Initial Capital:  10000.00 USDT
Final Equity:     12543.21 USDT
Total Return:     25.43%
Ann. Return:      25.43%
Sharpe Ratio:     1.87
Max Drawdown:     -12.34%
Win Rate:         54.2%
Profit Factor:    1.65
Total Trades:     312
============================================================
Report: ./data/backtest_results/bt_20240101_20241231_abc123.html
```

HTML 報告會自動生成至 `data/backtest_results/` 目錄。

---

### 3. 模擬交易（Paper Trading）

模擬交易連接 Binance **Testnet**，使用真實市場資料但不涉及真實資金。

**前置步驟：**
1. 至 [Binance Futures Testnet](https://testnet.binancefuture.com/) 申請帳號並取得 API 金鑰
2. 填入 `.env` 的 `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET`

```bash
# 啟動模擬交易
python scripts/run_paper.py

# 指定配置檔
python scripts/run_paper.py --config config/config.yaml
```

以 `Ctrl+C` 或 `SIGTERM` 優雅停止，系統會自動發送 Telegram 通知。

---

### 4. 實盤交易

> ⚠️ **警告**：實盤交易涉及真實資金，請務必先在 Testnet 充分測試，確認策略表現後再啟用。

**前置步驟：**
1. 填入 `.env` 的 `BINANCE_API_KEY` / `BINANCE_API_SECRET`
2. 確認幣安帳戶已開通合約交易功能
3. 建議設定 API 金鑰的 IP 白名單
4. 調整 `config/environments/live.yaml` 的初始資金與策略參數

```bash
# 啟動實盤交易
python scripts/run_live.py

# 指定配置檔
python scripts/run_live.py --config config/config.yaml
```

---

### 5. 啟動監控儀表板

```bash
streamlit run src/bot/monitoring/dashboard/app.py --server.port 8501
```

瀏覽器開啟 `http://localhost:8501` 即可查看：

- 即時資產曲線
- 當前持倉與未實現損益
- 策略績效統計
- 系統日誌

---

### 6. 緊急停止

```bash
# 觸發 Kill Switch（立即平倉所有部位並停止交易）
python scripts/kill_switch.py
```

Kill Switch 觸發後，系統將：
1. 停止接受新訊號
2. 嘗試平掉所有持倉
3. 發送 Telegram 通知
4. 記錄完整日誌

---

## Docker 部署

使用 Docker Compose 一鍵啟動 Bot + Dashboard：

```bash
# 複製並填寫環境變數
cp .env.example .env
# 編輯 .env...

# 啟動所有服務（背景執行）
docker-compose up -d

# 查看日誌
docker-compose logs -f bot
docker-compose logs -f dashboard

# 停止所有服務
docker-compose down
```

**服務說明：**

| 服務 | 說明 | 連接埠 |
|------|------|--------|
| `bot` | 主交易程式（預設執行 paper trading） | — |
| `dashboard` | Streamlit 監控儀表板 | `8501` |
| `watchtower` | 自動更新 Docker 映像（每小時檢查） | — |

修改 `docker-compose.yml` 中 `bot` 服務的 `command` 可切換執行模式：

```yaml
# Paper Trading（預設）
command: python scripts/run_paper.py

# 實盤交易
command: python scripts/run_live.py
```

---

## 策略說明

所有策略參數集中於 `config/strategies.yaml`，修改後重啟生效。

### 策略 A：資金費率套利（Funding Arbitrage）

利用資金費率高於閾值時做多/做空，並以現貨對沖，賺取資金費率差。

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `funding_rate_threshold_annual` | `15.0` | 進場閾值（年化 %） |
| `funding_rate_exit_annual` | `5.0` | 出場閾值（年化 %） |
| `max_hold_days` | `7` | 最長持倉天數 |
| `leverage` | `1` | 槓桿倍數 |

### 策略 B：合約網格（Futures Grid）

在設定區間內建立等間距網格，趨勢過濾器確保順勢操作。

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `grid_count` | `20` | 網格數量 |
| `grid_spacing` | `geometric` | 間距模式（`arithmetic`/`geometric`） |
| `trend_filter.ema_period` | `200` | 趨勢 EMA 週期 |
| `leverage` | `2` | 槓桿倍數 |

### 策略 C：趨勢跟隨（Donchian Breakout）

以唐奇安通道突破為進場訊號，ADX 過濾弱勢行情，ATR 設定止損。

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `entry_period` | `20` | 進場通道週期 |
| `exit_period` | `10` | 出場通道週期 |
| `adx_threshold` | `25` | ADX 強勢門檻 |
| `atr_stop_mult` | `2.0` | ATR 止損倍數 |
| `leverage` | `2` | 槓桿倍數 |

### 策略 D：均值回歸（Bollinger + RSI）

布林通道 + RSI 超賣/超買訊號，波動率過濾器避免高波動期間進場。

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `bb_period` | `20` | 布林通道週期 |
| `bb_std` | `2.0` | 標準差倍數 |
| `rsi_oversold` | `30` | RSI 超賣閾值 |
| `rsi_overbought` | `70` | RSI 超買閾值 |
| `leverage` | `2` | 槓桿倍數 |

### 資金分配

```yaml
capital_allocation:
  funding_arb:       0.60   # 60%
  trend_donchian:    0.25   # 25%
  grid_futures:      0.10   # 10%
  mean_reversion_bb: 0.05   #  5%
```

---

## 風控機制

所有風控參數集中於 `config/risk_limits.yaml`。

| 風控項目 | 預設值 | 觸發行為 |
|----------|--------|----------|
| 單筆交易最大風險 | 帳戶淨值 1% | 拒絕下單 |
| 單日最大虧損 | 帳戶淨值 3% | 熔斷當日 |
| 單週最大虧損 | 帳戶淨值 8% | 熔斷一週 |
| 最大槓桿（預設/硬上限） | 3x / 5x | 拒絕下單 |
| 維持保證金率 | < 50% | 自動減倉 30% |
| 單根 K 線波動 | > 5% | 暫停新單 30 分鐘 |
| API 錯誤次數 | 連續 5 次 | 觸發 Kill Switch |

---

## 監控與通知

### Telegram 通知事件

- ✅ 系統啟動/停止
- 📈 開倉訊號（策略名稱、方向、數量、進場價）
- 📉 平倉（損益、持倉時間）
- 🔴 熔斷/Kill Switch 觸發
- ⚠️ 異常警告（連線中斷、API 錯誤）

### 結構化日誌

日誌以 JSON 格式儲存於 `logs/` 目錄，每日輪轉，保留 7 天。

```bash
# 即時查看日誌
tail -f logs/bot.log | python -m json.tool
```

---

## GCP 雲端部署

詳細步驟請參閱 [DEPLOYMENT_GCP.md](DEPLOYMENT_GCP.md)。

**快速部署概覽：**

```bash
# 1. 在 GCP 建立 VM（e2-small，東京區域建議）
# 2. 安裝 Docker 並設定 systemd 服務
bash deploy/scripts/bootstrap_vm.sh

# 3. 設定 Secret Manager（存放 API 金鑰）
bash deploy/secrets/seed_secrets.sh

# 4. 部署 Docker Compose
bash deploy/scripts/deploy.sh

# 5. 設定每日自動備份至 GCS
#（systemd timer 已包含在 bootstrap 腳本中）
```

---

## 開發指南

### 安裝開發依賴

```bash
pip install -e ".[dev]"
```

### 執行測試

```bash
# 執行全部測試
PYTHONPATH=src python -m pytest tests/ -q

# 執行特定測試檔
PYTHONPATH=src python -m pytest tests/test_risk_manager.py -v
```

### 程式碼檢查

```bash
# Linter（ruff）
python -m ruff check .

# 型別檢查（mypy）
python -m mypy src

# 自動格式化
python -m black src tests
```

### 新增自訂策略

1. 在 `src/bot/strategy/` 建立新策略類別，繼承 `BaseStrategy`
2. 實作 `on_bar`、`on_fill`、`on_funding` 方法
3. 在 `src/bot/strategy/registry.py` 的 `register_default_strategies()` 中註冊
4. 在 `config/strategies.yaml` 中新增策略參數區塊

---

## 重要聲明

> ⚠️ **本系統不保證獲利。** 加密貨幣合約交易具有高度風險，可能損失全部本金。策略績效取決於市場狀況，歷史回測結果不代表未來表現。

> 🚫 **請勿在未充分測試前使用實盤模式。** 建議流程：回測 → Testnet 模擬 → 小額實盤（< 100 USDT）→ 正式實盤。

---

## 相關文件

- [SPEC.md](SPEC.md) — 完整開發規格書（Single Source of Truth）
- [ARCHITECTURE.md](ARCHITECTURE.md) — 專案架構與目錄結構
- [DEPLOYMENT_GCP.md](DEPLOYMENT_GCP.md) — GCP 雲端部署指南
