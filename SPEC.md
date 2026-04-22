# 幣安合約量化交易系統 — 開發規格書

**專案代號**: `binance-futures-bot`
**版本**: v1.2 (V8 Planning Draft)
**日期**: 2026-04-22
**作者**: Jack
**文件性質**: 目標規格(Target Spec)— 後續開發以此為準,規格異動需版本化。

---

## 0. 規格使用守則

1. **本文件為 Single Source of Truth**,所有程式碼、測試、文件以此為準。
2. 任何需求異動,必須先修改本文件並標註版本(例:v1.1),再進入實作。
3. 每個章節的「驗收條件」為該模組完成的唯一判準;未滿足驗收條件不得進入下一階段。
4. 所有「MUST / SHOULD / MAY」遵循 RFC 2119 語意。

---

## 1. 專案目標與非目標

### 1.1 目標 (Goals)

| ID | 目標 | 量化指標 |
|----|------|----------|
| G1 | 建立可靠的幣安 USDT 永續合約自動交易系統 | Testnet 連續運行 ≥ 14 天無人為介入 |
| G2 | 支援歷史資料回測,回測邏輯與實盤邏輯共用 | 回測 vs Paper Trading 在相同訊號下成交價差 ≤ 0.1% |
| G3 | 風控優先,最大回撤可控 | 回測 MaxDD ≤ 20%,實盤熔斷機制有效觸發 |
| G4 | 多策略並行,資金分配可配置 | 至少 4 種策略可同時運行,互不干擾 |
| G5 | 可觀測性 | 所有交易有結構化日誌,Telegram 即時推送,Dashboard 可視化 |

### 1.1A V8 績效升級目標 (Planning Baseline)

| ID | 目標 | 量化指標 |
|----|------|----------|
| VG1 | 在不犧牲 deployability 下提升獲利 | 組合年化報酬 55-65% |
| VG2 | 提升風險調整後報酬 | Portfolio Sharpe ≥ 2.2, Calmar ≥ 3.0 |
| VG3 | 控制風險上限 | Backtest MaxDD ≤ 18%, hard ceiling = 20% |
| VG4 | 提升分散化品質 | 至少 2 個低相關 sleeve 進入候選組合,與 core 的 30d PnL correlation < 0.5 |
| VG5 | 升級需可驗證 | 至少 3 組 V8 候選組合完成 backtest / walk-forward / paper gate 比較 |

**註**：V7.4 繼續作為 control baseline；V8 在指標被證明前，一律維持 opt-in 候選狀態。

### 1.2 非目標 (Non-Goals)

- ❌ **不保證獲利**。本系統為交易執行與研究框架,績效取決於策略設計與市場狀況。
- ❌ **不做高頻交易 (HFT)**。最小時間粒度為 1 分鐘 K 線,不做 tick-level 搶單。
- ❌ **不做跨交易所套利**(v1 範圍內)。僅幣安單一交易所。
- ❌ **不做現貨交易**(除資金費率套利需要的對沖現貨部位)。
- ❌ **不做機器學習模型訓練**(v1 使用規則型策略;ML 列入 v2 考慮)。

### 1.3 成功判準 (Definition of Done for v1)

全部滿足才視為 v1 完成:

1. 全部 4 種策略在歷史資料上完成 Walk-Forward 回測,產出報告。
2. Testnet 連續運行 30 天,無崩潰、無未處理例外。
3. 風控熔斷機制經過人工觸發測試驗證。
4. Telegram 推送、Dashboard、日誌三者資訊一致。
5. 回測引擎與實盤引擎通過 Parity Test(詳見 §10.4)。
6. 小額實盤(< 100 USDT 倉位)運行 7 天,結果與預期一致。

### 1.3A V8 候選升級判準

1. V7.4 baseline 與 V8-A / V8-B / V8-C 必須使用相同資料區間、成本模型與 leverage semantics 比較。
2. V8 Phase 1 僅允許納入 `lab_stat_arb_pairs`、`lab_stat_arb_pairs_bnb_btc`、`lab_liquidation_hunter`；其餘 lab 策略需先完成資料/執行基建。
3. 任一 V8 candidate 若未同時達成年化 55-65%、Sharpe ≥ 2.2、MaxDD ≤ 18%，不得 promotion 到 paper。
4. Paper 30 天 promotion gate：lab sleeves trade 數 > 20、30d Sharpe > 0.5、單 sleeve MaxDD < 5%、lab vs core correlation < 0.5、0 次風控違規。
5. V8 live promotion 必須在 GCP deployment hardening 項完成後才可進行。

---

## 2. 功能需求 (Functional Requirements)

### 2.1 資料管理

