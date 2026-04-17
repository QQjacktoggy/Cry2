# 🔧 回測優化與驗證計畫 (Optimization & Validation Plan)

> 版本: 1.0 | 建立日期: 2026-04-17
> 前置條件: `backtest_tool/` 已完成 Phase 1-6，54/54 測試通過
> 基準回測: 150 USDT，2024-04-17 → 2026-04-17，3 幣種 × 4 策略

---

## 📊 基準回測結果摘要 (Baseline)

| 策略 | BTC Return | ETH Return | SOL Return | 平均 Sharpe | 主要問題 |
|------|-----------|-----------|-----------|------------|---------|
| grid_futures | -3.75% | **+10.72%** | **+12.01%** | 0.30 | 最大回撤 45-75% 過高 |
| trend_donchian | -8.79% | -18.69% | -62.55% | -0.21 | 震盪市虧損嚴重，勝率 35% |
| mean_reversion_bb | -20.72% | -82.56% | -32.18% | -1.24 | ETH 崩潰，止損無效 |
| funding_arb | -31.89% | -35.13% | **+13.14%** | -0.46 | 交易太少(2-3筆)，樣本不足 |

**核心發現**: 只有 `grid_futures` 整體正向，所有策略的最大回撤都太高。

---

## 🗂️ 優化計畫總覽 (50 項)

計畫分為 **7 大階段**，每階段有明確的前置條件和驗收標準。

### 階段 A: 個別策略參數優化 (任務 1-12)
### 階段 B: 風控機制強化 (任務 13-20)
### 階段 C: 市場環境適應 (任務 21-28)
### 階段 D: 組合優化 (任務 29-35)
### 階段 E: 穩健性驗證 (任務 36-42)
### 階段 F: 進階策略改良 (任務 43-48)
### 階段 G: 上線前驗證 (任務 49-50)

---

## 階段 A: 個別策略參數優化

> **目標**: 找到每個策略在每個幣種上的最佳參數組合
> **工具**: `ParamScanner` + `run_param_scan.py`
> **前置條件**: 無
> **預期產出**: 每個策略 × 幣種的最佳參數 YAML + 掃描結果 CSV

### 任務 1: Grid Futures 參數掃描 — BTCUSDT

**背景**: Grid Futures 是目前最穩定的策略，但在 BTC 上仍虧損 -3.75%，需找到更好的參數。

**操作步驟**:
```bash
python -m backtest_tool.scripts.run_param_scan \
    --strategy grid_futures \
    --symbol BTCUSDT \
    --timeframe 4h \
    --start 2024-04-17 \
    --end 2026-04-17 \
    --target sharpe_ratio \
    --top 20
```

**掃描參數空間** (定義於 `config/param_spaces.yaml`):
- `grid_count`: [10, 15, 20, 30, 40, 50]  ← 增加 40, 50
- `range_period`: [10, 15, 20, 30, 40]  ← 新增此參數
- `ema_period`: [50, 100, 150, 200, 300]  ← 增加 50, 300
- `leverage`: [1, 2, 3]

**驗收標準**:
- [ ] Sharpe > 0.5 或 Return > 0% (BTC)
- [ ] 最大回撤 < 35%
- [ ] 交易次數 > 30 (統計顯著性)
- [ ] 掃描結果保存為 `reports/output/param_scan_grid_BTCUSDT.csv`

### 任務 2: Grid Futures 參數掃描 — ETHUSDT

**操作**: 同任務 1，替換 `--symbol ETHUSDT`

**驗收標準**:
- [ ] Sharpe > 0.5 (ETH 已有正收益，目標提升)
- [ ] 最大回撤 < 40%
- [ ] 結果保存為 `reports/output/param_scan_grid_ETHUSDT.csv`

### 任務 3: Grid Futures 參數掃描 — SOLUSDT

**操作**: 同任務 1，替換 `--symbol SOLUSDT`

**驗收標準**:
- [ ] Sharpe > 0.5
- [ ] 最大回撤 < 50% (SOL 波動大，允許稍高)
- [ ] 結果保存

### 任務 4: Trend Donchian 參數掃描 — 全幣種

**背景**: 勝率僅 35%，核心問題是:
1. `adx_threshold=25` 可能太低，讓太多假突破進場
2. `entry_period=20` 可能不適合 4h 週期
3. 缺乏趨勢強度的二次確認

**操作**:
```bash
# 針對每個幣種分別跑
for symbol in BTCUSDT ETHUSDT SOLUSDT; do
    python -m backtest_tool.scripts.run_param_scan \
        --strategy trend_donchian \
        --symbol $symbol \
        --timeframe 4h \
        --start 2024-04-17 \
        --target sharpe_ratio \
        --top 20
done
```

