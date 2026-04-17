# VectorBT 回測工具箱 — 規格書

**專案代號**: `backtest_tool`
**版本**: v1.0
**日期**: 2026-04-17
**母專案**: `binance-futures-bot` (Cry2)
**本文件為回測工具箱的 Single Source of Truth**

---

## 0. 文件使用守則

1. 本文件定義回測工具箱的完整目標、架構與驗收條件。
2. 所有程式碼以此為準，異動需版本化（v1.1、v1.2…）。
3. `MUST / SHOULD / MAY` 遵循 RFC 2119 語意。
4. 本工具箱 **獨立於母專案運行**，但可讀取母專案的策略定義與歷史資料。

---

## 1. 專案目標

### 1.1 核心目標

| ID | 目標 | 量化指標 |
|----|------|----------|
| G1 | 使用 VectorBT 向量化回測，速度比母專案 event-driven 引擎快 10x+ | 同等資料量下回測 < 10 秒 |
| G2 | 支援母專案 4 種策略的向量化等效實作 | 回測結果與母專案引擎相差 < 5% |
| G3 | 大規模參數掃描與最佳組合搜尋 | 單次可掃描 1,000+ 參數組合 |
| G4 | 多策略資金配置最佳化 | 找出 Sharpe 最大化的配置比例 |
| G5 | 建立獨立資料庫，可匯入、追蹤、查詢歷史資料 | 有 DATA_CATALOG.md 記錄所有資料集 |
| G6 | 產出專業 HTML 報告，含完整交易明細與獲利分析 | 報告含 20+ 指標、逐筆交易、視覺化圖表 |

### 1.2 非目標

- ❌ 不取代母專案 event-driven 引擎（本工具為研究輔助）
- ❌ 不做即時交易（純回測工具）
- ❌ 不做 ML/AI 模型訓練（v1 限規則型策略）

### 1.3 成功判準

1. 4 種策略皆可在 VectorBT 上回測並產出報告
2. 參數掃描可在 < 5 分鐘內完成 1,000 組參數
3. 組合最佳化可輸出最佳資金配置
4. 所有報告為自包含 HTML（可離線閱讀）
5. 資料庫有完整 catalog 與校驗

---

## 2. 架構總覽

### 2.1 系統分層

```
┌─────────────────────────────────────────────────────────┐
│                    使用者介面層                           │
│  CLI 入口（scripts）  │  HTML 報告  │  DATA_CATALOG.md   │
└───────┬───────────────┴─────────────┴───────────────────┘
        │
┌───────▼─────────────────────────────────────────────────┐
│                    策略引擎層                             │
│  VBT 策略適配器  │  參數掃描器  │  組合最佳化器           │
└───────┬──────────┴─────────────┴────────────────────────┘
        │
┌───────▼─────────────────────────────────────────────────┐
│                    資料管理層                             │
│  DataStore (Parquet)  │  Importer  │  Catalog Manager    │
└───────┬───────────────┴────────────┴────────────────────┘
        │
┌───────▼─────────────────────────────────────────────────┐
│                    報告產出層                             │
│  HTML Generator  │  Plotly 圖表  │  Trade Log Renderer   │
└─────────────────────────────────────────────────────────┘
```

### 2.2 與母專案的關係

```
母專案 (Cry2/)
├── data/historical/           ← 本工具可匯入此資料
├── config/strategies.yaml     ← 本工具讀取策略參數定義
├── src/bot/strategy/          ← 參考策略邏輯，轉為向量化版本
│
└── backtest_tool/             ← 本工具箱（獨立運行）
    ├── data/                  ← 獨立資料庫
    ├── strategies/            ← VectorBT 策略實作
    ├── engine/                ← 回測核心
    ├── reports/               ← 報告產出
    └── ...
```

---

## 3. 完整目錄結構（Architecture Tree）

