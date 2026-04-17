# 代理程式開發指南 — AGENT_GUIDE.md

**用途**: 定義 AI 代理（Codex / Copilot）如何撰寫本專案的每支程式，以及如何驗證與 review。
**搭配文件**: `SPEC.md` v1.0

---

## 0. 總則

### 0.1 開發流程

```
讀取 SPEC.md → 依 Phase 順序開發 → 每支程式完成後自我 review → 寫測試 → 跑測試通過 → 下一支
```

### 0.2 核心原則

1. **SPEC 為準**：所有實作以 SPEC.md 定義為準，不自行擴充功能。
2. **一次一個模組**：按 Phase 順序，每次只完成一個檔案。
3. **先寫測試**：每個模組完成後，立即寫對應測試並執行通過。
4. **不造輪子**：優先使用 VectorBT / pandas-ta 內建功能。
5. **最小驚訝**：函式命名、參數順序與母專案保持一致。

### 0.3 程式碼風格

- Python 3.11+ type hints 必用
- Docstring 使用 Google style
- 每個 public function 必須有 docstring
- 常數用 UPPER_SNAKE_CASE
- 類別用 PascalCase
- 函式/變數用 snake_case
- 每行最長 120 字元
- 不用 `# type: ignore` 除非有充分理由
- import 順序：stdlib → third-party → local

---

## 1. 各模組開發指南

### 1.1 Phase 1: 資料管理層

#### 📄 `data_manager/store.py` — DataStore

**職責**: Parquet 資料的讀寫、查詢、校驗。

**實作要求**:
```python
class DataStore:
    def __init__(self, data_dir: str = "./data"):
        """初始化，建立目錄結構。"""

    def save_klines(self, df: pd.DataFrame, symbol: str, timeframe: str) -> None:
        """儲存 K 線資料（按年分檔、去重、排序）。
        
        邏輯:
        1. 確保 df 有 SPEC 定義的所有欄位（timestamp, open, high, low, close, volume...）
        2. 按年拆分
        3. 若已存在同年檔案 → 合併 + 去重（以 timestamp 為 key）
        4. 排序後寫入 Parquet
        """

    def load_klines(self, symbol: str, timeframe: str, 
                    start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """載入 K 線資料。
        
        邏輯:
        1. 掃描 data/klines/{symbol}/{timeframe}/*.parquet
        2. 合併所有檔案
        3. 以 start/end 過濾（支援 'YYYY-MM-DD' 字串格式）
        4. 設定 DatetimeIndex（以 timestamp 轉換）
        """

    def save_funding(self, df: pd.DataFrame, symbol: str) -> None:
        """儲存資金費率（去重、排序）。"""

    def load_funding(self, symbol: str, 
                     start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """載入資金費率。"""

    def list_available(self) -> dict:
        """回傳所有可用資料集的 summary dict。
        
        回傳格式:
        {
            'klines': [
                {'symbol': 'BTCUSDT', 'timeframe': '4h', 'start': '2024-01-01', 
                 'end': '2025-12-31', 'rows': 4380, 'size_mb': 0.2},
                ...
            ],
            'funding': [
                {'symbol': 'BTCUSDT', 'start': '2024-01-01', 'end': '2025-12-31', 
                 'rows': 2190, 'size_mb': 0.1},
                ...
            ]
        }
        """
```

**校驗規則**（寫在 `validator.py`）:
- timestamp 必須單調遞增
- open/high/low/close 必須 > 0
- high >= max(open, close) 且 low <= min(open, close)
- volume >= 0
- 連續 bar 間距不超過預期值的 3 倍（偵測缺漏）

**Review Checklist**:
- [ ] Parquet 讀寫正確，去重邏輯無誤
- [ ] 日期篩選邊界正確（含端點）
- [ ] 空目錄 / 空檔案不 crash
- [ ] 大量資料（100 萬筆）效能可接受
- [ ] type hints 完整

---

#### 📄 `data_manager/importer.py` — Importer

**職責**: 從母專案或 Binance 匯入資料。

