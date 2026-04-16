# 專案架構 Tree — binance-futures-bot

**搭配文件**: SPEC.md v1.0
**用途**: 定義完整的目錄結構與檔案職責,開發時嚴格遵循。

---

## 完整目錄結構

```
binance-futures-bot/
│
├── README.md                           # 專案說明、快速開始
├── SPEC.md                             # 規格書(Single Source of Truth)
├── ARCHITECTURE.md                     # 架構樹(本檔)
├── CHANGELOG.md                        # 版本變更記錄
├── LICENSE
│
├── pyproject.toml                      # 專案配置、依賴、工具設定
├── requirements.txt                    # pip 依賴清單
├── requirements-dev.txt                # 開發依賴(pytest, black, mypy 等)
├── .python-version                     # 3.11+
├── .gitignore                          # 必含 .env、logs/、data/、*.pyc
├── .env.example                        # 範本(真實 .env 不進 Git)
├── .env                                # 正式環境變數(gitignored)
├── .env.testnet                        # Testnet 金鑰(gitignored)
│
├── Dockerfile                          # 容器化(多階段建置,含 healthcheck)
├── docker-compose.yml                  # 本地開發用(bot + dashboard)
├── docker-compose.prod.yml             # GCP VM 生產環境
├── .dockerignore
│
├── deploy/                             # 部署相關(GCP 專用)
│   ├── README.md                       # 部署步驟說明
│   ├── terraform/                      # IaC(選用,推薦)
│   │   ├── main.tf                     # VM、靜態 IP、防火牆、GCS bucket、Secret Manager
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   └── terraform.tfvars.example
│   ├── scripts/
│   │   ├── bootstrap_vm.sh             # 首次 VM 設定:裝 Docker、掛載 PD、設 systemd
│   │   ├── deploy.sh                   # 部署:pull image、docker-compose up
│   │   ├── backup.sh                   # 每日備份 /data /logs 至 GCS(cron 執行)
│   │   ├── restore.sh                  # 從 GCS 還原資料
│   │   ├── update.sh                   # 滾動更新(zero-downtime 盡力)
│   │   └── health_report.sh            # 每日健康報告(CPU、磁碟、容器狀態)
│   ├── systemd/
│   │   ├── bot.service                 # VM 開機自動啟動 docker-compose
│   │   └── backup.timer                # 每日 03:00 UTC 執行備份
│   │   └── backup.service              # backup.timer 觸發的 service
│   ├── secrets/
│   │   └── seed_secrets.sh             # 初次把 .env 內容寫入 Secret Manager
│   └── monitoring/
│       ├── uptime_check.yaml           # Cloud Monitoring uptime check 設定
│       └── alert_policies.yaml         # 告警規則(CPU、磁碟、容器崩潰)
│
├── config/                             # 配置檔案(YAML)
│   ├── config.yaml                     # 主配置(指向其他檔)
│   ├── strategies.yaml                 # 策略參數
│   ├── risk_limits.yaml                # 風控參數
│   ├── symbols.yaml                    # 交易標的清單
│   └── environments/                   # 環境特定配置
│       ├── backtest.yaml
│       ├── paper.yaml                  # Testnet
│       └── live.yaml                   # 實盤
│
├── src/
│   └── bot/                            # 主套件(import bot.xxx)
│       ├── __init__.py
│       ├── __main__.py                 # python -m bot 入口
│       │
│       ├── core/                       # 核心基礎設施
│       │   ├── __init__.py
│       │   ├── events.py               # 事件型別定義 (MarketEvent, SignalEvent, OrderEvent, FillEvent, FundingEvent, KillSwitchEvent)
│       │   ├── event_bus.py            # Event Bus 實作(pub/sub)
│       │   ├── clock.py                # 統一時間源(SimClock / RealClock)
│       │   ├── logger.py               # structlog 封裝
│       │   ├── exceptions.py           # 自訂例外類型
│       │   ├── constants.py            # 全域常數(OrderSide, TimeFrame 等)
│       │   └── types.py                # Pydantic models(Position, Order, Fill, ...)
│       │
│       ├── config/                     # 配置載入與驗證
│       │   ├── __init__.py
│       │   ├── loader.py               # YAML 載入
│       │   ├── schema.py               # Pydantic schema 驗證
│       │   └── env.py                  # 環境變數讀取(.env)
│       │
│       ├── data/                       # 資料層
│       │   ├── __init__.py
│       │   ├── fetcher.py              # 歷史資料下載(REST)
│       │   ├── storage.py              # Parquet 讀寫
│       │   ├── feed_base.py            # Data Feed 抽象介面
│       │   ├── feed_backtest.py        # 回測資料餵送器
│       │   ├── feed_live.py            # 實盤 WebSocket 餵送器
│       │   ├── funding_rate.py         # 資金費率歷史與即時
│       │   ├── symbol_info.py          # 合約規格(tick、min qty)
│       │   └── schemas.py              # 資料 schema 定義
│       │
│       ├── exchange/                   # 交易所封裝
│       │   ├── __init__.py
│       │   ├── binance_rest.py         # REST API 封裝
│       │   ├── binance_ws.py           # WebSocket 封裝
│       │   ├── order_manager.py        # 下單/撤單/改單
│       │   ├── account.py              # 持倉、餘額、槓桿
│       │   ├── rate_limiter.py         # API 限流保護
│       │   └── testnet.py              # Testnet 端點切換
│       │
│       ├── strategy/                   # 策略層
│       │   ├── __init__.py
│       │   ├── base.py                 # BaseStrategy 抽象類
│       │   ├── registry.py             # 策略註冊與動態載入
│       │   ├── funding_arb.py          # 策略 A:資金費率套利
│       │   ├── grid_futures.py         # 策略 B:合約網格
│       │   ├── trend_donchian.py       # 策略 C:趨勢跟隨
│       │   ├── mean_reversion_bb.py    # 策略 D:均值回歸
│       │   └── indicators/             # 策略專用指標
│       │       ├── __init__.py
│       │       ├── atr.py
│       │       ├── donchian.py
│       │       └── bollinger.py
│       │
│       ├── risk/                       # 風控層
│       │   ├── __init__.py
│       │   ├── position_sizer.py       # 倉位計算(fixed frac / ATR / Kelly)
│       │   ├── risk_manager.py         # 單筆/日/週檢查、熔斷
│       │   ├── liquidation_guard.py    # 強平距離監控
│       │   ├── kill_switch.py          # 緊急停止
│       │   └── circuit_breaker.py      # 黑天鵝熔斷
│       │
│       ├── execution/                  # 執行層
│       │   ├── __init__.py
│       │   ├── executor_base.py        # Executor 抽象介面
│       │   ├── executor_sim.py         # 模擬撮合(回測用)
│       │   ├── executor_live.py        # 實盤執行(送 REST)
│       │   ├── slippage.py             # 滑價模型
│       │   ├── fee_model.py            # 手續費模型
│       │   └── funding_model.py        # 資金費率結算模型
│       │
│       ├── portfolio/                  # 投組管理
│       │   ├── __init__.py
│       │   ├── portfolio.py            # 總投組狀態
│       │   ├── position.py             # 單一部位
│       │   ├── account_snapshot.py     # 每日快照
│       │   └── allocator.py            # 策略間資金分配
│       │
│       ├── backtest/                   # 回測
│       │   ├── __init__.py
│       │   ├── engine.py               # 事件驅動回測引擎
│       │   ├── metrics.py              # Sharpe/Sortino/MaxDD/Calmar/勝率/盈虧比
│       │   ├── report.py               # HTML 報告生成
│       │   ├── walk_forward.py         # Walk-Forward 分析
│       │   ├── monte_carlo.py          # 蒙地卡羅模擬
│       │   └── visualizer.py           # 權益曲線、回撤、熱力圖
│       │
│       ├── optimization/               # 參數優化
│       │   ├── __init__.py
│       │   ├── grid_search.py          # 網格搜尋
│       │   ├── optuna_tuner.py         # 貝葉斯優化
│       │   └── objectives.py           # 優化目標函數定義
│       │
│       ├── monitoring/                 # 監控與通知
│       │   ├── __init__.py
│       │   ├── telegram_notifier.py    # Telegram 推送
│       │   ├── dashboard/              # Streamlit Dashboard
│       │   │   ├── app.py              # 主頁
│       │   │   ├── pages/
│       │   │   │   ├── 1_overview.py
│       │   │   │   ├── 2_positions.py
│       │   │   │   ├── 3_trades.py
│       │   │   │   ├── 4_backtest.py
│       │   │   │   └── 5_health.py
│       │   │   └── components/
│       │   │       ├── equity_curve.py
│       │   │       └── positions_table.py
│       │   ├── health_check.py         # 心跳、API 延遲、WS 連線監測
│       │   └── alerts.py               # 告警規則引擎
│       │
│       ├── cloud/                      # GCP 整合(v1 新增)
│       │   ├── __init__.py
│       │   ├── secret_manager.py       # GCP Secret Manager 讀取封裝
│       │   ├── gcs_backup.py           # Cloud Storage 備份/還原
│       │   ├── cloud_logging.py        # Cloud Logging handler(整合 structlog)
│       │   └── metrics.py              # 自訂 Cloud Monitoring 指標上報
│       │
│       └── utils/                      # 工具
│           ├── __init__.py
│           ├── time_utils.py           # 時間轉換(ms/datetime/timezone)
│           ├── math_utils.py           # 精度處理、tick 對齊
│           ├── retry.py                # 重試裝飾器
│           └── id_generator.py         # 唯一 ID 生成(client_order_id)
│
├── scripts/                            # 可執行腳本(CLI 入口)
│   ├── download_history.py             # 下載歷史 K 線 + 資金費率
│   ├── run_backtest.py                 # 執行回測
│   ├── run_optimize.py                 # 參數優化
│   ├── run_paper.py                    # Testnet 模擬交易
│   ├── run_live.py                     # 實盤(需二次確認)
│   ├── run_dashboard.py                # 啟動 Streamlit Dashboard
│   ├── kill_switch.py                  # 緊急平倉工具
│   └── parity_check.py                 # 回測 vs 實盤一致性驗證
│
├── tests/                              # 測試
│   ├── __init__.py
│   ├── conftest.py                     # pytest fixtures
│   ├── unit/
│   │   ├── test_events.py
│   │   ├── test_event_bus.py
│   │   ├── test_position_sizer.py
│   │   ├── test_risk_manager.py
│   │   ├── test_kill_switch.py
│   │   ├── test_slippage.py
│   │   ├── test_fee_model.py
│   │   ├── test_funding_model.py
│   │   ├── test_storage.py
│   │   ├── test_indicators.py
│   │   └── strategies/
│   │       ├── test_funding_arb.py
│   │       ├── test_grid_futures.py
│   │       ├── test_trend_donchian.py
│   │       └── test_mean_reversion_bb.py
│   ├── integration/
│   │   ├── test_backtest_pipeline.py   # 資料 → 策略 → 風控 → 模擬執行
│   │   ├── test_config_loading.py
│   │   └── test_event_flow.py
│   ├── e2e/
│   │   ├── test_testnet_connect.py
│   │   └── test_parity.py              # 回測 vs 實盤一致性
│   └── fixtures/                       # 測試資料
│       ├── sample_klines.parquet
│       └── sample_funding.parquet
│
├── notebooks/                          # 研究用 Jupyter
│   ├── 01_data_exploration.ipynb
│   ├── 02_strategy_research.ipynb
│   ├── 03_backtest_analysis.ipynb
│   ├── 04_optimization_results.ipynb
│   └── 05_live_performance_review.ipynb
│
├── docs/                               # 文件
│   ├── getting_started.md              # 快速開始
│   ├── architecture.md                 # 架構詳解
│   ├── strategy_development.md         # 如何新增策略
│   ├── backtest_guide.md               # 回測使用指南
│   ├── deployment.md                   # 部署指南
│   ├── troubleshooting.md              # 常見問題
│   └── change_requests/                # 規格異動申請
│       └── .gitkeep
│
├── data/                               # 資料(gitignored)
│   ├── historical/
│   │   ├── klines/
│   │   │   └── {SYMBOL}/{TIMEFRAME}/{YEAR}.parquet
│   │   └── funding/
│   │       └── {SYMBOL}.parquet
│   ├── exchange_info/
│   │   └── {DATE}.json
│   └── backtest_results/
│       └── {RUN_ID}/
│           ├── config.yaml
│           ├── trades.parquet
│           ├── equity_curve.parquet
│           ├── metrics.json
│           └── report.html
│
├── logs/                               # 日誌(gitignored)
│   ├── app.log                         # 應用日誌(JSON)
│   ├── trades.log                      # 交易日誌
│   └── errors.log                      # 錯誤日誌
│
└── .github/                            # CI/CD(可選)
    └── workflows/
        ├── tests.yml                   # 自動測試
        └── lint.yml                    # 程式碼風格檢查
```