```
backtest_tool/
│
├── SPEC.md                         # 本文件 — 規格書
├── AGENT_GUIDE.md                  # 代理程式開發指南（如何寫 & 如何 review）
├── requirements.txt                # 本工具專用依賴
├── pyproject.toml                  # 專案配置
├── README.md                       # 快速開始指南
│
├── config/                         # 配置
│   ├── backtest_config.yaml        # 回測全域配置（手續費率、滑價、初始資金…）
│   └── param_spaces.yaml           # 參數掃描空間定義
│
├── data/                           # 獨立資料庫
│   ├── DATA_CATALOG.md             # 📋 資料目錄（記錄所有已存入資料集）
│   ├── klines/                     # K 線資料（Parquet）
│   │   ├── BTCUSDT/
│   │   │   ├── 1m/
│   │   │   │   └── *.parquet
│   │   │   ├── 4h/
│   │   │   └── 1d/
│   │   ├── ETHUSDT/
│   │   └── .../
│   └── funding/                    # 資金費率（Parquet）
│       ├── BTCUSDT.parquet
│       └── ETHUSDT.parquet
│
├── strategies/                     # VectorBT 策略向量化實作
│   ├── __init__.py
│   ├── base_vbt.py                 # VBT 策略基礎類
│   ├── trend_donchian_vbt.py       # 策略 C: Donchian 突破
│   ├── mean_reversion_bb_vbt.py    # 策略 D: BB + RSI 均值回歸
│   ├── grid_futures_vbt.py         # 策略 B: 合約網格
│   ├── funding_arb_vbt.py          # 策略 A: 資金費率套利
│   └── indicators/                 # VBT 指標封裝
│       ├── __init__.py
│       ├── atr.py                  # ATR (使用 pandas-ta 或 vectorbt)
│       ├── bollinger.py            # Bollinger Bands + RSI
│       ├── donchian.py             # Donchian Channel
│       └── adx.py                  # ADX
│
├── engine/                         # 回測核心引擎
│   ├── __init__.py
│   ├── runner.py                   # 回測執行器（單策略 / 多策略）
│   ├── param_scanner.py            # 參數空間掃描（VBT 向量化）
│   ├── portfolio_optimizer.py      # 多策略資金配置最佳化
│   └── cost_model.py               # 手續費 + 滑價模型（適配 VBT）
│
├── data_manager/                   # 資料管理
│   ├── __init__.py
│   ├── store.py                    # DataStore: Parquet 讀寫 + 校驗
│   ├── importer.py                 # 從母專案 / Binance 匯入資料
│   ├── catalog.py                  # DATA_CATALOG.md 自動更新
│   └── validator.py                # 資料完整性檢查（缺漏、異常值）
│
├── reports/                        # 報告產出
│   ├── __init__.py
│   ├── html_report.py              # 主報告生成器（自包含 HTML）
│   ├── trade_log.py                # 逐筆交易明細表
│   ├── charts.py                   # Plotly 圖表生成
│   ├── tearsheet.py                # 績效 Tearsheet（月度/年度）
│   ├── templates/                  # Jinja2 HTML 模板
│   │   ├── base.html               # 基礎版面
│   │   ├── summary.html            # 摘要頁
│   │   ├── trades.html             # 交易明細頁
│   │   ├── charts.html             # 圖表頁
│   │   └── comparison.html         # 策略比較頁
│   └── output/                     # 報告輸出目錄
│       └── .gitkeep
│
├── scripts/                        # CLI 入口腳本
│   ├── run_single.py               # 單策略回測
│   ├── run_compare.py              # 多策略比較回測
│   ├── run_param_scan.py           # 參數掃描
│   ├── run_optimize.py             # 組合最佳化
│   ├── import_data.py              # 匯入資料
│   └── download_data.py            # 下載 Binance 歷史資料
│
└── tests/                          # 測試
    ├── __init__.py
    ├── test_strategies.py          # 策略邏輯驗證
    ├── test_data_store.py          # 資料存取測試
    ├── test_reports.py             # 報告生成測試
    ├── test_param_scanner.py       # 參數掃描測試
    └── test_parity.py              # 與母專案引擎的一致性測試
```

---

## 4. 功能需求

### 4.1 資料管理 (data_manager/)

| ID | 需求 | 優先級 | 驗收條件 |
|----|------|--------|----------|
| DM-01 | 支援從母專案 `data/historical/` 匯入 Parquet | MUST | 匯入後資料筆數一致 |
| DM-02 | 支援直接從 Binance API 下載 K 線 | MUST | 下載 BTCUSDT 1m 一年資料 < 10 分鐘 |
| DM-03 | 支援下載資金費率歷史 | MUST | 每 8 小時一筆，無缺漏 |
| DM-04 | 所有資料存為 Parquet，按 symbol/timeframe/year 分檔 | MUST | 路徑格式為 `data/klines/{SYMBOL}/{TF}/{YEAR}.parquet` |
| DM-05 | 自動更新 `DATA_CATALOG.md`（記錄 symbol、timeframe、時間範圍、筆數、檔案大小） | MUST | 每次匯入/下載後自動更新 |
| DM-06 | 資料校驗：檢查時間連續性、缺漏 bar、異常值（OHLC 不合理） | MUST | 校驗失敗時輸出明確報告 |
| DM-07 | 增量更新：已有資料只下載新的部分 | SHOULD | 下載時間 < 重新下載的 50% |
| DM-08 | 支援多幣種批量匯入/下載 | MUST | 一次可處理 10+ 幣種 |