**實作要求**:
```python
class DataImporter:
    def __init__(self, store: DataStore):
        """初始化。"""

    def import_from_parent(self, source_dir: str, 
                           symbols: list[str] | None = None,
                           timeframes: list[str] | None = None) -> dict:
        """從母專案 data/historical/ 匯入。
        
        邏輯:
        1. 掃描 source_dir/klines/{symbol}/{tf}/*.parquet
        2. 逐檔讀取並存入本工具 DataStore
        3. 同時處理 source_dir/funding/*.parquet
        4. 回傳匯入摘要 dict
        
        注意: 母專案 Parquet schema 與本工具一致（KLINE_COLUMNS），可直接讀取。
        """

    def download_from_binance(self, symbols: list[str], 
                               timeframes: list[str],
                               start: str, end: str | None = None,
                               include_funding: bool = False) -> dict:
        """從 Binance API 下載。
        
        邏輯:
        1. 使用 python-binance 的 get_historical_klines()
        2. 分批下載（每次最多 1500 根 K 線）
        3. 轉為 DataFrame，欄位對齊 KLINE_COLUMNS
        4. 存入 DataStore
        5. 如 include_funding=True，另外下載資金費率
        6. 顯示 tqdm 進度條
        """
```

**Review Checklist**:
- [ ] 母專案 Parquet 格式正確解析
- [ ] Binance API 分頁邏輯正確（不遺漏資料）
- [ ] 增量下載（從已有的最後 timestamp 繼續）
- [ ] API 錯誤有 retry 機制（指數退避）
- [ ] 進度條正確顯示

---

#### 📄 `data_manager/catalog.py` — Catalog Manager

**職責**: 自動生成 `DATA_CATALOG.md`。

**實作要求**:
```python
class CatalogManager:
    def __init__(self, store: DataStore):
        """初始化。"""

    def update(self) -> None:
        """掃描 DataStore，重新生成 DATA_CATALOG.md。
        
        邏輯:
        1. 呼叫 store.list_available() 取得所有資料集
        2. 呼叫 validator.validate_all() 取得校驗結果
        3. 用字串模板生成 markdown
        4. 寫入 data/DATA_CATALOG.md
        """
```

**Review Checklist**:
- [ ] 欄位與 SPEC §7 定義一致
- [ ] 數字格式化（千分位、MB 單位）
- [ ] 校驗摘要正確統計
- [ ] 空資料庫時生成空表格而非 crash

---

### 1.2 Phase 2: VBT 策略實作

#### 📄 `strategies/base_vbt.py` — BaseVBTStrategy

**職責**: 所有 VBT 策略的抽象基礎類。

**實作要求**:
```python
from abc import ABC, abstractmethod

class BaseVBTStrategy(ABC):
    name: str = "base"
    default_params: dict = {}
    required_timeframe: str = "4h"

    def __init__(self, params: dict | None = None):
        """合併參數: default_params | params（用戶優先）。"""
        self.params = {**self.default_params, **(params or {})}

    @abstractmethod
    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """做多進場信號。ohlcv 是帶 DatetimeIndex 的 OHLCV DataFrame。
        回傳 bool Series（True = 進場）。"""

    @abstractmethod
    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """做多出場信號。"""

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series | None:
        """做空進場信號（預設 None = 不做空）。"""
        return None

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series | None:
        """做空出場信號。"""
        return None

    def run_backtest(self, ohlcv: pd.DataFrame, 
                     initial_capital: float = 10000,
                     fees: float = 0.0004,
                     slippage: float = 0.0002,
                     leverage: float = 1) -> vbt.Portfolio:
        """執行回測。
        
        邏輯:
        1. 呼叫 generate_entries/exits/short_entries/short_exits
        2. 使用 vbt.Portfolio.from_signals() 建立 Portfolio
        3. 傳入 fees, slippage, init_cash, leverage 等參數
        4. 回傳 VBT Portfolio 物件
        
        重要: 
        - freq 參數要正確設定（根據 required_timeframe）
        - 做空需要 short_entries + short_exits
        """

    def get_param_combinations(self, param_space: dict) -> list[dict]:
        """從參數空間生成所有組合（笛卡爾積）。"""
```