**擴展掃描空間** (修改 `param_spaces.yaml`):
```yaml
trend_donchian:
    entry_period: [10, 15, 20, 25, 30, 40, 55]
    exit_period: [5, 7, 10, 15, 20]
    adx_threshold: [20, 25, 30, 35, 40]    # 提高上限到 40
    atr_stop_mult: [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
    leverage: [1, 2, 3]
```

**驗收標準**:
- [ ] 至少 1 個幣種 Sharpe > 0.3
- [ ] 勝率提升至 > 40%
- [ ] 3 幣種的最佳參數記錄在結果 CSV

### 任務 5: Mean Reversion BB 參數掃描 — 全幣種

**背景**: ETH 上崩潰 -82%，核心問題:
1. `stop_loss_pct=1.5%` 在高波動環境太窄，頻繁被停損
2. `bb_std=2.0` 可能不夠寬，假信號多
3. RSI 閾值可能需要更極端 (< 25 / > 75)
4. 波動率過濾 (`atr_percentile`) 需要校正

**操作**: 分幣種掃描

**擴展掃描空間**:
```yaml
mean_reversion_bb:
    bb_period: [15, 20, 25, 30, 40]
    bb_std: [1.5, 2.0, 2.5, 3.0, 3.5]     # 加寬 BB
    rsi_period: [10, 14, 21]
    rsi_oversold: [15, 20, 25, 30]          # 更極端
    rsi_overbought: [70, 75, 80, 85]       # 更極端
    stop_loss_pct: [1.0, 1.5, 2.0, 3.0, 5.0]  # 核心！擴大止損
    atr_percentile: [30, 50, 70]
    leverage: [1, 2]
```

**驗收標準**:
- [ ] ETH 回測不再崩潰 (Return > -30%)
- [ ] 止損觸發後不會連續虧損 (連敗 < 10 次)
- [ ] 所有幣種 Sharpe > -0.5

### 任務 6: Funding Arb 參數掃描 — 全幣種

**背景**: 只有 2-3 筆交易，統計無意義。需要:
1. 降低 `funding_rate_threshold_annual` 讓更多機會進場
2. 縮短 `max_hold_days` 增加交易頻率

**擴展掃描空間**:
```yaml
funding_arb:
    funding_rate_threshold_annual: [5, 8, 10, 12, 15, 20]  # 降低門檻
    funding_rate_exit_annual: [1, 2, 3, 5, 7]
    max_hold_days: [1, 2, 3, 5, 7, 14]
    leverage: [1, 2]
```

**驗收標準**:
- [ ] 交易次數 > 10 (統計最低要求)
- [ ] SOL 維持正收益
- [ ] 結果保存

### 任務 7: 時間框架交叉驗證

**背景**: 每個策略目前綁定固定 timeframe，但可能不是最佳的。

**操作**: 對每個策略測試多個 timeframe
```
trend_donchian: 1h, 2h, 4h, 8h
mean_reversion_bb: 15m, 30m, 1h, 4h
grid_futures: 1h, 2h, 4h, 8h
funding_arb: 4h, 8h (受限於 funding rate 頻率)
```

**實作方式**: 修改 `run_all_2yr.py` 加入 timeframe 迴圈，或寫新腳本 `scripts/run_tf_scan.py`

**驗收標準**:
- [ ] 每策略找出最佳 timeframe
- [ ] 結果表格記錄: 策略 × TF × 幣種 → Sharpe/Return/MaxDD

### 任務 8: 最佳參數交叉幣種驗證

**背景**: 任務 1-6 是分幣種獨立掃描，需驗證最佳參數是否跨幣種有效。

**操作**:
1. 取 BTC 最佳參數，在 ETH/SOL 上跑
2. 取 ETH 最佳參數，在 BTC/SOL 上跑
3. 取 SOL 最佳參數，在 BTC/ETH 上跑

**驗收標準**:
- [ ] 跨幣種 Sharpe 降幅 < 50% 視為穩健
- [ ] 如果降幅 > 50%，標記為「幣種專屬參數」
- [ ] 產出交叉驗證矩陣表

### 任務 9: 最佳參數合併設定檔

**背景**: 將任務 1-8 的結果整合。