| ID | 需求 | 優先級 |
|----|------|--------|
| FR-D-01 | 支援下載幣安永續合約歷史 K 線(1m、5m、15m、1h、4h、1d) | MUST |
| FR-D-02 | 支援下載資金費率歷史(每 8 小時) | MUST |
| FR-D-03 | 資料以 Parquet 格式儲存,支援增量更新 | MUST |
| FR-D-04 | 提供統一資料介面,回測與實盤共用 | MUST |
| FR-D-05 | 支援即時 WebSocket K 線串流 | MUST |
| FR-D-06 | WebSocket 斷線自動重連,重連後補齊缺失資料 | MUST |
| FR-D-07 | 支援 Order Book 深度資料(v1 僅記錄,不用於策略) | MAY |

### 2.2 策略

| ID | 需求 | 優先級 |
|----|------|--------|
| FR-S-01 | 策略基類定義標準介面:`on_bar`、`on_fill`、`on_funding` | MUST |
| FR-S-02 | 實作策略 A:資金費率套利(Funding Arbitrage) | MUST |
| FR-S-03 | 實作策略 B:合約網格(Futures Grid)含趨勢過濾 | MUST |
| FR-S-04 | 實作策略 C:趨勢跟隨(Donchian Breakout) | MUST |
| FR-S-05 | 實作策略 D:均值回歸(Bollinger + RSI) | MUST |
| FR-S-06 | 策略參數集中於 YAML,不硬編碼 | MUST |
| FR-S-07 | 多策略並行,各自獨立倉位與資金池 | MUST |
| FR-S-08 | 策略可熱啟停(不需重啟整個程式) | SHOULD |

### 2.2.1 V8 策略擴充範圍

- **Phase 1（立即可驗證）**：`lab_stat_arb_pairs`、`lab_stat_arb_pairs_bnb_btc`、`lab_liquidation_hunter`
- **Phase 2（基建前置）**：`lab_perp_spot_basis`、`lab_cross_exchange_funding`、`lab_btc_dominance_rotation`
- **整合守則**：
  - Lab 一律 opt-in，不得改寫主線 default registry/config
  - Lab 與主線共用同一個 `RiskManager`，但 Lab 使用更嚴的風控閾值
  - 若某個 lab sleeve 與主線相關性過高或 paper gate 未過，維持候選狀態，不進主線

### 2.3 執行

| ID | 需求 | 優先級 |
|----|------|--------|
| FR-E-01 | 統一下單介面,支援市價/限價/停損單 | MUST |
| FR-E-02 | 支援 Post-Only、Reduce-Only 旗標 | MUST |
| FR-E-03 | 下單前自動檢查 tick size、最小下單量、名目價值下限 | MUST |
| FR-E-04 | 下單失敗自動重試(最多 3 次,指數退避) | MUST |
| FR-E-05 | 撤單/改單支援 | MUST |
| FR-E-06 | 模擬執行器(回測用)需模擬手續費、滑價、資金費率、強平 | MUST |

### 2.4 風險控制

| ID | 需求 | 優先級 |
|----|------|--------|
| FR-R-01 | 單筆交易風險 ≤ 帳戶淨值 1%(可配置) | MUST |
| FR-R-02 | 單日最大虧損 ≤ 帳戶淨值 3% → 熔斷當日 | MUST |
| FR-R-03 | 單週最大虧損 ≤ 帳戶淨值 8% → 熔斷一週 | MUST |
| FR-R-04 | 最大槓桿限制(預設 3x,硬上限 5x) | MUST |
| FR-R-05 | 維持保證金率監控,< 50% 時自動減倉 | MUST |
| FR-R-06 | 單根 K 線波動 > 5% 時暫停新單 30 分鐘 | MUST |
| FR-R-07 | 全系統緊急停止按鈕(Kill Switch),一鍵平倉所有部位 | MUST |
| FR-R-08 | 每日帳戶快照(淨值、持倉、未實現損益) | MUST |

### 2.5 回測

| ID | 需求 | 優先級 |
|----|------|--------|
| FR-B-01 | 事件驅動回測引擎(非向量化) | MUST |
| FR-B-02 | 回測必須納入資金費率結算 | MUST |
| FR-B-03 | 回測必須納入手續費(Maker/Taker 差異) | MUST |
| FR-B-04 | 滑價模型可配置(固定 bps / 成交量加權) | MUST |
| FR-B-05 | 訊號產生於 K 線 close,成交於下一根 K 線 open(避免未來函數) | MUST |
| FR-B-06 | 輸出完整交易明細(CSV + Parquet) | MUST |
| FR-B-07 | 輸出績效指標:Sharpe、Sortino、MaxDD、Calmar、勝率、盈虧比、年化報酬 | MUST |
| FR-B-08 | 輸出權益曲線圖、回撤曲線圖、月度熱力圖 | MUST |
| FR-B-09 | Walk-Forward 分析(訓練 6 個月 / 測試 1 個月,滾動) | MUST |
| FR-B-10 | 蒙地卡羅模擬(交易順序隨機打亂 ≥ 1000 次) | SHOULD |
| FR-B-11 | 參數優化:Grid Search + Optuna(貝葉斯) | SHOULD |
| FR-B-12 | V8 候選組合比較矩陣（V7.4 / V8-A / V8-B / V8-C） | MUST |
| FR-B-13 | Low-correlation sleeves 必須輸出 correlation report 與 promotion gates 結果 | MUST |