**Review Checklist**:
- [ ] `run_backtest` 正確呼叫 `vbt.Portfolio.from_signals()`
- [ ] fees 和 slippage 參數正確傳入 VBT
- [ ] freq 參數正確（'4h', '1h', '1d' 等）
- [ ] 做空信號正確處理（None vs Series）
- [ ] 參數組合生成正確

---

#### 📄 `strategies/trend_donchian_vbt.py` — 策略 C

**職責**: Donchian Breakout 趨勢跟隨策略的向量化實作。

**實作要求**:
```python
class TrendDonchianVBT(BaseVBTStrategy):
    name = "trend_donchian"
    default_params = {
        'entry_period': 20,
        'exit_period': 10,
        'adx_period': 14,
        'adx_threshold': 25,
        'atr_period': 14,
        'atr_stop_mult': 2.0,
        'leverage': 2,
    }
    required_timeframe = "4h"

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """做多進場: Close > Donchian Upper (entry_period) AND ADX > threshold。
        
        步驟:
        1. 計算 Donchian Upper = high.rolling(entry_period).max().shift(1)
           （shift(1) 因為要用前一根 bar 的 channel）
        2. 計算 ADX (使用 pandas-ta)
        3. entries = (close > donchian_upper) & (adx > adx_threshold)
        """

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """做多出場: Close < Donchian Lower (exit_period)。
        
        注意: ATR trailing stop 在向量化回測中較難精確實作。
        簡化方案: 使用 Donchian Exit Channel 作為主要出場。
        進階方案: 使用 vbt.STOPLOSS indicator 做 trailing stop。
        """

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """做空進場: Close < Donchian Lower (entry_period) AND ADX > threshold。"""

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """做空出場: Close > Donchian Upper (exit_period)。"""
```

**對照母專案**: 參考 `src/bot/strategy/trend_donchian.py`
- 母專案的 `entry_period=20` → `high.rolling(20).max()`
- 母專案的 `adx_threshold=25` → pandas-ta 的 `ta.adx()`
- 母專案 trailing stop 用 ATR → VBT 中用 `sl_stop` 參數或 Donchian exit 替代

**Review Checklist**:
- [ ] Donchian Channel 計算正確（注意 shift(1) 避免前視偏差）
- [ ] ADX 計算使用 pandas-ta，值範圍 0-100
- [ ] 做多+做空信號同時實作
- [ ] 參數皆可從 YAML 配置
- [ ] warmup period 處理（前 N 根 bar 不產生信號）

---

#### 📄 `strategies/mean_reversion_bb_vbt.py` — 策略 D

**實作要求**:
```python
class MeanReversionBBVBT(BaseVBTStrategy):
    name = "mean_reversion_bb"
    required_timeframe = "1h"
    
    # generate_entries: Close <= BB_lower AND RSI < rsi_oversold AND vol_filter_pass
    # generate_exits: Close >= BB_middle OR pnl <= -stop_loss_pct%
    # generate_short_entries: Close >= BB_upper AND RSI > rsi_overbought AND vol_filter_pass
    # generate_short_exits: Close <= BB_middle OR pnl <= -stop_loss_pct%
```

**對照母專案**: 參考 `src/bot/strategy/mean_reversion_bb.py`
- BB 計算: `ta.bbands(close, length=bb_period, std=bb_std)`
- RSI: `ta.rsi(close, length=rsi_period)`
- 波動率過濾: ATR 百分位數（`ta.atr()` → rolling percentile）
- Stop loss: 使用 VBT `sl_stop` 參數

**Review Checklist**:
- [ ] BB + RSI 信號組合正確
- [ ] 波動率過濾邏輯正確（ATR 百分位數）
- [ ] Stop loss 使用 VBT 原生 `sl_stop` 而非手動計算
- [ ] Take profit 在 BB middle 位置

---

#### 📄 `strategies/grid_futures_vbt.py` — 策略 B

**實作要求**:
- Grid 策略在向量化框架中較特殊
- 簡化方案：將 grid 視為「在 channel 下半部買入、上半部賣出」
- 使用 20-day range 建立 channel，EMA200 過濾趨勢方向

