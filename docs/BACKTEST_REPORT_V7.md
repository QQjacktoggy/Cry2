# 📊 Cry2 V7.4 完整回測報告

**版本**: V7.4（live-aligned baseline，已套用 breakout 接線修正 + ETH ADX 1x 候選）  
**日期**: 2026-04-22  
**初始資金**: 150 USDT  
**回測期間**: 2023-01-01 ~ 2026-04-20（1,206 個日頻觀測點）  
**資料來源**: `backtest_tool/data/klines` Binance Futures 4h / 1d K 線  
**正式 runner**: `python -m backtest_tool.scripts.full_portfolio_backtest`  
**配置來源**: `src/bot/strategy/bridge.py` 的 `V74_CONFIG`  
**輸出檔案**: `backtest_tool/reports/output/portfolio_backtest_results.csv`、`portfolio_combined_equity.csv`

> 本文件以 **目前 repo 內實際可跑的 V7.4 baseline** 為準，已納入本次重新回測、breakout 參數接線修正、以及 `trend_donchian_adx_slope_eth` 的 1x 優化替換結果。

---

## 1. 執行摘要

### 1.1 核心結果

| 指標 | 目標 | V7.4 結果 | 狀態 |
|------|------|-----------|------|
| Sharpe Ratio | > 0.8 | **1.984** | ✅ |
| 年化報酬率 | > 25% | **46.6%** | ✅ |
| 最大回撤 | < 20% | **-15.5%** | ✅ |
| Calmar Ratio | > 1.0 | **3.01** | ✅ |
| Walk-Forward | 通過 | **9/9 視窗中 8 個 OOS 為正，整體 Passed** | ✅ |
| 手續費安全性 | SAFE / OK | **SAFE，4.41x** | ✅ |

### 1.2 投資績效概覽

| 項目 | 數值 |
|------|------|
| 初始資金 | **$150** |
| 最終價值 | **$530** |
| 累計報酬 | **+253.7%** |
| 年化報酬 | **+46.6%** |
| Sharpe | **1.984** |
| Sortino | **2.960** |
| Max Drawdown | **-15.5%** |
| Calmar | **3.01** |
| 平均策略相關性 | **0.0643** |
| 總交易次數 | **620** |
| 手續費安全倍數 | **4.41x** |
| 策略位 | **16** |
| 幣種 | **BTC / ETH / BNB / XRP / SOL** |
| 最終判定 | **DEPLOYABLE（5/5 checks passed）** |

### 1.3 一句話結論

V7.4 目前已從原本「live-aligned 但回撤偏深」的版本，收斂成 **報酬略降、但 Sharpe / MaxDD 顯著改善** 的可部署 baseline；本輪最有效的優化不是 breakout，而是 **把 ETH ADX 從 2x 換成更健康的 1x 候選**。

---

## 2. 回測方法與本次重跑範圍

### 2.1 本次重跑做了什麼

1. 使用正式 baseline runner：`python -m backtest_tool.scripts.full_portfolio_backtest`
2. 直接讀取 live 端 `V74_CONFIG`，避免報告與部署配置脫鉤
3. 使用 `full_portfolio_backtest.py` 內的 **explicit return-amplification leverage model**
4. 重新執行弱勢策略參數掃描，再把確定有效的候選參數套回 baseline 重跑

### 2.2 這份報告相對舊版的更新點

- 不再是「V7.3 主文 + V7.4 附註」混合文件
- breakout `kc_ema` / `kc_atr` 接線錯誤已修正
- `trend_donchian_adx_slope_eth` 已從舊 2x 配置換成 1x 候選
- 所有總表、策略表、WF / MC / fee / correlation 已同步到最新重跑結果

### 2.3 槓桿語義

V7.4 的槓桿欄位應解讀為 **策略目標槓桿設定**。  
`full_portfolio_backtest.py` 已不再依賴 raw `vectorbt` `size_type="percent"` 直接表達 broker-style >1x 槓桿，而是顯式放大策略報酬路徑。

---

## 3. V7.4 組合結構

### 3.1 三層配置