### 2.6 監控與通知

| ID | 需求 | 優先級 |
|----|------|--------|
| FR-M-01 | 結構化日誌(JSON),分等級(DEBUG/INFO/WARN/ERROR) | MUST |
| FR-M-02 | Telegram 推送:下單、成交、熔斷、錯誤 | MUST |
| FR-M-03 | Streamlit Dashboard:即時持倉、PnL、近期交易 | MUST |
| FR-M-04 | Health Check:WebSocket 心跳、API 延遲 | MUST |
| FR-M-05 | 異常自動告警(連續錯誤 ≥ 5 次觸發高優先級通知) | MUST |

### 2.7 部署

**部署環境決策(已定)**: **GCP Compute Engine VM + Docker Compose**

| ID | 需求 | 優先級 |
|----|------|--------|
| FR-P-01 | Dockerfile 支援容器化部署 | MUST |
| FR-P-02 | docker-compose 一鍵啟動完整環境(bot + dashboard + watchtower) | MUST |
| FR-P-03 | 本機 Windows 支援(僅開發與回測,不做實盤) | MUST |
| FR-P-04 | 配置分層:Testnet / Paper / Live 明確區隔 | MUST |
| FR-P-05 | GCP VM 自動啟動 / 重啟恢復(systemd + docker restart policy) | MUST |
| FR-P-06 | 資料持久化至 GCP Persistent Disk,VM 重建不遺失 | MUST |
| FR-P-07 | 每日備份資料與日誌至 GCS (Google Cloud Storage) | MUST |
| FR-P-08 | Secret Manager 管理 API Key,不寫入 VM 磁碟 | MUST |
| FR-P-09 | Cloud Logging 整合結構化日誌 | SHOULD |
| FR-P-10 | Cloud Monitoring 告警(VM 健康、磁碟、CPU) | SHOULD |
| FR-P-11 | 靜態外部 IP + 幣安 API 白名單 | MUST |

### 2.7.1 GCP 部署規格

| 項目 | 規格 |
|------|------|
| 機器類型 | `e2-small` (2 vCPU, 2 GB RAM) — 回測階段可升級 `e2-standard-2` |
| 區域 | `asia-east1-b`(台灣)或 `asia-northeast1`(東京,離幣安近) |
| 作業系統 | Container-Optimized OS (COS) 或 Ubuntu 22.04 LTS |
| 磁碟 | 30 GB Balanced PD(系統)+ 獨立 50 GB PD 掛載 `/data` |
| 網路 | 固定外部 IP(預留靜態 IP) |
| 防火牆 | 只開 SSH (22)、Dashboard (8501,IP 白名單);不對外開其他 port |
| Secret 管理 | GCP Secret Manager(API keys、Telegram token) |
| 備份 | `gsutil rsync` 每日凌晨備份 `/data` 與 `/logs` 至 GCS bucket |
| 監控 | Cloud Monitoring agent + 自訂告警(詳見 §11) |

**建議分階段部署**:

| 階段 | 機器 | 用途 |
|------|------|------|
| 開發 | 本機 Windows | 寫 code、單元測試、快速回測 |
| 回測大量運算 | GCP `e2-standard-4` 臨時實例 | 長時間 Walk-Forward / Optuna 優化,跑完關機 |
| Paper Trading | GCP `e2-small` 常駐 | Testnet 30 天驗證 |
| Live | GCP `e2-small` 或 `e2-medium` 常駐 | 實盤 |

---

## 3. 非功能需求 (Non-Functional Requirements)

| ID | 需求 | 指標 |
|----|------|------|
| NFR-01 | 可靠性 | Testnet 連續運行 30 天,崩潰次數 = 0 |
| NFR-02 | 延遲 | 從訊號產生到下單送出 ≤ 500 ms |
| NFR-03 | 回測速度 | 1 年 1m K 線單策略回測 ≤ 5 分鐘 |
| NFR-04 | 可觀測性 | 任一筆交易可從日誌完整追溯訊號→下單→成交流程 |
| NFR-05 | 可測試性 | 核心模組單元測試覆蓋率 ≥ 70% |
| NFR-06 | 安全性 | API key 僅從環境變數載入,不落地到任何日誌 |
| NFR-07 | 可維護性 | 模組間透過事件/介面耦合,任一策略可獨立替換 |