### 4.2 策略實作 (strategies/)

| ID | 需求 | 優先級 | 驗收條件 |
|----|------|--------|----------|
| ST-01 | `base_vbt.py` 定義 VBT 策略基礎類，統一介面 | MUST | 所有策略繼承同一基礎類 |
| ST-02 | 策略 C: Donchian Breakout 向量化實作 | MUST | 信號與母專案邏輯一致（±5%交易筆數） |
| ST-03 | 策略 D: Bollinger + RSI 向量化實作 | MUST | 同上 |
| ST-04 | 策略 B: Grid Futures 向量化實作 | MUST | 同上 |
| ST-05 | 策略 A: Funding Arbitrage 向量化實作 | MUST | 同上 |
| ST-06 | 所有指標使用 pandas-ta 或 VBT 內建，不自行計算 | SHOULD | 指標值與標準庫一致 |
| ST-07 | 策略參數從 YAML 配置讀取，不硬編碼 | MUST | 修改 YAML 即可調整參數 |
| ST-08 | 每個策略支援 `generate_entries()` 和 `generate_exits()` 方法 | MUST | 回傳 boolean Series/ndarray |

### 4.3 回測引擎 (engine/)

| ID | 需求 | 優先級 | 驗收條件 |
|----|------|--------|----------|
| EN-01 | `runner.py`: 單策略回測，使用 VBT `Portfolio.from_signals()` | MUST | 回傳 VBT Portfolio 物件 |
| EN-02 | `runner.py`: 多策略回測，各策略獨立運行再合併 | MUST | 各策略 equity curve 可獨立查看 |
| EN-03 | `cost_model.py`: 模擬 Binance 手續費（Maker 0.02%, Taker 0.04%） | MUST | 費用計算與母專案 FeeModel 一致 |
| EN-04 | `cost_model.py`: 模擬滑價（固定 BPS 模式） | MUST | 與母專案 SlippageModel 一致 |
| EN-05 | `param_scanner.py`: 參數空間笛卡爾積掃描 | MUST | 1000 組合 < 5 分鐘（4h K 線 1 年） |
| EN-06 | `param_scanner.py`: 輸出最佳 N 組參數（按 Sharpe 排序） | MUST | 可自訂排序指標 |
| EN-07 | `portfolio_optimizer.py`: 多策略資金配置最佳化 | MUST | 使用 scipy.optimize 或暴力搜索 |
| EN-08 | `portfolio_optimizer.py`: 輸出 Sharpe 最大化的比例 | MUST | 配置總和 = 100% |
| EN-09 | 支援不同時間粒度回測（1m / 5m / 1h / 4h / 1d） | MUST | 所有 timeframe 可回測 |
| EN-10 | 支援多幣種（BTCUSDT、ETHUSDT、SOLUSDT…） | MUST | 至少 5 個幣種 |

### 4.4 報告產出 (reports/)

| ID | 需求 | 優先級 | 驗收條件 |
|----|------|--------|----------|
| RP-01 | 產出自包含 HTML 報告（CSS/JS 內嵌，可離線閱讀） | MUST | 單一 .html 檔可在瀏覽器開啟 |
| RP-02 | 摘要頁：20+ 績效指標（見下方列表） | MUST | 所有指標正確計算 |
| RP-03 | 交易明細頁：逐筆列出每筆交易 | MUST | 含進場/出場時間、價格、方向、數量、PnL、手續費 |
| RP-04 | 圖表頁：Equity Curve（使用 Plotly 互動式） | MUST | 可縮放、hover 顯示數值 |
| RP-05 | 圖表頁：月度收益熱力圖 | MUST | Year × Month 格式 |
| RP-06 | 圖表頁：Drawdown 曲線 | MUST | 與 equity curve 對應 |
| RP-07 | 圖表頁：每日/每月 PnL 柱狀圖 | SHOULD | 綠漲紅跌 |
| RP-08 | 圖表頁：交易分佈直方圖（PnL distribution） | SHOULD | 含正態分佈擬合 |
| RP-09 | 策略比較頁：多策略並排比較 | MUST | 表格 + overlay equity curve |
| RP-10 | Tearsheet：年度/月度損益明細表 | MUST | 類似 QuantStats tearsheet |
| RP-11 | 報告嵌入策略參數與回測設定 | MUST | 報告可追溯所有設定 |
| RP-12 | 支援中英文雙語報告標題 | SHOULD | 預設中文 |