| 層級 | 配置 | 說明 |
|------|------|------|
| Robust | **40%** | `trend_donchian` 家族；其中 ETH ADX 已改為 1x 健康版 |
| Moderate / Defensive | **48%** | `momentum_ranking`、`grid_trend_bias`、`tail_risk_hedge` |
| Fragile | **12%** | `grid_trend_bias_eth` + `breakout_squeeze_btc` |

### 3.2 幣種曝險

| 幣種 | 配置 |
|------|------|
| BTC | **34%** |
| ETH | **31%** |
| BNB | **15%** |
| XRP | **10%** |
| SOL | **10%** |

### 3.3 時間框架與目標槓桿

| 維度 | 數值 |
|------|------|
| 4h 策略配置 | **65%** |
| 1d 策略配置 | **35%** |
| 2x 目標槓桿配置 | **30%** |
| 1x 目標槓桿配置 | **70%** |
| 配置加權平均目標槓桿 | **1.30x** |

### 3.4 分層貢獻

| 層級 | 配置 | 初始資金 | 最終價值 | 損益 | 層內報酬 |
|------|------|----------|----------|------|----------|
| Robust | 40% | $60.0 | $151.6 | $91.6 | **+152.6%** |
| Moderate / Defensive | 48% | $72.0 | $337.4 | $265.4 | **+368.6%** |
| Fragile | 12% | $18.0 | $41.5 | $23.5 | **+130.7%** |

**解讀**：組合主要 alpha 仍來自 Moderate / Defensive 層；ETH ADX 改成 1x 後，Robust 層的報酬貢獻略降，但整體風險品質明顯提升。

---

## 4. 個別策略結果

### 4.1 全策略明細

| 策略位 | Symbol | TF | 配置 | 初始資金 | Final | Return | Sharpe | MaxDD | Calmar | Trades | Win% |
|--------|--------|----|------|----------|-------|--------|--------|-------|--------|--------|------|
| trend_donchian_mtf_btc | BTCUSDT | 4h | 16% | $24.0 | $51.8 | 116.0% | 1.018 | -23.5% | 1.12 | 7 | 71.4% |
| trend_donchian_adx_slope_eth | ETHUSDT | 4h | 10% | $15.0 | $48.8 | 225.5% | 1.142 | -30.5% | 1.41 | 118 | 42.4% |
| trend_donchian_adx_slope_btc | BTCUSDT | 4h | 8% | $12.0 | $18.7 | 55.4% | 0.598 | -72.5% | 0.20 | 110 | 43.6% |
| trend_donchian_mtf_xrp | XRPUSDT | 4h | 4% | $6.0 | $24.1 | 301.8% | 1.068 | -39.5% | 1.33 | 5 | 80.0% |
| trend_donchian_mtf_bnb | BNBUSDT | 4h | 2% | $3.0 | $8.1 | 171.0% | 1.011 | -31.4% | 1.12 | 10 | 70.0% |
| momentum_ranking_eth | ETHUSDT | 1d | 12% | $18.0 | $116.0 | 544.6% | 1.446 | -46.6% | 1.63 | 10 | 90.0% |
| momentum_ranking_bnb | BNBUSDT | 1d | 8% | $12.0 | $87.9 | 632.4% | 1.535 | -33.1% | 2.50 | 8 | 87.5% |
| momentum_ranking_sol | SOLUSDT | 1d | 3% | $4.5 | $34.0 | 655.8% | 1.262 | -47.7% | 1.77 | 33 | 42.4% |
| grid_trend_bias_xrp | XRPUSDT | 4h | 6% | $9.0 | $16.0 | 77.5% | 1.117 | -23.9% | 0.79 | 37 | 73.0% |
| tail_risk_hedge_bnb | BNBUSDT | 1d | 5% | $7.5 | $17.6 | 135.1% | 1.427 | -15.5% | 1.91 | 12 | 83.3% |
| tail_risk_hedge_sol | SOLUSDT | 1d | 4% | $6.0 | $34.3 | 472.3% | 1.243 | -39.3% | 1.77 | 59 | 62.7% |
| grid_trend_bias_btc | BTCUSDT | 4h | 4% | $6.0 | $8.1 | 35.2% | 1.327 | -7.4% | 1.29 | 16 | 75.0% |
| grid_trend_bias_sol | SOLUSDT | 4h | 3% | $4.5 | $14.6 | 224.6% | 1.224 | -31.4% | 1.36 | 96 | 72.9% |
| tail_risk_hedge_btc | BTCUSDT | 1d | 3% | $4.5 | $8.8 | 95.0% | 1.241 | -12.7% | 1.76 | 12 | 50.0% |
| grid_trend_bias_eth | ETHUSDT | 4h | 9% | $13.5 | $35.6 | 164.0% | 1.615 | -18.8% | 1.82 | 69 | 79.7% |
| breakout_squeeze_btc | BTCUSDT | 4h | 3% | $4.5 | $5.9 | 30.9% | 0.732 | -16.8% | 0.50 | 18 | 50.0% |