---

## 4. 系統架構

### 4.1 分層架構

```
┌─────────────────────────────────────────────────────────┐
│                    監控層 (Monitoring)                     │
│          Telegram / Streamlit Dashboard / Logs              │
└─────────────────────────────────────────────────────────┘
                            ▲
┌─────────────────────────────────────────────────────────┐
│                     策略層 (Strategy)                      │
│    Funding Arb │ Grid │ Trend │ Mean Reversion             │
└─────────────────────────────────────────────────────────┘
                            ▲
┌─────────────────────────────────────────────────────────┐
│                     風控層 (Risk)                          │
│   Position Sizer │ Risk Manager │ Liquidation Guard        │
└─────────────────────────────────────────────────────────┘
                            ▲
┌─────────────────────────────────────────────────────────┐
│                  執行層 (Execution)                        │
│        Executor (Sim / Live)  │  Order Manager              │
└─────────────────────────────────────────────────────────┘
                            ▲
┌─────────────────────────────────────────────────────────┐
│                   資料層 (Data)                             │
│   Feed (Backtest/Live) │ Historical │ WebSocket             │
└─────────────────────────────────────────────────────────┘
                            ▲
┌─────────────────────────────────────────────────────────┐
│                交易所層 (Exchange)                          │
│              Binance REST / WebSocket                        │
└─────────────────────────────────────────────────────────┘
```

### 4.2 事件驅動模型

所有模組透過 Event Bus 通訊,事件類型:

| 事件 | 產生者 | 消費者 | 用途 |
|------|--------|--------|------|
| `MarketEvent` | Data Feed | Strategy | 新 K 線或 tick 到達 |
| `FundingEvent` | Data Feed | Strategy / Risk | 資金費率結算 |
| `SignalEvent` | Strategy | Risk Manager | 策略產生進出場訊號 |
| `OrderEvent` | Risk Manager | Executor | 風控通過後的下單指令 |
| `FillEvent` | Executor | Strategy / Portfolio | 成交回報 |
| `RejectEvent` | Executor | Strategy / Monitor | 下單被拒 |
| `LiquidationEvent` | Account Monitor | Risk Manager | 強平告警 |
| `KillSwitchEvent` | Risk Manager / User | 所有模組 | 緊急停機 |

### 4.3 回測與實盤差異點(唯二)

**關鍵設計原則**: 策略、風控、事件流程完全一致,只在兩處切換:

1. **Data Feed**: 回測讀 Parquet,實盤訂 WebSocket。
2. **Executor**: 回測用 `executor_sim`(模擬撮合),實盤用 `executor_live`(送 REST)。

其餘程式碼 100% 共用。這是確保回測與實盤邏輯一致的關鍵。

---

## 5. 交易標的

### 5.1 標的清單(可配置,預設值)

主流幣 5-10 檔,以流動性與資金費率穩定度篩選:

| 符號 | 用途 | 預設啟用 |
|------|------|----------|
| BTCUSDT | 主力 | ✅ |
| ETHUSDT | 主力 | ✅ |
| BNBUSDT | 次要 | ✅ |
| SOLUSDT | 次要 | ✅ |
| XRPUSDT | 次要 | ✅ |
| DOGEUSDT | 網格候選 | ⬜ |
| AVAXUSDT | 網格候選 | ⬜ |
| LINKUSDT | 趨勢候選 | ⬜ |
| ADAUSDT | 候選 | ⬜ |
| MATICUSDT | 候選 | ⬜ |

### 5.2 標的篩選規則(動態)

每週重新評估,符合以下全部條件才保留:

- 30 日平均成交量 ≥ 5 億 USDT
- 資金費率絕對值 30 日均值 ≤ 0.05%(避免費率過高成本)
- 最小下單價值 ≤ 10 USDT(便於小額測試)

---

## 6. 策略詳細規格

### 6.1 策略 A:資金費率套利 (Funding Arbitrage)

**代號**: `funding_arb`
**風險等級**: 低
**預期年化**: 5-15%(視市場情緒)

**原理**: 永續合約資金費率極端時,合約空單 + 現貨多單(或反向)對沖,賺費率。

**進場條件**:
- 資金費率年化 > 15% → 合約做空 + 現貨做多
- 資金費率年化 < -10% → 合約做多 + 現貨做空(較少見,通常只做正費率)

**出場條件**:
- 資金費率年化回落到 < 5%
- 持有超過 7 天強制平倉(避免長期暴險)