**Review Checklist**:
- [ ] Grid 邏輯轉化為信號正確
- [ ] EMA 趨勢過濾正確（bullish/bearish/ranging）
- [ ] Grid 重置條件處理

---

#### 📄 `strategies/funding_arb_vbt.py` — 策略 A

**實作要求**:
- 需要合併 K 線與 funding rate 兩種資料
- 進場: 年化 funding rate > 15% → short
- 出場: 年化 funding rate < 5% 或持倉超過 max_hold_days

**Review Checklist**:
- [ ] funding rate 年化計算正確（rate × 3 × 365）
- [ ] K 線與 funding rate 的時間對齊
- [ ] max_hold_days 邏輯正確

---

### 1.3 Phase 3: 回測引擎

#### 📄 `engine/runner.py` — 回測執行器

**職責**: 執行單策略 / 多策略回測。

**實作要求**:
```python
class BacktestRunner:
    def __init__(self, config: dict):
        """從 backtest_config.yaml 初始化。"""

    def run_single(self, strategy: BaseVBTStrategy, 
                   ohlcv: pd.DataFrame,
                   **kwargs) -> BacktestResult:
        """單策略回測。
        
        邏輯:
        1. 呼叫 strategy.run_backtest(ohlcv, ...)
        2. 從 VBT Portfolio 提取所有指標
        3. 提取逐筆交易明細（portfolio.trades.records_readable）
        4. 打包為 BacktestResult dataclass
        """

    def run_multi(self, strategies: list[BaseVBTStrategy],
                  data: dict[str, pd.DataFrame],
                  allocations: dict[str, float]) -> MultiBacktestResult:
        """多策略回測。
        
        邏輯:
        1. 各策略分別跑 run_single（資金 = 總資金 × 分配比例）
        2. 合併 equity curve（加權相加）
        3. 計算組合級別指標
        """

@dataclass
class BacktestResult:
    strategy_name: str
    portfolio: vbt.Portfolio           # VBT Portfolio 物件
    metrics: dict[str, float]          # 績效指標
    trades: pd.DataFrame               # 逐筆交易明細
    equity_curve: pd.Series            # 權益曲線
    params: dict                       # 使用的參數
    config: dict                       # 回測設定（費率、滑價等）
```

**Review Checklist**:
- [ ] 指標計算完整（SPEC §4.4 列出的 20+ 指標）
- [ ] 交易明細欄位完整（進/出場時間、價格、PnL、手續費…）
- [ ] 多策略 equity curve 合併邏輯正確
- [ ] 錯誤處理：資料不足時給友善提示

---

#### 📄 `engine/param_scanner.py` — 參數掃描

**實作要求**:
```python
class ParamScanner:
    def __init__(self, runner: BacktestRunner):
        """初始化。"""

    def scan(self, strategy_class: type[BaseVBTStrategy],
             param_space: dict,
             ohlcv: pd.DataFrame,
             sort_by: str = "sharpe_ratio",
             top_n: int = 20) -> pd.DataFrame:
        """參數空間掃描。
        
        邏輯:
        1. 從 param_space 生成所有組合（itertools.product）
        2. 優先使用 VBT 內建的向量化參數掃描
           - 某些指標支援 multi-param 向量化（一次算所有組合）
        3. 否則 for-loop 跑每組參數
        4. 收集結果到 DataFrame
        5. 按 sort_by 排序，取 top_n
        """
```

**效能要求**:
- 優先利用 VBT 的 `vbt.IndicatorFactory` 做向量化多參數計算
- 如不可行，使用 `concurrent.futures.ProcessPoolExecutor` 平行化

**Review Checklist**:
- [ ] 參數組合數量 = 各維度的笛卡爾積
- [ ] 排序指標可自訂
- [ ] 有進度條
- [ ] 結果 DataFrame 包含所有參數 + 所有指標

---

#### 📄 `engine/portfolio_optimizer.py` — 組合最佳化

