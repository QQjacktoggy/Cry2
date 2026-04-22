# Cry2 TODO

## 已依目前 code 校正

這份 TODO 以目前 repo 內的實作為準，已排除舊版待辦與已完成但未更新的項目。

## 已完成

- GPT-5.4 review 第一波修正已落地
  - BacktestRunner 會下放 `symbol` / `timeframe`
  - Pair trading 缺 pair data 時直接 raise
  - `max_hold_bars` 已實作並補 regression tests
  - `.claude` 本地設定已移出版本控制
- Live 交易紀錄與分析鏈已接通
  - `TradeJournal`（SQLite fills / trades）
  - `TradeAnalyzer`
  - `reconcile_from_binance`
  - `run_live.py` / `run_paper.py` 已接 journal
- Live 韌性與觀測能力已接上
  - `UserDataStream`
  - WebSocket reconnect + periodic reconcile
  - `HealthStateWriter`
  - dashboard `5_health.py`
- V7.4 基礎接線已存在
  - `create_v74_strategies()`
  - `run_live.py` / `run_paper.py` 支援 `--version v74`
- V7.4 smoke validation 已補齊
  - `run_live.py` 新增 `--dry-run` / `--health-path`
  - `run_paper.py --dry-run` 改走共用 runtime smoke helper
  - 新增 `runtime_smoke.py` 與 `test_runtime_smoke.py`
- 槓桿差距已完成調查並收斂處理方式
  - `vectorbt` 的 `size_type="percent"` 在 `size > 1` 時不會形成真實 2x 曝險
  - `full_portfolio_backtest.py` 改成顯式 leverage model（return amplification）
  - `v74_leverage_optimization.py` 明確標示 simulated 2x leverage
- 文件已同步到目前 V7.4 狀態
  - `BACKTEST_REPORT_V7.md` 已補上 V7.4 smoke validation 結果
  - 已補充 leverage semantics 與回測模型說明
- V7.4 已完成一次完整重測
  - `full_portfolio_backtest.py` 已改成直接對齊 live `create_v74_strategies()` 的 16 策略位 / 混合 leverage 配置
  - `python -m backtest_tool.scripts.full_portfolio_backtest`：Sharpe 1.763 / MaxDD -17.7% / Final $543 / 5 of 5 checks passed
- 相關測試已補上或已存在
  - `test_trade_journal.py`
  - `test_trade_analyzer.py`
  - `test_user_data_stream.py`
  - `test_live_executor.py`
  - `backtest_tool/tests/test_regression.py`

## 目前真正待辦

### 1. V7.4 上 GCP VM 跑 Binance Testnet（最高優先）

依賴順序：先把 deploy / rollback 流程定義好（pre-VM gate），再做 runtime 接線與風控（第一批實作），最後接 testnet 時一併把資料回流補起來。

- **策略更新 / 部署 / 回滾流程（上 VM 前就定義好）**
  - GCP VM 上**不直接手改策略或 YAML**；只允許 versioned image + versioned config bundle 部署
  - 更新流程固定化：local dry-run → repo tests → backtest / regression → GCP testnet deploy → smoke / health gate → 觀察期 → 匯出 review bundle → 批准後保留或 rollback
  - deploy 前自動備份當前 journal / config / health，保留上一版 image 與 config，確保一鍵 rollback
  - 若策略 schema / journal schema 有異動，必須先定義 migration / backward compatibility；不接受直接把 VM 上的既有資料打壞

- **Runtime 接線與風險保護（第一批就做）**
  - 啟動前 preflight + 單實例保護：檢查 secrets、journal / health path 可寫、exchange connectivity、testnet 環境一致性，並補 single-instance guard，避免 deploy / restart 時同時跑兩個 bot
  - Secret Manager 真正接線：讓 `run_paper.py` / `run_live.py` 在 GCP 環境下真的透過 `bot.cloud.secret_manager` 讀取 secrets，不要只停在 `GOOGLE_CLOUD_PROJECT` 偵測後 early return
  - MaxDD / 風控處置語義補強：把 `config/risk_limits.yaml` 的 `max_drawdown_pct` / `drawdown_cooldown_bars` 真正傳進 `RiskManager`，並定義清楚「超過 MaxDD 後」的 runtime 行為（禁止新開倉、允許 reduce-only / exit、是否加 emergency flatten）
  - 機器可讀的 health probe / endpoint：補給 systemd / GCP uptime check / automation 用的 machine-readable health check（HTTP `/healthz` 或 CLI probe），至少涵蓋 market WS、user data stream、last reconcile、kill switch、circuit breaker、run id / uptime
  - Cloud Logging / Monitoring 真正接線：把既有 `bot.cloud.cloud_logging` / `bot.cloud.metrics` 接進 `run_paper.py` / `run_live.py`，上報 startup / shutdown、WS disconnect、reconcile、drawdown halt、kill switch、daily PnL

- **Testnet 資料回流閉環（接入 testnet 同批補上）**
  - 對 `TradeJournal` / health snapshot / 結構化 logs 補齊 `run_id`、`config_fingerprint`、`git_sha 或 image_tag`、`environment`、`version`
  - 除 fills / paired trades 外，還要持久化：`signal_generated`、`signal_rejected`、reconcile delta、API latency、WS disconnect / reconnect、daily equity snapshot、position snapshot、risk halt / kill switch 事件
  - 定義一個 review bundle：至少包含 `paper_trades.db`、health snapshot、structured logs、deploy metadata、config snapshot、account snapshot，固定回傳到 GCS 或可拉回本機分析
  - 補一條 paper review pipeline（腳本或報表）：輸出 per-strategy PnL、rolling Sharpe、勝率、slippage / fees、reject reasons、reconcile anomalies、runtime incidents，讓 testnet 資料能直接餵回優化流程