**倉位**:
- 單標的最大暴險:帳戶 20%
- 槓桿:1x(最保守)

**備註**:
- v1 先只實作「正費率套利」(合約空 + 現貨多)。
- 需要現貨帳戶與合約帳戶同步,**v1 只做合約側,現貨側手動**(簡化);v1.1 再自動化現貨。

### 6.2 策略 B:合約網格 (Futures Grid)

**代號**: `grid_futures`
**風險等級**: 中高(槓桿放大)
**預期年化**: 10-30%(震盪市),單邊市可能虧損

**參數**:
```yaml
grid_futures:
  symbols: [BTCUSDT, ETHUSDT]
  upper_price: auto  # 20 日高點 × 1.02
  lower_price: auto  # 20 日低點 × 0.98
  grid_count: 20
  grid_spacing: geometric  # arithmetic or geometric
  leverage: 2
  per_grid_size_pct: 1  # 每格佔帳戶 1%
  trend_filter:
    enabled: true
    ema_period: 200
    timeframe: 4h
    # EMA 向上只開多格、向下只開空格、盤整雙向
  stop_loss_atr_mult: 1.0  # 突破區間 ± 1 ATR 即停損重置
```

**核心邏輯**:
1. 計算 20 日區間作為網格邊界。
2. 在區間內等距(或等比)設置 20 格。
3. 根據 4H EMA200 趨勢決定方向偏好:
   - 多頭:只在區間下半部做多
   - 空頭:只在區間上半部做空
   - 盤整:雙向
4. 突破 ± 1 ATR 觸發停損,重新計算區間。

### 6.3 策略 C:趨勢跟隨 (Trend Following)

**代號**: `trend_donchian`
**風險等級**: 中
**預期年化**: 15-40%(趨勢年),震盪年可能 -10%

**參數**:
```yaml
trend_donchian:
  symbols: [BTCUSDT, ETHUSDT, SOLUSDT]
  timeframe: 4h
  entry_period: 20       # 20 根 K 線 Donchian 突破
  exit_period: 10        # 10 根 K 線反向出場
  adx_period: 14
  adx_threshold: 25      # ADX > 25 才進場
  atr_period: 14
  atr_stop_mult: 2.0     # 2×ATR 移動止損
  risk_per_trade_pct: 1  # 單筆風險 1%
  leverage: 2
```

**核心邏輯**:
1. 計算 20 日 Donchian 通道上下軌。
2. 突破上軌 + ADX > 25 → 做多;跌破下軌 + ADX > 25 → 做空。
3. 倉位 = (帳戶 × 1%) / (2 × ATR) × 槓桿。
4. 移動止損:多單 = max(目前止損, close - 2×ATR);空單反之。
5. 10 日 Donchian 反向即平倉。

### 6.4 策略 D:均值回歸 (Mean Reversion)

**代號**: `mean_reversion_bb`
**風險等級**: 中(需嚴格止損)
**預期年化**: 10-25%

**參數**:
```yaml
mean_reversion_bb:
  symbols: [BTCUSDT, ETHUSDT]
  timeframe: 1h
  bb_period: 20
  bb_std: 2.0
  rsi_period: 14
  rsi_oversold: 30
  rsi_overbought: 70
  volatility_filter:
    atr_percentile: 70   # 只在 ATR 高於 70 百分位時啟用(高波動才反轉)
  stop_loss_pct: 1.5     # 固定停損
  take_profit: bb_middle # 回到布林中軌出場
  leverage: 2
```

**核心邏輯**:
1. 價格觸及布林下軌 + RSI < 30 → 做多(反轉)。
2. 價格觸及布林上軌 + RSI > 70 → 做空。
3. 只在 ATR 位於 70 百分位以上(高波動)時啟用,低波動時關閉(避免趨勢市被輾)。
4. 停損:1.5% 固定止損(嚴格,因為「均值回歸失敗」就是趨勢啟動)。
5. 停利:回到布林中軌。

### 6.5 策略組合與資金分配

```yaml
capital_allocation:
  funding_arb:       60%  # 穩健打底
  trend_donchian:    25%  # 抓大行情
  grid_futures:      10%  # 震盪補貼
  mean_reversion_bb:  5%  # 小比例輔助
```

每個策略擁有獨立的虛擬資金池,互不影響。總風險在風控層彙總控制。

---

## 7. 風控規格

### 7.1 多層風控設計

```
Level 1 (策略層)     — 策略自身的進出場規則
    ↓
Level 2 (Position Sizer) — 單筆倉位計算
    ↓
Level 3 (Risk Manager)   — 單筆/單日/單週風險檢查
    ↓
Level 4 (Liquidation Guard) — 強平距離監控
    ↓
Level 5 (Kill Switch)    — 緊急停止
```