---

## 模組依賴關係

依賴方向**只能向下**,禁止反向依賴:

```
scripts/  (CLI 入口)
    ↓
monitoring/ ← backtest/ ← optimization/
    ↓           ↓
strategy/ ← portfolio/
    ↓
risk/
    ↓
execution/
    ↓
exchange/ ← data/
    ↓         ↓
core/ ← config/ ← utils/
```

**規則**:
- `core/`、`utils/`、`config/` 為最底層,不依賴任何業務模組
- `strategy/` **不得**直接呼叫 `exchange/`,必須透過 Event Bus 與 Executor
- `backtest/` **不得**依賴 `execution/executor_live`,只能用 `executor_sim`
- `monitoring/` 為旁路觀測,**不參與**決策流程

---

## 檔案職責速查表

### core/ — 基礎設施

| 檔案 | 職責 |
|------|------|
| `events.py` | 定義所有事件 dataclass/Pydantic model |
| `event_bus.py` | pub/sub 機制,支援同步與非同步訂閱 |
| `clock.py` | 統一時間源,回測用 SimClock,實盤用 RealClock |
| `logger.py` | structlog 配置,JSON 輸出,自動脫敏 |
| `exceptions.py` | 業務例外(OrderRejected、InsufficientMargin 等) |
| `types.py` | 核心 Pydantic models |