**操作**:
1. 建立 `config/optimized_params.yaml`
2. 格式:
```yaml
optimized_params:
  grid_futures:
    BTCUSDT: {grid_count: X, range_period: Y, ema_period: Z, leverage: N}
    ETHUSDT: {grid_count: X, ...}
    SOLUSDT: {grid_count: X, ...}
    universal: {grid_count: X, ...}  # 跨幣種穩健參數
  trend_donchian:
    ...
```

**驗收標準**:
- [ ] YAML 結構完整，每策略 × 每幣種 + universal
- [ ] 使用 universal 參數重跑回測，記錄基準

### 任務 10: 優化前後對比報告

**操作**:
1. 用優化後參數重跑全部回測
2. 用 `run_compare.py` 對比 default vs optimized
3. 產出 HTML 對比報告

**驗收標準**:
- [ ] HTML 報告中清楚標示 before/after
- [ ] 至少 2/3 幣種有 Sharpe 改善
- [ ] 最大回撤有下降

### 任務 11: 添加 `range_period` 參數到 Grid Futures

**背景**: 目前 `range_period` 硬編碼為 20，需要讓它可配置。

**操作**: 修改 `grid_futures_vbt.py`
```python
default_params = {
    "grid_count": 20,
    "range_period": 20,  # 已存在但需確認 _compute_channel 使用 self.params
    "ema_period": 200,
    "leverage": 2,
}
```

**驗收標準**:
- [ ] `range_period` 在 param_spaces.yaml 可掃描
- [ ] 單元測試通過

### 任務 12: 添加 `stop_loss_pct` 到 Trend Donchian

**背景**: 目前 Trend Donchian 只用 Donchian Lower/Upper 作為出場，沒有固定止損。

**操作**:
1. 參考 `mean_reversion_bb_vbt.py` 的 `run_backtest` override
2. 添加 `sl_stop` 參數 (ATR-based 或固定百分比)
3. 添加到 param_spaces.yaml

**驗收標準**:
- [ ] 止損觸發時正確平倉
- [ ] 最大回撤預期下降 10-20%
- [ ] 測試通過

---

## 階段 B: 風控機制強化

> **目標**: 降低所有策略的最大回撤至可接受範圍
> **前置條件**: 階段 A 完成（有最佳基礎參數）
> **預期產出**: 新的風控模組 + 回撤改善報告

### 任務 13: 全局止損機制 (Portfolio-Level Stop)

**背景**: 即使單策略有止損，組合層面缺乏保護。當連續虧損時，應降低整體曝險。

**操作**:
1. 在 `engine/` 新增 `risk_manager.py`
2. 實作 `PortfolioStopLoss`:
   ```python
   class PortfolioStopLoss:
       def __init__(self, max_drawdown_pct: float = 20.0, cooldown_bars: int = 48):
           """
           max_drawdown_pct: 組合回撤超過此值，暫停所有交易
           cooldown_bars: 暫停交易的 bar 數 (48 bars × 4h = 8 天)
           """
   ```
3. 整合到 `BacktestRunner`

**驗收標準**:
- [ ] 回撤觸發後交易正確暫停
- [ ] 冷卻期結束後恢復交易
- [ ] 整體最大回撤降低

### 任務 14: 動態倉位管理 (Position Sizing)

**背景**: 目前每次交易使用 100% 可用資金，風險過高。

**操作**:
1. 在 `base_vbt.py` 的 `run_backtest` 中加入 `size` 和 `size_type` 參數
2. 實作三種模式:
   - `fixed_fraction`: 每次用 X% 的總資金 (預設 50%)
   - `kelly`: Kelly Criterion 動態計算
   - `atr_based`: 根據 ATR 調整倉位大小 (波動大 → 倉位小)

**具體修改** (`base_vbt.py`):
```python
# 在 run_backtest() 的 kwargs 中加入:
if size_mode == "fixed_fraction":
    kwargs["size"] = fraction  # e.g., 0.5
    kwargs["size_type"] = "percent"
elif size_mode == "atr_based":
    # ATR-based sizing: risk_per_trade / (ATR * multiplier)
    kwargs["size"] = computed_size_series
    kwargs["size_type"] = "amount"
```

**驗收標準**:
- [ ] `fixed_fraction=0.5` 時，最大回撤降低 30%+
- [ ] Kelly 模式不會出現倉位 > 100%
- [ ] 三種模式都有對應測試

### 任務 15: 連續虧損保護 (Consecutive Loss Guard)

**操作**:
1. 策略層面: 連續 N 次虧損後，暫停交易 M 根 bar
2. 修改 `base_vbt.py` 或在 entries/exits 生成後加 post-processing

**驗收標準**:
- [ ] `max_consecutive_losses=5, pause_bars=24` 作為預設
- [ ] 歷史回測中有效避開大回撤事件