### 7.2 風控參數(預設)

```yaml
risk_limits:
  # 單筆
  max_risk_per_trade_pct: 1.0
  max_position_value_pct: 20.0    # 單標的最大倉位
  max_leverage: 3                  # 槓桿硬上限
  
  # 每日
  daily_loss_limit_pct: 3.0        # 當日虧損達此值 → 熔斷
  daily_trade_count_limit: 50      # 單日最多交易次數
  
  # 每週
  weekly_loss_limit_pct: 8.0       # 當週虧損達此值 → 熔斷
  
  # 保證金
  maintenance_margin_ratio_min: 50  # 維持保證金率 % 下限
  
  # 黑天鵝
  circuit_breaker_bar_pct: 5.0     # 單根 K 線 > 5% 暫停新單
  circuit_breaker_cooldown_min: 30
  
  # Kill Switch
  max_consecutive_errors: 5
  max_api_latency_ms: 3000
```

### 7.3 熔斷機制

| 觸發條件 | 動作 |
|----------|------|
| 單日虧損 ≥ 3% | 暫停新單當日,已有倉位正常止損,次日重置 |
| 單週虧損 ≥ 8% | 暫停新單一週,平倉所有倉位,人工介入檢討 |
| 維持保證金率 < 50% | 自動減倉 30%,並 Telegram 高優先告警 |
| 連續 API 錯誤 ≥ 5 次 | 暫停交易,等待人工處理 |
| WebSocket 斷線 > 60s | 暫停新單,嘗試重連,超過 5 分鐘未恢復則觸發 Kill Switch |
| 使用者手動 Kill Switch | 立即市價平倉所有部位,停止系統 |

---

## 8. 資料規格

### 8.1 資料來源

| 資料 | 來源 | 頻率 | 儲存格式 |
|------|------|------|----------|
| K 線 (Kline) | Binance REST `/fapi/v1/klines` + WebSocket `kline` | 1m/5m/15m/1h/4h/1d | Parquet |
| 資金費率 | `/fapi/v1/fundingRate` | 8h | Parquet |
| 合約規格 | `/fapi/v1/exchangeInfo` | 每日更新 | JSON |
| 帳戶資訊 | User Data Stream | 即時 | 記憶體 + 日誌 |
| 訂單簿 | WebSocket `depth`(僅記錄) | 即時 | Parquet (可選) |

### 8.2 資料目錄結構

```
data/
├── historical/
│   ├── klines/
│   │   ├── BTCUSDT/
│   │   │   ├── 1m/
│   │   │   │   ├── 2023.parquet
│   │   │   │   ├── 2024.parquet
│   │   │   │   └── 2025.parquet
│   │   │   ├── 4h/
│   │   │   └── 1d/
│   │   └── ETHUSDT/
│   └── funding/
│       ├── BTCUSDT.parquet
│       └── ETHUSDT.parquet
├── exchange_info/
│   └── 2026-04-17.json
└── backtest_results/
    └── {run_id}/
        ├── config.yaml
        ├── trades.parquet
        ├── equity_curve.parquet
        ├── metrics.json
        └── report.html
```

### 8.3 K 線資料 Schema

```
timestamp: int64 (ms)
open: float64
high: float64
low: float64
close: float64
volume: float64
quote_volume: float64
trade_count: int64
taker_buy_volume: float64
taker_buy_quote_volume: float64
```

---

## 9. API 與介面規格

### 9.1 策略基類介面

```python
class BaseStrategy(ABC):
    name: str
    symbols: list[str]
    timeframe: str
    
    @abstractmethod
    def on_bar(self, event: MarketEvent) -> list[SignalEvent]:
        """新 K 線到達時呼叫"""
    
    def on_fill(self, event: FillEvent) -> None:
        """成交回報"""
    
    def on_funding(self, event: FundingEvent) -> None:
        """資金費率結算"""
    
    def warmup_bars(self) -> int:
        """策略需要多少根 K 線才能開始產生訊號"""
```

### 9.2 執行器介面

```python
class BaseExecutor(ABC):
    @abstractmethod
    def submit_order(self, order: OrderEvent) -> str:
        """送單,回傳 order_id"""
    
    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        pass
    
    @abstractmethod
    def get_position(self, symbol: str) -> Position:
        pass
    
    @abstractmethod
    def get_balance(self) -> float:
        pass
```

### 9.3 配置檔案層次

```
config/
├── config.yaml              # 主配置,指向其他檔案
├── strategies.yaml          # 策略參數
├── risk_limits.yaml         # 風控參數
├── symbols.yaml             # 標的清單
└── environments/
    ├── backtest.yaml        # 回測專用
    ├── paper.yaml           # Testnet
    └── live.yaml            # 實盤
```