#### 報告必須包含的績效指標

| 指標 | 說明 |
|------|------|
| Total Return | 總回報率 |
| Annualized Return | 年化回報率 |
| Sharpe Ratio | 夏普比率（年化） |
| Sortino Ratio | 索提諾比率 |
| Max Drawdown | 最大回撤 |
| Max Drawdown Duration | 最大回撤持續天數 |
| Calmar Ratio | 卡爾瑪比率 |
| Win Rate | 勝率 |
| Profit Factor | 獲利因子 |
| Payoff Ratio | 盈虧比 |
| Total Trades | 總交易筆數 |
| Avg Trade PnL | 平均每筆損益 |
| Avg Win / Avg Loss | 平均贏/平均虧 |
| Best Trade | 最佳單筆 |
| Worst Trade | 最差單筆 |
| Avg Holding Period | 平均持倉時間 |
| Total Fees | 總手續費 |
| Net Profit | 淨利潤 |
| Daily / Monthly / Annual Return Table | 損益時間表 |
| Volatility (Ann.) | 年化波動率 |

### 4.5 CLI 入口 (scripts/)

| ID | 需求 | 優先級 | 驗收條件 |
|----|------|--------|----------|
| CL-01 | `run_single.py`: 單策略回測 + 報告 | MUST | `python scripts/run_single.py --strategy trend_donchian --symbol BTCUSDT --start 2024-01-01 --end 2024-12-31` |
| CL-02 | `run_compare.py`: 多策略比較 + 報告 | MUST | 可指定多個策略名稱 |
| CL-03 | `run_param_scan.py`: 參數掃描 + 報告 | MUST | 可指定策略 + 參數空間 |
| CL-04 | `run_optimize.py`: 組合最佳化 + 報告 | MUST | 輸出最佳配置比例 |
| CL-05 | `import_data.py`: 從母專案匯入資料 | MUST | `python scripts/import_data.py --source ../data` |
| CL-06 | `download_data.py`: 從 Binance 下載資料 | MUST | 與母專案功能等效 |
| CL-07 | 所有 CLI 指令支援 `--help` 文件 | MUST | argparse 描述完整 |
| CL-08 | 所有 CLI 指令有進度條顯示 | SHOULD | 使用 tqdm |

---

## 5. 技術選型

### 5.1 核心依賴

| 套件 | 版本 | 用途 |
|------|------|------|
| `vectorbt` | >=0.26.0 | 向量化回測核心 |
| `pandas` | >=2.1.0 | 資料處理 |
| `numpy` | >=1.25.0 | 數值計算 |
| `pandas-ta` | >=0.3.14b1 | 技術指標計算 |
| `plotly` | >=5.18.0 | 互動式圖表 |
| `jinja2` | >=3.1.0 | HTML 模板引擎 |
| `scipy` | >=1.11.0 | 最佳化演算法 |
| `pyarrow` | >=14.0.0 | Parquet 讀寫 |
| `pyyaml` | >=6.0.1 | 配置讀取 |
| `tqdm` | >=4.66.0 | 進度條 |
| `python-binance` | >=1.0.19 | Binance API（下載資料用） |
| `structlog` | >=23.2.0 | 結構化日誌 |

### 5.2 Python 版本

- **最低**: Python 3.11
- **建議**: Python 3.12+

---

## 6. 配置檔定義

### 6.1 `config/backtest_config.yaml`