### 任務 16: 波動率自適應槓桿

**操作**:
1. 高波動率 (ATR percentile > 80) → 降低槓桿至 1x
2. 低波動率 (ATR percentile < 20) → 允許最高槓桿
3. 在策略層面實作為 `leverage_series: pd.Series`

**驗收標準**:
- [ ] 2022 年 LUNA 崩盤期間槓桿自動降低 (如有該時段資料)
- [ ] Sharpe 改善或至少不下降

### 任務 17: 最大持倉時間限制 (Stale Position Exit)

**背景**: 某些交易持倉過久，佔用資金但無收益。

**操作**:
1. 每個策略增加 `max_hold_bars` 參數
2. 超過 N bars 未觸發正常出場 → 強制市價平倉

**建議值**:
- trend_donchian: 120 bars (20 天 × 4h)
- mean_reversion_bb: 168 bars (7 天 × 1h)
- grid_futures: 240 bars (40 天 × 4h)
- funding_arb: 已有 `max_hold_days`

**驗收標準**:
- [ ] 平均持倉天數下降
- [ ] 不影響獲利交易的出場

### 任務 18: 夜間/週末波動保護

**背景**: 加密市場 24/7，但某些時段流動性低、波動異常。

**操作**:
1. 統計歷史資料: 按小時/星期幾 groupby 計算平均波動率和收益
2. 找出高風險時段 (例如 UTC 週日 0-8)
3. 在高風險時段停止開新倉

**實作**: 在 entries Series 上加一個 time mask

**驗收標準**:
- [ ] 產出「每小時/每日收益熱力圖」
- [ ] 高風險時段辨識報告
- [ ] 過濾後 Sharpe 改善

### 任務 19: 回撤恢復速度指標

**操作**:
1. 在 `_extract_metrics` 加入:
   - `max_dd_recovery_bars`: 最大回撤的恢復時間 (bars)
   - `avg_dd_recovery_bars`: 平均回撤恢復時間
   - `ulcer_index`: 回撤的痛苦指標

**驗收標準**:
- [ ] metrics dict 包含新指標
- [ ] HTML 報告中顯示

### 任務 20: 風控效果驗證報告

**操作**: 對比 階段 A 最佳參數 vs 階段 A + 階段 B 風控
- 產出前後對比 HTML 報告
- 特別觀察: 最大回撤、Calmar Ratio、連續虧損次數

**驗收標準**:
- [ ] 最大回撤降低 > 20%
- [ ] Sharpe 不因風控而大幅下降 (降幅 < 15%)

---

## 階段 C: 市場環境適應

> **目標**: 讓策略根據市場環境自動切換或調整
> **前置條件**: 階段 B 完成
> **預期產出**: 市場分類模組 + 環境感知策略

### 任務 21: 市場環境分類器 (Market Regime Detector)

**操作**:
1. 新增 `strategies/market_regime.py`
2. 分類四種環境:
   - `trending_up`: ADX > 25 且 EMA50 > EMA200
   - `trending_down`: ADX > 25 且 EMA50 < EMA200
   - `ranging`: ADX < 20
   - `volatile`: ATR percentile > 80
3. 產出 `regime: pd.Series` 對應每根 bar

**驗收標準**:
- [ ] 回測歷史資料上分類結果視覺化
- [ ] 各環境佔比合理 (趨勢 30-40%, 盤整 40-50%, 高波動 10-20%)

### 任務 22: 環境感知策略選擇器

**操作**:
1. 新增 `engine/strategy_selector.py`
2. 邏輯:
   ```
   trending_up → trend_donchian (long bias)
   trending_down → trend_donchian (short bias) 或 funding_arb
   ranging → grid_futures + mean_reversion_bb
   volatile → 降低倉位 或 暫停交易
   ```

**驗收標準**:
- [ ] 自動選擇比固定策略 Sharpe 高
- [ ] 切換頻率合理 (不要每根 bar 都切)

### 任務 23: 策略間相關性分析

**操作**:
1. 計算 4 策略的 daily return 相關矩陣
2. 找出低相關策略對 (用於組合優化)
3. 產出 correlation heatmap

**驗收標準**:
- [ ] 產出相關矩陣 + heatmap (Plotly)
- [ ] 找出相關性 < 0.3 的策略對

### 任務 24: 滾動窗口 Sharpe 分析

**操作**:
1. 計算每個策略的 30 天/90 天滾動 Sharpe
2. 視覺化: 哪些時段策略表現好/差
3. 判斷策略是否有結構性衰退