**實作要求**:
```python
class PortfolioOptimizer:
    def optimize(self, strategy_results: dict[str, BacktestResult],
                 allocation_step: float = 0.05,
                 target_metric: str = "sharpe_ratio") -> dict:
        """找出最佳資金配置。
        
        邏輯:
        1. 生成所有可能配置（步進 5%，總和 = 100%）
        2. 用各策略 equity curve 加權合成
        3. 計算組合 Sharpe / Sortino / MaxDD
        4. 按 target_metric 排序
        5. 回傳 top 10 配置
        
        注意: 配置組合數可能很大，考慮 scipy.optimize 或只搜索關鍵配置。
        """
```

**Review Checklist**:
- [ ] 配置總和 = 100%
- [ ] equity curve 加權正確（不是收益率加權）
- [ ] 組合指標計算正確

---

### 1.4 Phase 4: HTML 報告

#### 📄 `reports/html_report.py` — 主報告生成器

**職責**: 將 BacktestResult 轉為自包含 HTML 檔。

**實作要求**:
```python
class HTMLReportGenerator:
    def __init__(self, template_dir: str = "reports/templates"):
        """初始化 Jinja2 環境。"""

    def generate_single(self, result: BacktestResult, 
                        output_path: str) -> str:
        """產生單策略報告。
        
        邏輯:
        1. 用 charts.py 生成所有 Plotly 圖表（to_html(full_html=False)）
        2. 用 trade_log.py 生成交易明細 HTML table
        3. 用 tearsheet.py 生成月度/年度損益表
        4. 將所有元件注入 Jinja2 template
        5. 寫入 HTML 檔
        """

    def generate_comparison(self, results: dict[str, BacktestResult],
                            output_path: str) -> str:
        """產生多策略比較報告。"""
```

**Review Checklist**:
- [ ] HTML 為自包含（CSS 內嵌、Plotly 用 CDN 或內嵌）
- [ ] 在瀏覽器可正常開啟
- [ ] 圖表可互動（zoom, hover）
- [ ] 交易明細表可排序
- [ ] 中文標題正確顯示
- [ ] 檔案大小 < 10 MB

---

#### 📄 `reports/charts.py` — 圖表生成

**要產生的圖表清單**:
1. `equity_curve_chart(equity: pd.Series) -> str` — Plotly 權益曲線
2. `drawdown_chart(equity: pd.Series) -> str` — 回撤曲線
3. `monthly_heatmap(equity: pd.Series) -> str` — 月度收益熱力圖
4. `daily_pnl_bar(equity: pd.Series) -> str` — 每日 PnL 柱狀圖
5. `trade_pnl_histogram(trades: pd.DataFrame) -> str` — PnL 分佈直方圖
6. `equity_overlay(equities: dict[str, pd.Series]) -> str` — 多策略 overlay

每個函式回傳 `plotly.io.to_html(fig, full_html=False, include_plotlyjs='cdn')` 字串。

**Review Checklist**:
- [ ] 每個圖表有明確標題和軸標籤
- [ ] 配色一致（使用 SPEC 定義的 theme）
- [ ] 月度熱力圖格式正確（Y=年, X=月, 值=收益率%）
- [ ] 互動功能正常（hover tooltip）

---

#### 📄 `reports/trade_log.py` — 交易明細

**產出格式**:
```html
<table>
  <thead>
    <tr>
      <th>#</th>
      <th>方向</th>
      <th>幣種</th>
      <th>進場時間</th>
      <th>進場價格</th>
      <th>出場時間</th>
      <th>出場價格</th>
      <th>數量</th>
      <th>PnL (USDT)</th>
      <th>PnL (%)</th>
      <th>手續費</th>
      <th>持倉時間</th>
    </tr>
  </thead>
  <tbody>
    <!-- 逐筆交易 -->
  </tbody>
</table>
```

- 從 `vbt.Portfolio.trades.records_readable` 提取
- 盈利行綠色、虧損行紅色
- 底部加總：Total PnL、Total Fees、Avg PnL

**Review Checklist**:
- [ ] 每筆交易資訊完整
- [ ] 顏色編碼正確
- [ ] 底部加總正確
- [ ] 大量交易（500+）時不卡頓

---

### 1.5 Phase 5: 組合最佳化 & 比較