**環境切換透過 CLI 參數**: `python scripts/run_live.py --env paper`

---

## 10. 測試規格

### 10.1 單元測試

- 每個核心模組 ≥ 70% 覆蓋率
- 使用 `pytest`
- 風控模組要求 100% 覆蓋(關鍵路徑)

### 10.2 整合測試

- 策略 → 風控 → 模擬執行器 完整鏈路
- 事件流順序驗證
- 配置檔案載入驗證

### 10.3 回測測試

每個策略必須通過:
- 至少 2 年歷史資料回測
- Walk-Forward 分析(至少 12 次滾動)
- 蒙地卡羅 1000 次模擬
- 不同市場階段分段驗證(牛市/熊市/盤整)

### 10.4 Parity Test(回測 vs 實盤一致性)

**目的**: 驗證回測引擎與實盤引擎在相同輸入下產生相同輸出。

**方法**:
1. 取過去 7 天 Testnet 實際運行的 K 線與訊號
2. 用相同資料跑回測
3. 比對訊號產生時間、下單價格、成交結果

**驗收**: 訊號產生時間誤差 ≤ 1 秒,成交價差 ≤ 0.1%。

### 10.5 壓力測試

- WebSocket 斷線重連 100 次,無資料遺漏
- 模擬 API 限流,系統正確退避
- 模擬幣安維護(503),系統進入安全模式

---

## 11. 安全規格

### 11.1 基本安全

| 項目 | 規格 |
|------|------|
| API Key 儲存 | **生產環境**:GCP Secret Manager;**開發環境**:`.env`(gitignored) |
| API Key 權限 | 僅開啟「合約交易」,**關閉**「提現」 |
| IP 白名單 | 幣安 API 設定 IP 白名單(GCP 靜態外部 IP) |
| 日誌脫敏 | API Key、Secret 在日誌中必須遮蔽(自訂 structlog processor) |
| Git 保護 | `.env`、`logs/`、`data/` 加入 `.gitignore` |
| Kill Switch | 支援 Telegram 指令遠端觸發 |
| 二次確認 | `run_live.py` 啟動需輸入確認字串 |

### 11.2 GCP 安全規格

| 項目 | 規格 |
|------|------|
| Secret Manager | `binance-api-key`、`binance-api-secret`、`telegram-bot-token`、`telegram-chat-id` |
| IAM | VM 服務帳戶僅授予必要角色:`Secret Manager Secret Accessor`、`Logs Writer`、`Storage Object Admin`(限定 bucket) |
| SSH | 僅透過 `gcloud compute ssh` 或 IAP,禁用密碼登入 |
| 防火牆 | 僅允許 SSH (22) 自授權 IP;Dashboard (8501) 限開發者 IP;預設拒絕 |
| 磁碟加密 | GCP 預設啟用;Secret 不落盤到應用日誌 |
| VPC | 使用預設 VPC 即可;如有合規需求另建 Custom VPC |
| 審計 | 啟用 Cloud Audit Logs,追蹤 Secret Manager 存取 |

### 11.3 Secret 取用流程

```
程式啟動
  ↓
檢查 GOOGLE_CLOUD_PROJECT 環境變數
  ↓
  ├─ 有 → 從 Secret Manager 讀取(生產環境)
  └─ 無 → 從 .env 讀取(本機開發)
  ↓
載入到記憶體,不寫入任何檔案
  ↓
日誌 processor 自動遮蔽敏感欄位
```

---

## 12. 開發里程碑

| Phase | 期程 | 交付物 | 驗收 |
|-------|------|--------|------|
| **P0: 骨架** | Week 1 | 目錄結構、配置系統、事件匯流排、日誌 | 可跑 Hello World 事件流 |
| **P1: 資料層** | Week 2-3 | 歷史下載、Parquet 儲存、Feed 介面、WebSocket | 下載 BTC/ETH 2 年 1m 資料,WebSocket 穩定訂閱 |
| **P2: 回測引擎** | Week 4-5 | 事件驅動引擎、模擬執行器、績效指標 | 用簡單 EMA 策略回測通過,指標正確 |
| **P3: 風控** | Week 6 | Position Sizer、Risk Manager、Kill Switch | 熔斷機制單元測試 100% 通過 |
| **P4: 策略實作** | Week 7-10 | 4 種策略 + Walk-Forward | 每策略 2 年回測報告完成 |
| **P5: 實盤接入** | Week 11-12 | Binance REST/WS、Live Executor | 本機 Testnet 連續運行 7 天無錯誤 |
| **P6: 監控** | Week 13 | Telegram、Dashboard、Health Check | 所有告警路徑驗證通過 |
| **P6.5: GCP 部署** | Week 13-14 | Dockerfile、docker-compose、Terraform 腳本、GCS 備份 | VM 開機自動啟動,備份任務成功執行 |
| **P7: Paper Trading** | Week 15-18 | GCP 上 Testnet 長跑 | 連續 30 天穩定運行,零停機 |
| **P8: 小額實盤** | Week 19+ | 小額資金驗證 | 運行 7 天,結果符合預期 |