**驗收標準**:
- [ ] Plotly 滾動 Sharpe 圖表
- [ ] 標記 Sharpe < 0 的衰退期

### 任務 25: 成交量/流動性過濾器

**背景**: 低流動性時段交易滑價更高。

**操作**:
1. 計算 volume 的 rolling percentile
2. volume < 20th percentile → 不開新倉
3. 在 entries 上加 volume mask

**驗收標準**:
- [ ] 過濾前後 Sharpe 對比
- [ ] 滑價估計更準確

### 任務 26: 多時間框架確認 (MTF Confirmation)

**背景**: 4h 入場信號 + 日線方向確認 → 減少假突破。

**操作**:
1. Trend Donchian: 4h 突破 + 1D ADX 趨勢方向一致 → 開倉
2. Mean Reversion: 1h 超賣 + 4h 仍在 BB 帶內 → 開倉
3. 需要同時載入多個 timeframe 的資料

**實作**: 修改策略的 `generate_entries()` 接受額外的 `higher_tf_data` 參數

**驗收標準**:
- [ ] 勝率提升 5%+
- [ ] 交易次數減少但品質提升 (avg PnL per trade ↑)

### 任務 27: 季節性模式分析

**操作**:
1. 按月份 groupby 計算每個策略的平均收益
2. 判斷是否有明顯的季節性 (例如 Q1 表現好)
3. 產出月度績效分析報告

**驗收標準**:
- [ ] 月度分析 HTML 報告
- [ ] 如果存在季節性，標記最佳/最差月份

### 任務 28: 環境適應效果驗證

**操作**: 對比 階段 B 結果 vs 階段 C 環境適應版本
- 重點觀察: 策略切換是否有效降低回撤

**驗收標準**:
- [ ] 產出完整對比報告
- [ ] 環境感知版本 Sharpe > 靜態版本

---

## 階段 D: 組合優化

> **目標**: 找到多策略最佳資金配置
> **前置條件**: 階段 A-C 完成（每個策略已有最佳配置）
> **預期產出**: 最佳組合配置 + 再平衡策略

### 任務 29: 等權重 vs 最佳化配置對比

**操作**:
1. 等權重: 4 策略各 25%
2. Sharpe 最佳化: 用 `PortfolioOptimizer`
3. Calmar 最佳化: `target_metric=calmar_ratio`
4. 最小回撤: `target_metric=max_drawdown`

```bash
python -m backtest_tool.scripts.run_optimize \
    --strategies grid_futures trend_donchian mean_reversion_bb funding_arb \
    --symbol BTCUSDT \
    --timeframes 4h 4h 1h 8h \
    --start 2024-04-17 \
    --target sharpe_ratio
```

**驗收標準**:
- [ ] 4 種配置方式的結果對比表
- [ ] 最佳化配置 Sharpe > 等權重配置

### 任務 30: 分幣種組合優化

**操作**: 對 BTC/ETH/SOL 各自求最佳配置

**驗收標準**:
- [ ] 3 幣種各有最佳配置
- [ ] 記錄是否有策略被配置為 0% (表示該策略在該幣種無效)

### 任務 31: 跨幣種資金配置

**背景**: 不僅策略間分配，還要決定 BTC/ETH/SOL 的資金比例。

**操作**:
1. 將每個「策略 × 幣種」視為一個獨立資產
2. 最多 12 個資產 (4 策略 × 3 幣種)
3. 用 `PortfolioOptimizer` 做 12 維配置

**注意**: 12 維 Cartesian product 太大 (step=5% → 超過百萬組合)
→ 解決: 先篩選 top 6 表現最好的「策略×幣種」，再做 6 維優化

**驗收標準**:
- [ ] 找出 top 6 候選
- [ ] 6 維最佳配置 + Sharpe

### 任務 32: 動態再平衡策略

**操作**:
1. 實作 `engine/rebalancer.py`
2. 策略:
   - `fixed_interval`: 每 N 天重新平衡
   - `threshold`: 偏離目標配置 > X% 時再平衡
   - `performance`: 表現好的策略加碼，差的減碼
3. 在回測中模擬再平衡的摩擦成本

**驗收標準**:
- [ ] 3 種再平衡策略的回測對比
- [ ] 最佳再平衡頻率 (7天? 14天? 30天?)

### 任務 33: 風險平價 (Risk Parity) 配置

**操作**:
1. 根據每個策略的歷史波動率反向配置
2. 波動率高的策略 → 低配置
3. 實作 `PortfolioOptimizer.risk_parity()` 方法