#### 📄 `scripts/run_optimize.py` — CLI 入口

```bash
python scripts/run_optimize.py \
  --strategies trend_donchian mean_reversion_bb grid_futures funding_arb \
  --symbols BTCUSDT ETHUSDT \
  --start 2024-01-01 \
  --end 2025-12-31 \
  --step 0.10 \
  --metric sharpe_ratio
```

**輸出**: 
1. 終端顯示 Top 10 配置
2. 產出 HTML 比較報告

---

## 2. Review 流程

### 2.1 自我 Review（每支程式完成後）

每支程式寫完後，按以下清單逐項檢查：

#### 通用 Review Checklist（所有檔案）

```
□ 1. [正確性] 邏輯符合 SPEC.md 對應需求
□ 2. [完整性] 所有 SPEC 要求的 function/method 都已實作
□ 3. [型別] 所有 function 有 type hints（參數 + 回傳值）
□ 4. [文件] 所有 public function 有 Google-style docstring
□ 5. [錯誤處理] 邊界情況有處理（空 DataFrame、None、0 值）
□ 6. [無前視偏差] 回測信號不使用未來資料（shift(1) 正確使用）
□ 7. [效能] 避免逐行 for-loop，優先用向量化操作
□ 8. [依賴] 只用 SPEC §5 列出的套件
□ 9. [import] import 順序：stdlib → third-party → local
□ 10. [命名] 與母專案保持一致（策略名稱、參數名稱）
```

### 2.2 Codex Review（整合 review）

Codex review 時重點關注：

#### Review 層級 1：安全性
- [ ] 沒有硬編碼的 API key / secret
- [ ] 沒有寫入母專案目錄的操作（只讀取）
- [ ] 沒有網路請求在回測 hot path 中

#### Review 層級 2：正確性
- [ ] VBT `Portfolio.from_signals()` 參數正確
- [ ] fees / slippage 有傳入（不是 0）
- [ ] 信號沒有前視偏差（look-ahead bias）
- [ ] 月度/年度指標計算正確
- [ ] 交易筆數與信號數一致

#### Review 層級 3：品質
- [ ] 程式碼可讀性（非 AI slop）
- [ ] 沒有重複程式碼（DRY）
- [ ] 錯誤訊息有用（不是 generic "Error"）
- [ ] 日誌等級合理（INFO for major events, DEBUG for details）
- [ ] 沒有 print()，全用 structlog

#### Review 層級 4：一致性
- [ ] 策略名稱與母專案一致
- [ ] 參數名稱與母專案 YAML 一致
- [ ] 資料格式與母專案 Parquet 相容
- [ ] 手續費 / 滑價模型與母專案一致

### 2.3 驗證腳本

每個 Phase 完成後執行驗證：

```bash
# Phase 1 驗證
python -m pytest tests/test_data_store.py -v
python scripts/import_data.py --source ../data
cat data/DATA_CATALOG.md  # 確認格式正確

# Phase 2 驗證
python -m pytest tests/test_strategies.py -v

# Phase 3 驗證
python -m pytest tests/test_param_scanner.py -v
python scripts/run_single.py --strategy trend_donchian --symbol BTCUSDT \
  --start 2024-01-01 --end 2024-12-31  # 確認可執行

# Phase 4 驗證
python -m pytest tests/test_reports.py -v
# 手動在瀏覽器開啟 reports/output/*.html 確認

# Phase 5 驗證
python scripts/run_optimize.py --strategies trend_donchian mean_reversion_bb \
  --symbols BTCUSDT --start 2024-01-01 --end 2024-12-31

# 全面驗證
python -m pytest tests/ -v --tb=short
```

---

## 3. 測試撰寫指南

### 3.1 測試結構