### data/ — 資料層

| 檔案 | 職責 |
|------|------|
| `fetcher.py` | 從 Binance REST 下載歷史資料 |
| `storage.py` | Parquet 讀寫、增量更新、去重 |
| `feed_backtest.py` | 從 Parquet 依時間順序產生 MarketEvent |
| `feed_live.py` | 訂閱 WebSocket,產生即時 MarketEvent |
| `funding_rate.py` | 資金費率資料源 |
| `symbol_info.py` | 合約規格快取(每日更新) |

### strategy/ — 策略層

| 檔案 | 職責 |
|------|------|
| `base.py` | BaseStrategy 定義 on_bar/on_fill/on_funding |
| `funding_arb.py` | 資金費率套利 |
| `grid_futures.py` | 合約網格 + 趨勢過濾 |
| `trend_donchian.py` | Donchian 突破 + ADX + ATR 止損 |
| `mean_reversion_bb.py` | Bollinger + RSI 反轉 |

### risk/ — 風控層

| 檔案 | 職責 |
|------|------|
| `position_sizer.py` | 根據風險模式計算部位大小 |
| `risk_manager.py` | 檢查單筆/日/週風險,可否放行訂單 |
| `liquidation_guard.py` | 監控維持保證金率 |
| `kill_switch.py` | Kill Switch 狀態機與觸發邏輯 |
| `circuit_breaker.py` | 黑天鵝熔斷(單根 K 線 > 5%) |