**驗收標準**:
- [ ] 組合波動率均勻分佈
- [ ] Sharpe 與 brute-force 對比

### 任務 34: 最小資金門檻分析

**背景**: 150 USDT 是否太少？部分策略可能因資金不足而表現異常。

**操作**:
1. 用 [100, 150, 300, 500, 1000, 5000, 10000] USDT 分別回測
2. 觀察 Sharpe 和 Return 在不同資金量下的變化
3. 找出每個策略的「最低有效資金」

**驗收標準**:
- [ ] 資金-績效曲線圖
- [ ] 每策略最低有效資金標記

### 任務 35: 組合優化效果驗證

**操作**: 最佳組合 vs 單策略 vs 等權重，完整對比 HTML 報告

**驗收標準**:
- [ ] 最佳組合 Sharpe > 任何單策略
- [ ] 最大回撤低於最差單策略的回撤

---

## 階段 E: 穩健性驗證

> **目標**: 確保優化結果不是 overfitting
> **前置條件**: 階段 D 完成
> **預期產出**: Walk-forward 報告 + Monte Carlo 分析

### 任務 36: Walk-Forward 分析

**這是最重要的穩健性測試。**

**操作**:
1. 新增 `engine/walk_forward.py`
2. 方法:
   ```
   全部資料: 2023-01-01 → 2026-04-17 (約 40 個月)
   窗口: 12 個月 train + 3 個月 test
   步進: 3 個月
   
   第 1 輪: Train 2023-01 → 2023-12, Test 2024-01 → 2024-03
   第 2 輪: Train 2023-04 → 2024-03, Test 2024-04 → 2024-06
   第 3 輪: Train 2023-07 → 2024-06, Test 2024-07 → 2024-09
   ...
   ```
3. 每輪: 在 train 上做參數掃描 → 取最佳參數 → 在 test 上驗證
4. 統計 test 期間的平均 Sharpe

**驗收標準**:
- [ ] Walk-forward Sharpe > 0 (out-of-sample 有效)
- [ ] Train Sharpe vs Test Sharpe 差距 < 50%
- [ ] 產出 walk-forward 報告 (每輪的 train/test 績效)

### 任務 37: In-Sample vs Out-of-Sample 分割驗證

**操作**:
1. 50/50 分割: 2023-01 ~ 2024-06 (IS) / 2024-07 ~ 2026-04 (OOS)
2. 70/30 分割: 2023-01 ~ 2025-03 (IS) / 2025-04 ~ 2026-04 (OOS)
3. 在 IS 上優化參數，在 OOS 上驗證

**驗收標準**:
- [ ] OOS 表現 > 基線 (default params)
- [ ] IS-OOS 績效差異 < 40%

### 任務 38: Monte Carlo 模擬

**操作**:
1. 新增 `engine/monte_carlo.py`
2. 方法:
   - 取實際交易的 PnL 序列
   - 隨機打亂順序 1000 次
   - 計算每次的最終資產、最大回撤
   - 計算 5th/25th/50th/75th/95th percentile
3. 用途: 量化「運氣」的影響

**驗收標準**:
- [ ] 95% CI 的最大回撤估計
- [ ] 實際結果在 MC 分佈中的位置

### 任務 39: 參數穩定性熱力圖

**操作**:
1. 在參數掃描結果中，分析「鄰近參數的 Sharpe 變化」
2. 如果最佳參數 Sharpe=1.5，但旁邊的參數 Sharpe=-0.5 → 不穩定 (可能 overfit)
3. 好的參數: 鄰近區域 Sharpe 也不錯 (平滑)

**實作**: 從 ParamScanner 結果 DataFrame 計算 2D 熱力圖

**驗收標準**:
- [ ] 每個策略的 2D 參數穩定性熱力圖
- [ ] 標記穩定區域 vs 不穩定區域

### 任務 40: 壓力測試 (Stress Testing)

**操作**:
1. 找出歷史上的極端事件:
   - 2024-04 BTC 大跌 (if in data)
   - 2025-xx 任何大波動期間
2. 單獨在極端事件期間 ±7 天跑回測
3. 觀察策略是否倖存

**驗收標準**:
- [ ] 每個極端事件的獨立回測
- [ ] 單事件最大虧損 < 初始資金的 30%

### 任務 41: 手續費敏感度分析

**操作**:
1. 用 [0, 0.02%, 0.04%, 0.06%, 0.1%] fee rate 分別跑
2. 觀察 Sharpe 對手續費的敏感度
3. 高頻策略 (mean_reversion_bb, 252 trades) 最敏感