---

## 13. 技術棧

| 類別 | 選擇 | 備註 |
|------|------|------|
| 語言 | Python 3.11+ | |
| 資料處理 | pandas, numpy, pyarrow | Parquet 用 pyarrow |
| 技術指標 | pandas-ta | 比 TA-Lib 好裝 |
| 交易所 | python-binance | v1 先用這個,v2 可考慮 ccxt |
| 優化 | optuna | 貝葉斯超參搜尋 |
| 視覺化 | matplotlib, plotly, streamlit | Dashboard 用 Streamlit |
| 測試 | pytest, pytest-cov | |
| 日誌 | structlog | JSON 結構化 |
| 配置 | pyyaml, pydantic | Pydantic 做 schema 驗證 |
| 任務排程 | apscheduler | 資金費率檢查、每日結算 |
| WebSocket | websockets / python-binance 內建 | |
| 通知 | python-telegram-bot | |
| 容器化 | Docker, docker-compose | |
| 環境變數 | python-dotenv | |
| GCP SDK | google-cloud-secret-manager, google-cloud-storage, google-cloud-logging | 生產環境使用 |

---

## 13.1 GCP 部署技術棧

| 類別 | 選擇 | 備註 |
|------|------|------|
| 運算 | Compute Engine VM | `e2-small` 常駐,`e2-standard-4` 臨時回測 |
| 容器編排 | Docker Compose | 單機即可,不需要 GKE |
| 容器更新 | Watchtower 或手動 | v1 手動;v1.1 考慮自動 |
| Secret | Secret Manager | API key、Telegram token |
| 儲存 | Persistent Disk + Cloud Storage | PD 熱資料,GCS 冷備份 |
| 日誌 | Cloud Logging + 本機檔案 | 雙寫,本機 7 天保留 |
| 監控 | Cloud Monitoring | 自訂指標:未成交單數、當日 PnL |
| 告警通道 | Cloud Monitoring → Pub/Sub → Cloud Function → Telegram | 亦可直接 Email |
| 網路 | Static External IP + Firewall Rules | IP 白名單必備 |
| CI/CD | GitHub Actions → Artifact Registry → VM pull | v1.1 導入 |

---

## 14. 風險警示

1. **市場風險**: 合約交易可能全額虧損,甚至負債(雖然幣安有強平保護)。
2. **技術風險**: API 故障、WebSocket 斷線、伺服器當機皆可能造成未預期損失。
3. **過擬合風險**: 回測績效再好都不保證實盤獲利。Walk-Forward 與蒙地卡羅是必要但不充分條件。
4. **黑天鵝**: 極端行情下(如 2020/03、2022/05 LUNA)滑價與強平可能遠超模型預期。
5. **法律風險**: 使用者須自行確認在所在地區合法使用合約交易。

**本規格不構成任何投資建議。**

---

## 15. 版本歷史

| 版本 | 日期 | 變更 | 作者 |
|------|------|------|------|
| v1.0 | 2026-04-17 | 初版規格凍結 | Jack |
| v1.1 | 2026-04-17 | 部署環境鎖定 GCP;新增 §2.7.1、§11.2、§13.1、cloud/ 模組、deploy/ 目錄 | Jack |
| v1.2 | 2026-04-22 | 新增 V8 planning targets、候選升級判準、V8 策略 phase split 與回測比較矩陣要求 | Jack |

---

## 附錄 A:規格異動流程

1. 提出變更:在 `docs/change_requests/` 建立 `CR-YYYYMMDD-nn.md`
2. 影響評估:列出受影響模組、測試、里程碑
3. 批准後:更新本 SPEC.md,遞增版本號
4. 實作:以變更版本為準

## 附錄 B:術語表

| 術語 | 說明 |
|------|------|
| Perpetual Futures | 永續合約,無到期日的期貨 |
| Funding Rate | 資金費率,每 8 小時多空雙方互付的費用 |
| Liquidation | 強制平倉,保證金不足時交易所強制平倉 |
| Maintenance Margin | 維持保證金,帳戶最低保證金要求 |
| Post-Only | 僅掛單模式,避免被當 Taker 吃高手續費 |
| Reduce-Only | 僅減倉模式,避免反向開倉 |
| Walk-Forward | 向前分析,滾動訓練/測試以防過擬合 |
| Event-Driven | 事件驅動,每筆事件依序處理(vs 向量化批次) |