### 4.2 主要獲利來源（以 PnL 排序）

| 策略位 | PnL | 備註 |
|--------|-----|------|
| momentum_ranking_eth | **+$98.0** | 本組合最大單一獲利來源 |
| momentum_ranking_bnb | **+$75.9** | 報酬效率最高之一 |
| trend_donchian_adx_slope_eth | **+$33.8** | 套用 1x 候選後，風險品質明顯改善 |
| momentum_ranking_sol | **+$29.5** | 小權重高彈性 |
| tail_risk_hedge_sol | **+$28.3** | 防禦策略同時提供高報酬 |

### 4.3 個別策略風險觀察

- **最大單體風險來源仍是 BTC ADX**：`trend_donchian_adx_slope_btc` MaxDD **-72.5%**
- **低交易數**：`trend_donchian_mtf_btc` 7 筆、`trend_donchian_mtf_xrp` 5 筆、`momentum_ranking_bnb` 8 筆
- **最弱單體**：`breakout_squeeze_btc` Sharpe **0.732**，可保留但不應被視為主要 alpha 引擎

---

## 5. 組合層級表現

### 5.1 核心績效

| 指標 | 數值 |
|------|------|
| 初始資金 | $150 |
| 最終價值 | **$530** |
| 累計報酬 | **+253.7%** |
| 年化報酬 | **+46.6%** |
| Sharpe | **1.984** |
| Sortino | **2.960** |
| Max Drawdown | **-15.5%** |
| Calmar | **3.01** |
| 期間 | 2023-01-01 → 2026-04-20 |

### 5.2 年度表現

| 年度 | 年內報酬 | 年底資產 |
|------|----------|----------|
| 2023 | **+47.60%** | $221.40 |
| 2024 | **+48.35%** | $333.62 |
| 2025 | **+43.17%** | $477.80 |
| 2026 YTD | **+11.19%** | $530.48 |

### 5.3 月度極值

| 項目 | 數值 |
|------|------|
| 最佳月份 | **2024-11：+28.10%** |
| 最差月份 | **2023-05：-5.80%** |

### 5.4 最大回撤區間

| 項目 | 日期 / 數值 |
|------|-------------|
| 前高點 | **2024-03-13** |
| 最深回撤點 | **2024-06-06** |
| 最大回撤 | **-15.51%** |
| 回到前高 | **2024-11-08** |

**解讀**：ETH ADX 的 1x 化後，組合最大回撤從原本 V7.4 重新回測的 ~17.6% 區間進一步壓到 ~15.5%，恢復期仍長，但已更接近可實際承受的部署輪廓。

---

## 6. 穩健性與診斷

### 6.1 市場 regime 分布（BTC 4h）

| Regime | 佔比 | Bars | 最長連續區段 |
|--------|------|------|--------------|
| trending_up | 18.7% | 1,351 | 97 |
| trending_down | 16.3% | 1,179 | 73 |
| ranging | 42.9% | 3,098 | 120 |
| volatile | 22.1% | 1,598 | 82 |

### 6.2 相關性

| 指標 | 數值 |
|------|------|
| 平均相關性 | **0.0643** |
| 低相關對數（< 0.3） | **112** |

代表性低相關配對：