### execution/ — 執行層

| 檔案 | 職責 |
|------|------|
| `executor_sim.py` | 模擬撮合,計算滑價、手續費、資金費率、強平 |
| `executor_live.py` | 透過 `exchange/order_manager` 實際下單 |
| `slippage.py` | 滑價模型(固定 bps / 成交量加權) |
| `fee_model.py` | Maker/Taker 手續費 |
| `funding_model.py` | 資金費率結算邏輯 |

### backtest/ — 回測

| 檔案 | 職責 |
|------|------|
| `engine.py` | 事件驅動主迴圈 |
| `metrics.py` | 計算 Sharpe、MaxDD 等指標 |
| `report.py` | 產出 HTML 報告 |
| `walk_forward.py` | 滾動訓練/測試 |
| `monte_carlo.py` | 交易順序隨機打亂模擬 |

### monitoring/ — 監控

| 檔案 | 職責 |
|------|------|
| `telegram_notifier.py` | Telegram 推送,支援指令接收(遠端 Kill Switch) |
| `dashboard/app.py` | Streamlit 主頁 |
| `health_check.py` | WebSocket 心跳、API 延遲監測 |
| `alerts.py` | 告警規則引擎 |

### cloud/ — GCP 整合

| 檔案 | 職責 |
|------|------|
| `secret_manager.py` | 從 GCP Secret Manager 讀取 API key;本機自動 fallback 到 `.env` |
| `gcs_backup.py` | 使用 `gsutil rsync` 邏輯上傳 `/data` `/logs` 至 GCS |
| `cloud_logging.py` | structlog handler,同步雙寫本機檔案與 Cloud Logging |
| `metrics.py` | 上報自訂指標(當日 PnL、未成交單數、Kill Switch 狀態) |

