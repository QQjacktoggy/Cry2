# VectorBT 回測工具箱 (Backtest Toolkit)

高效能向量化回測系統，用於加密貨幣期貨策略研究與參數優化。

## 快速開始

### 1. 安裝依賴

```bash
cd backtest_tool
pip install -r requirements.txt
```

### 2. 匯入資料

從母專案匯入已下載的歷史資料：

```bash
# 匯入全部
python -m backtest_tool.scripts.import_data --all

# 匯入特定幣種
python -m backtest_tool.scripts.import_data --symbol BTCUSDT --timeframe 4h
```

從 Binance API 下載：

```bash
python -m backtest_tool.scripts.download_data \
    --symbol BTCUSDT --timeframe 4h --start 2023-01-01
```

### 3. 執行單策略回測

```bash
python -m backtest_tool.scripts.run_single \
    --strategy trend_donchian \
    --symbol BTCUSDT \
    --timeframe 4h \
    --start 2023-01-01
```

### 4. 多策略比較

```bash
python -m backtest_tool.scripts.run_compare \
    --strategies trend_donchian mean_reversion_bb grid_futures \
    --symbol BTCUSDT \
    --timeframes 4h 1h 4h \
    --start 2023-01-01
```

### 5. 參數掃描

```bash
python -m backtest_tool.scripts.run_param_scan \
    --strategy trend_donchian \
    --symbol BTCUSDT \
    --timeframe 4h \
    --start 2023-01-01 \
    --target sharpe_ratio \
    --top 20
```

### 6. 組合優化

```bash
python -m backtest_tool.scripts.run_optimize \
    --strategies trend_donchian mean_reversion_bb grid_futures \
    --symbol BTCUSDT \
    --timeframes 4h 1h 4h \
    --start 2023-01-01 \
    --target sharpe_ratio
```

## 架構總覽

```
backtest_tool/
├── config/
│   ├── backtest_config.yaml    # 全域設定（資金、手續費、滑價）
│   └── param_spaces.yaml       # 參數掃描空間定義
├── data/                       # Parquet 資料儲存
│   ├── klines/{SYMBOL}/{TF}/   # K 線資料（按年分割）
│   └── funding/                # 資金費率資料
├── data_manager/
│   ├── store.py                # DataStore: Parquet CRUD
│   ├── validator.py            # 資料品質驗證
│   ├── importer.py             # 資料匯入（母專案 + Binance API）
│   └── catalog.py              # 自動產生 DATA_CATALOG.md
├── strategies/
│   ├── base_vbt.py             # 策略基底類別 (ABC)
│   ├── trend_donchian_vbt.py   # 策略 C: Donchian 趨勢突破
│   ├── mean_reversion_bb_vbt.py # 策略 D: BB 均值回歸
│   ├── grid_futures_vbt.py     # 策略 B: 網格交易
│   ├── funding_arb_vbt.py      # 策略 A: 資金費率套利
│   └── indicators/             # 技術指標函式
├── engine/
│   ├── runner.py               # BacktestRunner: 回測執行器
│   ├── cost_model.py           # 手續費 + 滑價模型
│   ├── param_scanner.py        # 參數空間掃描
│   └── portfolio_optimizer.py  # 多策略配置優化
├── reports/
│   ├── html_report.py          # HTML 報告產生器
│   ├── charts.py               # Plotly 互動圖表（6 種）
│   ├── trade_log.py            # 交易明細表
│   ├── tearsheet.py            # 月度/年度績效表
│   └── templates/              # Jinja2 HTML 模板
├── scripts/                    # CLI 入口程式
└── tests/                      # 測試套件
```

## 支援策略

| 策略 | 類別 | 時間框架 | 說明 |
|------|------|----------|------|
| `trend_donchian` | TrendDonchianVBT | 4h | Donchian 通道突破 + ADX 過濾 |
| `mean_reversion_bb` | MeanReversionBBVBT | 1h | 布林帶 + RSI 超賣 + 止損 |
| `grid_futures` | GridFuturesVBT | 4h | 通道區域 + EMA 趨勢過濾 |
| `funding_arb` | FundingArbVBT | 8h | 資金費率套利（做空為主） |

## HTML 報告內容

- 📈 權益曲線（互動式 Plotly 圖表）
- 📉 回撤分析
- 🗓️ 月度收益熱力圖
- 📊 每日損益柱狀圖
- 📊 交易損益分佈直方圖
- 📅 月度/年度績效明細表
- 📋 逐筆交易明細（含進出場價格、PnL、手續費）
- ⚙️ 回測參數配置

## 測試

```bash
python -m pytest backtest_tool/tests/ -v
```

## 技術依賴

- **VectorBT** ≥ 0.26.0 — 向量化回測引擎
- **ta** ≥ 0.11.0 — 技術指標（ATR, ADX, BB, RSI, EMA）
- **Plotly** ≥ 5.18.0 — 互動式圖表
- **Jinja2** — HTML 模板引擎
- **PyArrow** — Parquet 讀寫