- `trend_donchian_adx_slope_eth` ↔ `grid_trend_bias_xrp`: 0.000
- `trend_donchian_mtf_bnb` ↔ `tail_risk_hedge_btc`: -0.003
- `trend_donchian_mtf_xrp` ↔ `momentum_ranking_sol`: -0.003

### 6.3 健康度與近期衰退

**明確被標記為衰退**

| 策略位 | Sharpe 30d | Sharpe 90d | Current DD |
|--------|------------|------------|------------|
| tail_risk_hedge_btc | n/a | -1.30 | -12.8% |

**近期 30d Sharpe 偏弱**

| 策略位 | Sharpe 30d | Sharpe 90d |
|--------|------------|------------|
| trend_donchian_adx_slope_eth | -2.55 | 0.42 |
| trend_donchian_adx_slope_btc | -1.15 | 1.00 |
| momentum_ranking_bnb | -0.38 | 2.22 |
| momentum_ranking_sol | -4.39 | 0.72 |
| tail_risk_hedge_sol | -0.10 | 1.43 |

**低交易數策略需降低信心**

- `trend_donchian_mtf_btc`: 7 trades
- `trend_donchian_mtf_xrp`: 5 trades
- `momentum_ranking_bnb`: 8 trades

### 6.4 Walk-Forward（組合）

| Window | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD |
|--------|-----------|------------|------------|-----------|
| W0 | 2.534 | 1.807 | 13.8% | -9.9% |
| W1 | 1.959 | -1.157 | -4.5% | -10.1% |
| W2 | 1.773 | 1.241 | 5.6% | -6.0% |
| W3 | 1.892 | 3.728 | 27.6% | -5.2% |
| W4 | 1.764 | 0.955 | 4.4% | -6.2% |
| W5 | 1.566 | 1.236 | 5.3% | -4.8% |
| W6 | 1.928 | 4.081 | 22.1% | -6.5% |
| W7 | 2.655 | 1.588 | 7.7% | -6.3% |
| W8 | 1.944 | 2.118 | 11.9% | -4.8% |

Walk-Forward summary:

| 指標 | 數值 |
|------|------|
| 視窗數 | 9 |
| 平均 OOS Sharpe | **1.733** |
| 最低 OOS Sharpe | **-1.157** |
| OOS / IS 效率比 | **0.866** |
| OOS 為正比例 | **89%** |
| 判定 | **PASSED** |

### 6.5 Nested Walk-Forward（每視窗重配權重）

| 權重法 | Avg OOS Sharpe | Min OOS Sharpe | Positive OOS | 視窗數 |
|--------|----------------|----------------|--------------|--------|
| equal | **2.543** | -0.073 | 88.9% | 9 |
| sharpe_pos | **2.148** | -1.199 | 88.9% | 9 |

### 6.6 Monte Carlo

| 模型 | Final | Sharpe | MaxDD |
|------|-------|--------|-------|
| 原始路徑 | 3.5365x | 1.984 | -15.5% |
| i.i.d. 模擬平均 | 3.7473x | 1.964 | -16.8% |
| i.i.d. P5 | 1.8474x | 1.041 | -10.1%* |
| i.i.d. P95 | 6.5894x | 2.898 | n/a |
| block bootstrap（5d）P5 / P95 Sharpe | n/a | 1.223 / 2.780 | n/a |
| block bootstrap（5d）Final P5 / P95 | 2.068x / 6.322x | n/a | n/a |
| block bootstrap（5d）MaxDD P5 | n/a | n/a | **-22.94%** |

\* i.i.d. 欄位中 console 顯示的是 `MaxDD(P95)`，不是 worst-tail 下界；更值得參考的是 block bootstrap 的 `MaxDD P5 = -22.94%`。

### 6.7 手續費敏感度

| 指標 | 數值 |
|------|------|
| 總交易次數 | **620** |
| Breakeven Fee | **26.5 bps** |
| Safety Margin | **4.41x** |
| Assessment | **SAFE** |

---

## 7. 與 V7.3 歷史基準比較