```yaml
# 回測全域配置
backtest:
  initial_capital: 10000          # 初始資金 (USDT)
  default_leverage: 2             # 預設槓桿
  
  # 手續費
  fees:
    maker_rate: 0.0002            # 0.02%
    taker_rate: 0.0004            # 0.04%
    default_type: taker           # 預設使用 taker 費率
  
  # 滑價
  slippage:
    model: fixed_bps              # fixed_bps | volume_weighted
    fixed_bps: 2.0                # 2 basis points
  
  # 報告
  report:
    language: zh-TW               # zh-TW | en
    output_dir: reports/output
    include_trade_log: true
    include_charts: true
    chart_theme: plotly_dark      # plotly | plotly_dark | seaborn
  
  # 資料
  data:
    default_timeframe: 4h
    default_symbols:
      - BTCUSDT
      - ETHUSDT
```

### 6.2 `config/param_spaces.yaml`

```yaml
# 參數掃描空間定義
param_spaces:
  trend_donchian:
    entry_period: [10, 15, 20, 25, 30, 40]
    exit_period: [5, 7, 10, 15]
    adx_threshold: [20, 25, 30, 35]
    atr_stop_mult: [1.0, 1.5, 2.0, 2.5, 3.0]
    leverage: [1, 2, 3]

  mean_reversion_bb:
    bb_period: [15, 20, 25, 30]
    bb_std: [1.5, 2.0, 2.5, 3.0]
    rsi_period: [10, 14, 21]
    rsi_oversold: [20, 25, 30, 35]
    rsi_overbought: [65, 70, 75, 80]
    leverage: [1, 2]

  grid_futures:
    grid_count: [10, 15, 20, 30]
    ema_period: [100, 150, 200]
    leverage: [1, 2, 3]

  funding_arb:
    funding_rate_threshold_annual: [10, 12, 15, 20, 25]
    funding_rate_exit_annual: [3, 5, 7, 10]
    max_hold_days: [3, 5, 7, 14]

# 組合最佳化
portfolio_optimization:
  allocation_step: 0.05           # 5% 步進
  min_allocation: 0.0             # 最小配置比例
  max_allocation: 0.80            # 最大配置比例（單策略不超過 80%）
  target_metric: sharpe_ratio     # 最佳化目標
```

---

## 7. 資料目錄規範（DATA_CATALOG.md）

`DATA_CATALOG.md` 由 `catalog.py` 自動生成，格式如下：

```markdown
# 📋 Data Catalog — 回測資料目錄

> 自動生成，請勿手動編輯。最後更新：2026-04-17 16:10:00 UTC

## K 線資料

| Symbol | Timeframe | 起始日期 | 結束日期 | 筆數 | 檔案大小 | 缺漏率 | 最後更新 |
|--------|-----------|----------|----------|------|----------|--------|----------|
| BTCUSDT | 1m | 2024-01-01 | 2025-12-31 | 1,051,200 | 42.3 MB | 0.01% | 2026-04-17 |
| BTCUSDT | 4h | 2024-01-01 | 2025-12-31 | 4,380 | 0.2 MB | 0.00% | 2026-04-17 |
| ETHUSDT | 1m | 2024-01-01 | 2025-12-31 | 1,051,200 | 41.8 MB | 0.02% | 2026-04-17 |
| ... | | | | | | | |

## 資金費率資料

| Symbol | 起始日期 | 結束日期 | 筆數 | 檔案大小 | 最後更新 |
|--------|----------|----------|------|----------|----------|
| BTCUSDT | 2024-01-01 | 2025-12-31 | 2,190 | 0.1 MB | 2026-04-17 |
| ... | | | | | |

## 資料校驗摘要

- ✅ 通過校驗：15 / 15 資料集
- ⚠️ 缺漏 bar > 0.1%：0 / 15
- ❌ 異常值偵測：0 / 15
```

---

## 8. HTML 報告規格

### 8.1 報告結構

```
單策略報告
├── Header（策略名稱、幣種、時間段、參數）
├── Section 1: 績效摘要卡片（20+ 指標，2 欄卡片佈局）
├── Section 2: Equity Curve（Plotly 互動式）
├── Section 3: Drawdown 曲線
├── Section 4: 月度收益熱力圖（Year × Month）
├── Section 5: 每日 PnL 柱狀圖
├── Section 6: 交易 PnL 分佈直方圖
├── Section 7: 年度 / 月度損益明細表
├── Section 8: 逐筆交易明細表（可排序、可搜尋）
│   └── 每筆含：#序號、進場時間、出場時間、方向、幣種、
│       進場價、出場價、數量、PnL、PnL%、手續費、持倉時間、進場原因
├── Section 9: 回測設定（手續費、滑價、初始資金、槓桿）
└── Footer（生成時間、工具版本）

多策略比較報告
├── Header
├── Section 1: 比較摘要表格
├── Section 2: Equity Curve Overlay
├── Section 3: 各策略獨立績效卡片
├── Section 4: 最佳配置建議
└── Footer
```