### deploy/ — 部署腳本(專案根目錄下)

| 檔案 | 職責 |
|------|------|
| `terraform/main.tf` | 一鍵建立 VM、靜態 IP、防火牆、GCS bucket、Secret Manager |
| `scripts/bootstrap_vm.sh` | SSH 進 VM 後執行:裝 Docker、掛載 PD、設 systemd service |
| `scripts/deploy.sh` | `git pull && docker-compose pull && docker-compose up -d` |
| `scripts/backup.sh` | cron 每日呼叫,備份到 GCS |
| `systemd/bot.service` | VM 重啟後自動執行 docker-compose up |

### scripts/ — CLI 入口

| 腳本 | 用途 |
|------|------|
| `download_history.py` | `python scripts/download_history.py --symbols BTCUSDT ETHUSDT --start 2023-01-01` |
| `run_backtest.py` | `python scripts/run_backtest.py --strategy trend_donchian --start 2024-01-01 --end 2024-12-31` |
| `run_optimize.py` | `python scripts/run_optimize.py --strategy grid_futures --trials 100` |
| `run_paper.py` | `python scripts/run_paper.py --strategies all` |
| `run_live.py` | `python scripts/run_live.py --strategies funding_arb trend_donchian --confirm CONFIRM_LIVE_TRADING` |
| `run_dashboard.py` | `streamlit run src/bot/monitoring/dashboard/app.py` |
| `kill_switch.py` | `python scripts/kill_switch.py --close-all` |
| `parity_check.py` | `python scripts/parity_check.py --days 7` |

---

## 配置檔案範本預覽

### config/config.yaml (主配置)

```yaml
project: binance-futures-bot
version: "1.0"

environment: paper   # backtest | paper | live

includes:
  - strategies.yaml
  - risk_limits.yaml
  - symbols.yaml
  - environments/${environment}.yaml

paths:
  data_dir: ./data
  logs_dir: ./logs
  results_dir: ./data/backtest_results

logging:
  level: INFO
  format: json
  rotation: daily

telegram:
  enabled: true
  bot_token_env: TELEGRAM_BOT_TOKEN
  chat_id_env: TELEGRAM_CHAT_ID
```

### config/environments/paper.yaml

```yaml
exchange:
  name: binance
  mode: testnet
  base_url: https://testnet.binancefuture.com
  ws_url: wss://stream.binancefuture.com
  api_key_env: BINANCE_TESTNET_API_KEY
  api_secret_env: BINANCE_TESTNET_API_SECRET

execution:
  executor: live   # live executor 對到 testnet
  slippage_model: fixed_bps
  slippage_bps: 2

initial_capital: 10000   # USDT(Testnet 假資金)
```

### config/environments/backtest.yaml

```yaml
data_source: parquet
start_date: "2024-01-01"
end_date: "2025-12-31"

execution:
  executor: sim
  slippage_model: volume_weighted
  slippage_bps_base: 1
  fee_rate_maker: 0.0002
  fee_rate_taker: 0.0004

initial_capital: 10000
```

---

## `.gitignore` 必含項目

```
# Python
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.coverage
htmlcov/
dist/
build/
*.egg-info/

# Environments
.env
.env.*
!.env.example
venv/
.venv/

# Data & Logs
data/
logs/
*.log
*.parquet
!tests/fixtures/*.parquet

# IDE
.vscode/
.idea/
*.swp

# OS
.DS_Store
Thumbs.db

# Notebooks
.ipynb_checkpoints/
```

---

## 開發檢查清單

每次新增模組或功能時,確認:

- [ ] 對應位置放在 Tree 中正確的資料夾
- [ ] 遵循依賴方向規則(不反向依賴)
- [ ] 有對應的單元測試 (`tests/unit/`)
- [ ] 公開介面有 docstring
- [ ] 不直接呼叫跨層模組(透過 Event Bus 或介面)
- [ ] 配置參數放在 YAML,不硬編碼
- [ ] 日誌使用 structlog,不用 print
- [ ] 任何 IO / API 呼叫有例外處理與重試
- [ ] 敏感資訊不落日誌

---

**本架構樹為 v1.0 凍結版本。異動請依附錄 A(SPEC.md)規格異動流程進行。**