```python
# tests/test_strategies.py

class TestTrendDonchianVBT:
    """Donchian 策略測試。"""

    def test_entries_on_breakout(self):
        """當 close > donchian upper 且 ADX > threshold 時應產生進場信號。"""
        # 構造已知的 OHLCV 數據
        # 驗證 generate_entries() 在正確位置回傳 True

    def test_no_entry_low_adx(self):
        """當 ADX < threshold 時不應產生信號。"""

    def test_exits_on_donchian_lower(self):
        """當 close < exit donchian lower 時應產生出場信號。"""

    def test_warmup_period(self):
        """前 N 根 bar（不足計算指標）不應產生信號。"""

    def test_param_override(self):
        """自訂參數應覆蓋預設值。"""

    def test_full_backtest_runs(self):
        """使用真實資料跑完整回測不 crash，且 metrics 合理。"""
```

### 3.2 測試數據

- 小型測試：使用 `pd.DataFrame` 手工構造 50-100 根 bar
- 整合測試：使用 `data/` 中的真實資料（如果有的話）
- 邊界測試：空 DataFrame、單一 bar、全部相同價格

### 3.3 Parity Test（一致性測試）

```python
# tests/test_parity.py

class TestParity:
    """驗證 VBT 策略與母專案 event-driven 引擎的一致性。"""

    def test_donchian_trade_count_parity(self):
        """VBT 與母專案引擎的交易筆數差異 < 10%。
        
        注意: 完全一致不可能（向量化 vs event-driven 有固有差異），
        但數量級應相同。
        """

    def test_total_return_parity(self):
        """總回報差異 < 5%（允許因滑價/費用模型差異）。"""
```

---

## 4. 常見陷阱與避免方式

| 陷阱 | 說明 | 避免方式 |
|------|------|----------|
| 前視偏差 | 使用當前 bar 的 close 做進場判斷 | 所有 channel/indicator 都要 `.shift(1)` |
| 存活偏差 | 只用現在的幣種回測 | 使用回測期間存在的幣種 |
| 參數過擬合 | 掃描太多參數，找到的 "最佳" 只是過擬合 | Walk-Forward 驗證、OOS 測試 |
| 忽略手續費 | 回測看起來很棒，加上費用就虧 | 所有回測 MUST 包含 fees + slippage |
| VBT freq 錯誤 | freq 參數影響年化計算 | 確保 freq 與實際 K 線週期匹配 |
| NaN 處理 | 指標計算初期會有 NaN | `.fillna(False)` 在信號 Series 上 |
| DataFrame copy | 修改 ohlcv 影響原始資料 | 在函式開頭 `ohlcv = ohlcv.copy()` |

---

## 5. 執行順序總覽

```
Phase 1 (資料)
  ├─ data_manager/store.py
  ├─ data_manager/validator.py
  ├─ data_manager/importer.py
  ├─ data_manager/catalog.py
  └─ tests/test_data_store.py
      ↓ 驗證: 匯入資料 + 確認 DATA_CATALOG.md

Phase 2 (策略)
  ├─ strategies/base_vbt.py
  ├─ strategies/indicators/*.py
  ├─ strategies/trend_donchian_vbt.py
  ├─ strategies/mean_reversion_bb_vbt.py
  ├─ strategies/grid_futures_vbt.py
  ├─ strategies/funding_arb_vbt.py
  └─ tests/test_strategies.py
      ↓ 驗證: 信號正確 + 回測可運行

Phase 3 (引擎)
  ├─ engine/cost_model.py
  ├─ engine/runner.py
  ├─ engine/param_scanner.py
  ├─ engine/portfolio_optimizer.py
  └─ tests/test_param_scanner.py
      ↓ 驗證: 參數掃描 + 最佳化可運行

Phase 4 (報告)
  ├─ reports/charts.py
  ├─ reports/trade_log.py
  ├─ reports/tearsheet.py
  ├─ reports/templates/*.html
  ├─ reports/html_report.py
  └─ tests/test_reports.py
      ↓ 驗證: HTML 報告可正常開啟

Phase 5 (整合)
  ├─ scripts/run_single.py
  ├─ scripts/run_compare.py
  ├─ scripts/run_param_scan.py
  ├─ scripts/run_optimize.py
  ├─ scripts/import_data.py
  ├─ scripts/download_data.py
  └─ tests/test_parity.py
      ↓ 驗證: 全 CLI 可運行

Phase 6 (文件)
  ├─ README.md
  ├─ config/backtest_config.yaml
  └─ config/param_spaces.yaml
```