| 指標 | V7.3 歷史報告 | V7.4 目前版 | 變化 |
|------|---------------|-------------|------|
| 最終價值 | $505 | **$530** | +$25 |
| 累計報酬 | +236.7% | **+253.7%** | 改善 |
| 年化報酬 | 44.3% | **46.6%** | 改善 |
| Sharpe | **2.437** | 1.984 | 惡化 |
| Sortino | **3.625** | 2.960 | 惡化 |
| MaxDD | **-9.1%** | -15.5% | 惡化 |
| Calmar | **4.87** | 3.01 | 惡化 |
| 平均相關性 | **0.041** | 0.0643 | 略惡化 |
| 手續費安全倍數 | 3.79x | **4.41x** | 改善 |
| 策略位 | 17 | **16** | -1 |

### 7.1 正確解讀這個差異

V7.4 並不是 V7.3 的單純上位版。它同時包含：

- 與 live wiring 對齊的實際 baseline
- 槓桿語義校正
- 不同的策略配置與權重結構

因此 V7.3 仍然是歷史上更漂亮的風險調整後報酬樣貌；但若要討論 **現在 repo 裡可直接接到 live / paper 的正式控制組**，應以本文件的 V7.4 為準。

---

## 8. 本輪弱勢策略優化與後續方向

### 8.1 這次真的有效的修正

| 項目 | 動作 | 結果 |
|------|------|------|
| breakout 接線 | 修正 `V74_CONFIG` 使用錯的 `kc_ema_period` / `kc_atr_period` key，改成策略實作實際吃的 `kc_ema` / `kc_atr` | breakout 單體從舊配置的 27.9% / 0.793 / -16.7% 變成 **30.9% / 0.732 / -16.8%**；整體組合影響有限 |
| ETH ADX | 補跑 ETH 專掃後，把 `trend_donchian_adx_slope_eth` 換成 `entry=15, exit=5, adx_threshold=20, adx_slope_bars=3, leverage=1` | ETH ADX 單體從 **0.934 / -66.7%** 改善到 **1.142 / -30.5%**；組合層提升到 **Sharpe 1.984 / MaxDD -15.5%** |

### 8.2 目前最大的剩餘問題

1. **BTC ADX 仍然過弱**  
   已掃描過 BTC 版本，最佳也只有 **Sharpe 0.581 / MaxDD -34.49%**，遠不足以支撐目前的 2x + 8% 配置。

2. **breakout 不是當前最值得深挖的 alpha**  
   接線修好後可保留，但它不是組合改善的主要來源。

3. **低交易數策略仍在**  
   `trend_donchian_mtf_btc`、`trend_donchian_mtf_xrp`、`momentum_ranking_bnb` 仍有統計信心不足問題。

### 8.3 優先優化順序

| 優先級 | 對象 | 建議 |
|--------|------|------|
| P1 | `trend_donchian_adx_slope_btc` | 先做 **1x / 降權重 / allocation-only** 路線，不建議再維持 2x 高曝險 |
| P2 | `tail_risk_hedge_btc` | 因唯一明確 decay flag，建議改成 **volatile-only / drawdown-only** 啟用 |
| P2 | Fragile tier | `grid_trend_bias_eth` + `breakout_squeeze_btc` 合計 12%，可考慮先壓到 9% |
| P3 | `momentum_ranking_bnb` | 表現強但交易數少，先延長樣本 / 做 cross-asset 驗證，再決定是否加碼 |

### 8.4 對應 repo 內現成工具

- `python -m backtest_tool.scripts.optimize_phase12 --phase 1 --task 1d`
- `python -m backtest_tool.scripts.optimize_phase12 --phase 2 --task 2b`
- `python -m backtest_tool.scripts.v74_leverage_optimization`

### 8.5 最終結論

V7.4 在 **live-aligned、槓桿語義已修正、ETH ADX 已切換到更健康的 1x 候選** 後，仍然達成：

- 年化報酬 > 25%
- MaxDD < 20%
- Walk-Forward 通過
- 手續費安全

因此目前結論是：

> **V7.4 可以作為 Cry2 的正式 baseline 回測報告與部署控制組。**  
> 現階段最值得繼續優化的不是 breakout，也不是 ETH ADX，而是 **BTC ADX 的風險處理與整體 allocation 微調**。