### 2. GCP VM 上線前 APP 補強（必補，延續）

- **Secret Manager 真正接線**
  - 讓 `run_live.py` / `run_paper.py` 在 GCP 環境下真的透過 `bot.cloud.secret_manager` 讀取 secrets
  - 不要只停在 `GOOGLE_CLOUD_PROJECT` 偵測後 early return
  - 明確定義 secret name ↔ env key 的對應與 fallback 行為

- **MaxDD / 風控處置語義補強**
  - 把 `config/risk_limits.yaml` 的 `max_drawdown_pct` / `drawdown_cooldown_bars` 真正傳進 live / paper `RiskManager`
  - 定義清楚「超過 MaxDD 後」的 runtime 行為：禁止新開倉、允許 reduce-only / exit
  - 決定是否需要再加一層更高門檻的 emergency flatten

- **機器可讀的 health probe / endpoint**
  - 補一個給 systemd / GCP uptime check / automation 用的 machine-readable health check（HTTP `/healthz` 或 CLI probe）
  - 至少涵蓋：market WS、user data stream、last reconcile、kill switch、circuit breaker、run id / uptime

- **Cloud Logging / Monitoring 真正接線**
  - 把既有 `bot.cloud.cloud_logging` / `bot.cloud.metrics` 接進 `run_live.py` / `run_paper.py`
  - 上報關鍵事件：startup / shutdown、WS disconnect、reconcile、drawdown halt、kill switch、daily PnL

- **啟動前 preflight + 單實例保護**
  - 啟動前檢查：secrets、journal / health path 可寫、exchange connectivity、live vs paper 環境一致性
  - 補 single-instance guard，避免 deploy / restart 時同時跑兩個 live bot
  - 補 restart / resume 摘要，讓 VM 重啟後可快速確認當前狀態

### 3. GCP VM 上線前 APP 補強（加分）

- restart / resume 後主動 Telegram 通知（含 open positions、reconcile 結果、啟動版本）
- 在 health snapshot / logs 補上 config fingerprint、run_id、boot time
- 決定 health check 採 HTTP endpoint 還是 CLI probe，並整理成 runbook

### 4. V8 版規劃與驗證前置（暫緩）

- **V8 SPEC 目標上修**
  - 以目前 V7.4 作為 control baseline；V8 planning target 先定為：年化 55-65%、Sharpe >= 2.2、MaxDD <= 18%
  - V8 必須靠「低相關新 sleeve」抬升組合品質，而不是只把既有方向性策略加大槓桿
  - 至少 2 個候選 sleeve 要通過 promotion gate，且與 core 的 30d PnL correlation < 0.5

- **V8 候選搭配矩陣（先做這 3 組）**
  - V8-A（low-risk pilot）：V7.4 core 93% + `lab_stat_arb_pairs` 5% + `lab_liquidation_hunter` 2%
  - V8-B（balanced target）：V7.4 core 89% + `lab_stat_arb_pairs` 8% + `lab_liquidation_hunter` 3%
  - V8-C（research upper bound）：V7.4 core 85% + `lab_stat_arb_pairs` 8% + `lab_stat_arb_pairs_bnb_btc` 4% + `lab_liquidation_hunter` 3%

- **V8 Phase 1（可立即落地）**
  - 補 `strategies_lab` 的 opt-in integration layer / runner，不改主線 default 行為
  - 只先整合 `lab_stat_arb_pairs`、`lab_stat_arb_pairs_bnb_btc`、`lab_liquidation_hunter`
  - Lab 策略共用主線 `RiskManager`，但套用 `strategies_lab/config/strategies_lab.yaml` 的更嚴 lab risk limits

- **V8 最佳化與驗證**
  - 跑 V7.4 / V8-A / V8-B / V8-C 對照回測（2022+）+ correlation summary
  - 對 pairs/liquidation 做參數與 allocation sweep；walk-forward / Monte Carlo 都要納入
  - Promotion gate：backtest 達到 Ann 55-65%、Sharpe >= 2.2、MaxDD <= 18%，且 paper 30 天滿足 Sharpe > 0.5、MaxDD < 5%、lab vs core correlation < 0.5

- **V8 Phase 2（先做 feasibility，不急著實作）**
  - `lab_perp_spot_basis`：需補 spot client + paired execution/rollback
  - `lab_cross_exchange_funding`：需補 multi-exchange connectivity + cross-venue margin/reconcile
  - `lab_btc_dominance_rotation`：需補 market-cap data client + daily scheduler

### 5. 最終 review

- 在上述項目穩定後，再做一次 GPT-5.4 final review
- 根據 review 結果整理下一輪 backlog

## 可補強 / 待確認

- `reconcile` helper 是否需要獨立、明確的 helper-level 測試檔
- 是否需要把 V7.4 的 smoke run 結果固化成更正式的回歸測試或 runbook

## 建議執行順序

1. 先把策略更新 / deploy / rollback 流程固定化，確保 VM 上出事有退路、不會手改 YAML
2. 再完成 V7.4 上 GCP VM 跑 Binance Testnet 的 runtime hardening（preflight / secrets / MaxDD / health / cloud logging）
3. 接 testnet 時同批補齊資料回流閉環（journal / logs / review bundle / analyzer）
4. Testnet 跑穩後，再回頭做 V8 或其他策略研究
5. 最後做 GPT-5.4 final review

## 驗證指令

```powershell
python -m ruff check .
python -m mypy src
PYTHONPATH=src python -m pytest tests/ -q
PYTHONPATH=src python -m pytest backtest_tool/tests/ -q
```