**驗收標準**:
- [ ] fee rate vs Sharpe 曲線圖
- [ ] 找出每個策略的「盈虧平衡手續費」

### 任務 42: 穩健性驗證總結報告

**操作**: 彙整 任務 36-41 的結果，產出:
1. 「策略信心評分」(0-10)
2. 每策略的 Overfit 風險等級 (低/中/高)
3. 最終推薦配置

**驗收標準**:
- [ ] 信心評分合理 (Walk-forward 通過 = 高分)
- [ ] 有明確的「可上線」vs「需改進」標記

---

## 階段 F: 進階策略改良

> **目標**: 基於回測結果改進策略邏輯
> **前置條件**: 階段 E 的穩健性分析結果
> **預期產出**: 改良版策略程式碼 + 對比報告

### 任務 43: Trend Donchian 添加趨勢強度過濾

**背景**: 勝率 35% 太低，太多假突破。

**操作**:
1. 添加 Volume Breakout Confirmation:
   - 突破時 volume > 20-bar 平均 volume × 1.5 → 才確認進場
2. 添加 EMA Slope Filter:
   - EMA20 斜率 > 0 才做多，< 0 才做空
3. 修改 `trend_donchian_vbt.py` 的 `generate_entries()`

**驗收標準**:
- [ ] 勝率提升至 > 42%
- [ ] 平均獲利/虧損比 (payoff ratio) > 2.0
- [ ] 測試通過

### 任務 44: Mean Reversion BB 改用 ATR Trailing Stop

**背景**: 固定 1.5% 止損在不同波動環境下不合理。

**操作**:
1. 替換固定止損為 ATR-based trailing stop:
   ```
   stop_price = entry_price - N * ATR  (做多)
   stop_price = entry_price + N * ATR  (做空)
   ```
2. Trailing: 隨著獲利移動止損
3. 修改 `run_backtest()` override

**驗收標準**:
- [ ] ETH 回撤從 -82% 降至 < -40%
- [ ] SOL 上仍然可用

### 任務 45: Grid Futures 動態格距

**背景**: 固定 20 格可能不適合所有行情。

**操作**:
1. ATR-adaptive grid count:
   - ATR 高 → 格距大 → 格數少 (10-15 格)
   - ATR 低 → 格距小 → 格數多 (30-50 格)
2. 修改 `_compute_channel()` 使用動態 range_period

**驗收標準**:
- [ ] 動態格距 vs 固定格距 Sharpe 對比
- [ ] 高波動期間不會過度交易

### 任務 46: Funding Arb 加入多空平衡

**背景**: 只做空單，單邊風險太大。

**操作**:
1. 做空期貨的同時，模擬持有現貨 (delta neutral)
2. 修改 PnL 計算: 期貨 PnL + 現貨 PnL = 淨 funding 收益
3. 需要在 `prepare_data()` 加入 spot 模擬

**驗收標準**:
- [ ] 回撤大幅降低 (因為 delta neutral)
- [ ] 正收益來源主要是 funding rate

### 任務 47: 新策略探索 — 動量反轉 (Momentum Reversal)

**背景**: 現有 4 策略不夠多元。

**操作**:
1. 新增 `strategies/momentum_reversal_vbt.py`
2. 邏輯:
   - 計算 N 天收益率排名
   - 過去 N 天漲最多的 → 做空 (反轉預期)
   - 過去 N 天跌最多的 → 做多
   - 適合 1D timeframe
3. 加入 `STRATEGY_MAP`

**驗收標準**:
- [ ] 新策略 + 測試
- [ ] 與現有策略相關性 < 0.3

### 任務 48: 策略改良對比驗證

**操作**: 對比所有改良前後的策略
- Original 4 策略 vs Improved 4 策略 + 1 新策略
- Walk-forward 驗證改良版

**驗收標準**:
- [ ] 至少 3/4 策略有改善
- [ ] 新策略通過 walk-forward 測試

---

## 階段 G: 上線前驗證

> **目標**: 最終確認優化結果可用於實盤
> **前置條件**: 全部前置階段完成
> **預期產出**: 最終部署建議書

### 任務 49: 完整最終回測

**操作**:
1. 使用所有優化結果 (最佳參數 + 風控 + 環境適應 + 組合配置)
2. 全時段 2023-01-01 → 2026-04-17 回測
3. Walk-forward 驗證
4. 產出完整 HTML 報告

**報告內容**:
- 最終推薦策略組合
- 各策略最佳參數
- 資金配置比例
- 風控設定
- 預期 Sharpe / 年化收益 / 最大回撤
- 信心等級