### 8.2 技術要求

- 單一 HTML 檔，CSS 和 Plotly JS 皆內嵌
- Plotly 圖表使用 `plotly.js` CDN 或內嵌（離線模式）
- 交易明細表使用 DataTables.js 或純 CSS 實作排序
- 報告模板使用 Jinja2
- 檔案大小目標：< 10 MB（含圖表）

---

## 9. VBT 策略介面定義

### 9.1 基礎類 `BaseVBTStrategy`

```python
class BaseVBTStrategy(ABC):
    """VectorBT 策略基礎類。"""

    name: str                       # 策略名稱
    default_params: dict            # 預設參數
    required_timeframe: str         # 所需 K 線週期

    def __init__(self, params: dict | None = None):
        """初始化，合併預設參數與用戶參數。"""

    @abstractmethod
    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """生成進場信號（True/False Series）。"""

    @abstractmethod
    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """生成出場信號（True/False Series）。"""

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series | None:
        """生成做空進場信號（可選，預設 None）。"""
        return None

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series | None:
        """生成做空出場信號（可選，預設 None）。"""
        return None

    def run_backtest(
        self,
        ohlcv: pd.DataFrame,
        initial_capital: float = 10000,
        fees: float = 0.0004,
        slippage: float = 0.0002,
        leverage: int = 1,
    ) -> vbt.Portfolio:
        """執行回測，回傳 VBT Portfolio。"""
```

### 9.2 策略實作對照表

| 母專案策略 | VBT 策略 | 進場條件 | 出場條件 | 特殊處理 |
|-----------|---------|----------|----------|----------|
| `TrendDonchianStrategy` | `TrendDonchianVBT` | Close > Donchian Upper & ADX > threshold | Close < Donchian Exit Lower \| ATR trailing stop | 做多+做空 |
| `MeanReversionBBStrategy` | `MeanReversionBBVBT` | Close <= BB Lower & RSI < oversold | Close >= BB Middle \| Stop loss % | 波動率過濾 |
| `GridFuturesStrategy` | `GridFuturesVBT` | Price 觸及 grid level | 觸及對向 grid level | EMA 趨勢過濾 |
| `FundingArbStrategy` | `FundingArbVBT` | Annual rate > threshold | Rate < exit \| 超時 | 需要 funding rate 資料 |

---

## 10. 非功能需求

| 項目 | 需求 |
|------|------|
| 回測速度 | 4h K 線 × 1 年 × 單策略 < 5 秒 |
| 參數掃描速度 | 1,000 組合 × 4h × 1 年 < 5 分鐘 |
| 報告生成速度 | 含圖表 < 10 秒 |
| 記憶體 | 單次回測 < 4 GB |
| 錯誤處理 | 所有 CLI 指令遇到錯誤顯示清楚訊息而非 traceback |
| 日誌 | 使用 structlog，回測過程有 progress 訊息 |
| 相容性 | Windows / macOS / Linux 皆可運行 |

---

## 11. 測試計畫

| 測試類別 | 檔案 | 測試重點 |
|----------|------|----------|
| 策略邏輯 | `test_strategies.py` | 已知輸入 → 預期信號、edge case |
| 資料存取 | `test_data_store.py` | 讀寫一致性、增量更新、校驗 |
| 報告生成 | `test_reports.py` | HTML 可生成、指標正確嵌入 |
| 參數掃描 | `test_param_scanner.py` | 結果數量 = 笛卡爾積、排序正確 |
| 一致性 | `test_parity.py` | VBT 策略 vs 母專案引擎，交易筆數差異 < 10% |

---

## 12. 里程碑

| Phase | 內容 | 依賴 |
|-------|------|------|
| P1 | 資料管理層 + DATA_CATALOG.md | 無 |
| P2 | VBT 策略實作（4 種） + 指標 | P1 |
| P3 | 回測引擎 + 參數掃描 | P2 |
| P4 | HTML 報告產出 | P3 |
| P5 | 組合最佳化 + 比較報告 | P3, P4 |
| P6 | 測試 + 文件 | P1-P5 |