**驗收標準**:
- [ ] Walk-forward OOS Sharpe > 0.3
- [ ] 組合最大回撤 < 30%
- [ ] 年化收益 > 5%
- [ ] 報告完整且可讀

### 任務 50: 部署建議書與監控計畫

**操作**: 產出 `DEPLOYMENT_RECOMMENDATION.md`

**內容**:
```
1. 推薦策略組合
   - 策略名稱、參數、時間框架
   - 資金配置比例
   - 幣種

2. 風控設定
   - 最大回撤停損線
   - 連續虧損暫停規則
   - 倉位管理模式

3. 監控指標
   - 每日應監控的指標 (equity, drawdown, trade count)
   - 異常告警門檻 (回撤 > X%, 連虧 > N 筆)
   - 策略失效判斷條件 (30天滾動 Sharpe < -1)

4. 維護計畫
   - 每月: 下載最新資料，重跑回測確認參數仍有效
   - 每季: Walk-forward 重新優化參數
   - 每半年: 全面 review，考慮新策略

5. 風險提示
   - 回測不等於實盤
   - 滑價可能高於模型估計
   - 極端市場可能超過歷史最大回撤
```

**驗收標準**:
- [ ] 文件完整、可執行
- [ ] 包含所有監控指標和門檻值
- [ ] 包含失效應對方案

---

## 📋 執行順序與依賴關係

```
階段 A (1-12): 參數優化    ──────┐
                                  ├→ 階段 D (29-35): 組合優化 ──┐
階段 B (13-20): 風控強化    ──────┤                              │
                                  ├→ 階段 E (36-42): 穩健性驗證 ─┤
階段 C (21-28): 環境適應    ──────┘                              │
                                                                  │
                              階段 F (43-48): 進階改良 ←──────────┘
                                        │
                                        ▼
                              階段 G (49-50): 上線驗證
```

**最高優先級** (Quick Wins):
1. 任務 1-3: Grid Futures 參數掃描 (目前最好的策略)
2. 任務 5: Mean Reversion 止損修復 (ETH -82% 太慘)
3. 任務 14: 固定比例倉位管理 (最簡單有效的風控)
4. 任務 36: Walk-Forward (決定一切是否有效)

---

## ⚙️ 工具使用指南

### 參數掃描
```bash
python -m backtest_tool.scripts.run_param_scan \
    --strategy <strategy_name> \
    --symbol <SYMBOL> \
    --timeframe <tf> \
    --start 2024-04-17 \
    --end 2026-04-17 \
    --target sharpe_ratio \
    --top 20
```

### 組合優化
```bash
python -m backtest_tool.scripts.run_optimize \
    --strategies grid_futures trend_donchian mean_reversion_bb \
    --symbol BTCUSDT \
    --timeframes 4h 4h 1h \
    --start 2024-04-17 \
    --target sharpe_ratio
```

### 新增模組位置
| 模組 | 路徑 |
|------|------|
| 市場分類器 | `strategies/market_regime.py` |
| 風控管理 | `engine/risk_manager.py` |
| 再平衡 | `engine/rebalancer.py` |
| Walk-Forward | `engine/walk_forward.py` |
| Monte Carlo | `engine/monte_carlo.py` |
| 策略選擇器 | `engine/strategy_selector.py` |

### 關鍵檔案參考
| 用途 | 檔案 |
|------|------|
| 策略基底類別 | `strategies/base_vbt.py` |
| 回測引擎 | `engine/runner.py` |
| 參數空間定義 | `config/param_spaces.yaml` |
| 全域設定 | `config/backtest_config.yaml` |
| HTML 報告產生 | `reports/html_report.py` |
| 資料管理 | `data_manager/store.py` |

---

## 📝 給接手代理人的注意事項

1. **先跑測試**: `python -m pytest backtest_tool/tests/ -v` 確認 54/54 通過
2. **VBT 版本**: 使用 vectorbt 0.28.5，注意 `direction` 警告是無害的
3. **pandas-ta 不能用**: 已改用 `ta` 庫，勿切換回去
4. **metrics 已轉換為百分比**: `total_return=10.0` 代表 10%，不是 0.10
5. **DataStore 路徑**: 預設指向 `backtest_tool/data/`，不是 `./data/`
6. **Incremental download**: 下載新資料會自動 dedup + merge
7. **HTML 報告**: 自動輸出到 `backtest_tool/reports/output/`
8. **Jinja2 陷阱**: 模板中 dict 的 `values` key 要用 `row["values"]` 不能用 `row.values`
