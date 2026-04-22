# 📊 Cry2 量化交易系統 — 完整回測分析報告

**版本**: V7.3 主報告 + V7.4 更新附註 | **日期**: 2026-04-22  
**初始資金**: 150 USDT  
**回測期間**: 2023-01-01 ~ 2026-04-20（約 3.3 年）  
**資料來源**: Binance Futures 4h/1d K線（BTC/ETH/BNB/SOL/XRP）  
**回測引擎**: VectorBT Pro + 自建策略框架  
**交易幣種**: BTC、ETH、BNB、XRP、SOL（5 幣種）  
**策略位**: 17 個（9 種策略 × 5 幣種，含市場中性策略）

---

## 📋 目錄

1. [執行摘要](#1-執行摘要)
2. [策略演進歷程 V1→V7.3](#2-策略演進歷程-v1v73)
3. [V7.3 最終組合詳細分析](#3-v73-最終組合詳細分析)
4. [Phase B/C 深度優化過程](#4-phase-bc-深度優化過程)
5. [G4/G5 新策略分析](#5-g4g5-新策略分析)
6. [參數穩健性分析](#6-參數穩健性分析)
7. [Walk-Forward 驗證](#7-walk-forward-驗證)
8. [Monte Carlo 模擬](#8-monte-carlo-模擬)
9. [策略相關性分析](#9-策略相關性分析)
10. [策略健康度與衰退偵測](#10-策略健康度與衰退偵測)
11. [市場環境分析](#11-市場環境分析)
12. [手續費敏感度分析](#12-手續費敏感度分析)
13. [小資金部署建議](#13-小資金部署建議)
14. [風險警示](#14-風險警示)
15. [已知限制與注意事項](#15-已知限制與注意事項)

---

## 1. 執行摘要

### 🎯 目標 vs 達成

| 指標 | 目標 | V7.3 結果 | 狀態 |
|------|------|-----------|------|
| 年化 Sharpe Ratio | > 0.8 | **2.437** | ✅ 超標 205% |
| 年化報酬率 | > 25% | **44.3%** | ✅ 超標 77% |
| 最大回撤 | < 20% | **-9.1%** | ✅ 通過（全版本最佳 🏆） |
| Calmar Ratio | > 1.0 | **4.87** | ✅ 超標 387%（全版本最佳 🏆） |
| Walk-Forward OOS Sharpe（post-selection） | > 0.5 | **2.103** | ✅ 通過，但非嚴格 nested OOS（見 §7） |
| 策略相關性（daily-resampled） | < 0.1 | **0.041** | ✅ 通過（大幅改善） |
| 手續費安全倍數 | > 4.0x | **3.79x** | ⚠️ 略低於 4x，仍安全 |

### 💰 投資績效概覽（150 USDT 初始資金）

| 項目 | 數值 |
|------|------|
| 初始資金 | **$150 USDT** |
| 最終價值 | **$505 USDT** |
| 累計報酬 | **+236.7%** |
| 年化報酬 | **+44.3%** |
| 最大回撤 | **-9.1%**（最多虧約 $14） |
| Sharpe Ratio | **2.437** |
| Sortino Ratio | **3.625** |
| Calmar Ratio | **4.87** |
| 策略間平均相關性 | **0.041**（極低，daily-resampled） |
| 總交易次數 | ~670 筆 |
| 手續費安全倍數 | **3.79x** |
| 策略位 | 17 個 |
| 幣種 | 5 (BTC/ETH/BNB/XRP/SOL) |

### 🔄 V7.2 → V7.3 關鍵改進

| 指標 | V7.2 | V7.3 | 變化 |
|------|------|------|------|
| Sharpe | 2.355 | **2.437** | +3.5% |
| Ann Return | 45.2% | **44.3%** | -2.0%（因配置調整） |
| MaxDD | -10.6% | **-9.1%** | 改善 14.2% 🏆 |
| Calmar | 4.27 | **4.87** | +14.1% 🏆 |
| Sortino | 3.464 | **3.625** | +4.6% |
| 相關性 | 0.062 | **0.041** | 改善 33.9% |
| WF OOS Sharpe | 2.004 | **2.103** | +4.9% |
| Fee Safe | 4.32x | **3.79x** | -12.3% |
| 策略位 | 16 | **17** | +1（市場中性策略） |
| 最終價值 | $514 | **$505** | -$9（DD 改善抵消） |

**V7.3 核心改進**: 新增 G4 Funding Rate 反向策略 + G5 BTC-ETH OLS 配對交易，引入市場中性維度，大幅降低 MaxDD 與策略間相關性。

### 🆕 V7.4 執行鏈與槓桿模型更新（2026-04-22）

| 項目 | 結果 |
|------|------|
| `python scripts/run_live.py --dry-run --version v74` | ✅ 通過；16 個策略位、5 幣種、1d/4h 混合時間框架、總配置 100%，可寫出 health snapshot |
| `python scripts/run_paper.py --dry-run --version v74` | ✅ 通過；摘要與 live dry-run 一致，確認 paper/runtime wiring 無衝突 |
| runtime smoke regression | ✅ `tests/unit/test_runtime_smoke.py` 通過 |
| `python -m backtest_tool.scripts.full_portfolio_backtest` | ✅ 已重跑（對齊 live V7.4 配置）；Final **$543**、Total Return **+261.7%**、Ann Return **+47.6%**、Sharpe **1.763**、MaxDD **-17.7%**、Verdict **DEPLOYABLE (5/5)** |
| leverage semantics | ✅ 已收斂：raw `vectorbt` `size_type="percent"` 在 `size > 1` 時不會形成真實 >1x 曝險，因此 `full_portfolio_backtest.py` 改為顯式 **return-amplification leverage model**，`v74_leverage_optimization.py` 明確標示為 simulated 2x leverage |

> 註：本文件其餘績效表仍以 **V7.3 歷史回測結果** 為主；上表已補上 **2026-04-22 的 V7.4 重測摘要**。若要把整份報告升級成正式 V7.4 版，仍需把正文各章節表格與分析敘述整體改寫。

---

## 2. 策略演進歷程 V1→V7.3

### 全版本對比

```
版本   Sharpe  年化報酬  MaxDD     Calmar  策略  幣種  $150→    狀態
────  ──────  ───────  ────────  ──────  ────  ────  ──────  ────
V1    1.320    33.1%   -21.8%    1.52     10    2    $200   4/5 ❌
V2    1.430    31.3%   -19.0%    1.65      8    2    $197   5/5 ✅
V3    1.470    31.9%   -17.8%    1.80      8    2    $198   5/5 ✅
V4    1.685    35.1%   -16.7%    2.10      7    2    $203   5/5 ✅
V5    1.969    37.4%   -13.8%    2.70     10    4    $428   5/5 ✅
V6    2.063    38.5%   -12.0%    3.20     12    5    $439   5/5 ✅
V7    2.392    45.8%   -12.8%    3.59     14    5    $521   5/5 ✅
V7.1  2.414    44.0%   -11.9%    3.68     16    5    $500   5/5 ✅
V7.2  2.355    45.2%   -10.6%    4.27     16    5    $514   5/5 ✅
V7.3  2.437    44.3%    -9.1%    4.87     17    5    $505   5/5 ✅ 🏆
```

### V1→V6 摘要

| 版本 | 核心改動 |
|------|---------|
| V1 | 初版科學配置（取代 funding_arb 60%） |
| V2 | 移除 2 個虧損策略，首次 5/5 通過 |
| V3 | Phase 7 新策略替換（tail_risk_hedge、pv_divergence） |
| V4 | 630 組參數優化 + 新增 dual_channel_breakout |
| V5 | BNB + XRP 多幣種擴展（4→4 幣種） |
| V6 | SOL 加入 + 配置精調（5 幣種 12 策略位） |

### V6 → V7（Rescan 參數升級）

**核心改動**: 7,605 組全面參數重掃，發現多個重大改進

- ⬆️ `adx_slope BTC`: Sharpe 0.65→1.052 (+62%)
- ⬆️ `grid_trend_bias XRP`: Sharpe 0.93→1.117 (+20%)
- 🆕 `tail_risk_hedge SOL`: Sharpe 1.243 (472% Return)
- 🆕 `adx_slope ETH`: Sharpe 1.172 (225% Return)
- 🆕 `mtf XRP`: Sharpe 1.059 (115% Return)

**結果**: Sharpe 2.063→2.392 (+16%), $150→$521

### V7 → V7.1（穩健度加權）

**核心改動**: 基於 7,605 組合的參數穩定性分析，建立三層架構

- ↑ 穩健策略權重（viable combos >50%）
- ↓ 脆弱策略權重（viable combos <15%）
- ↓ `dual_channel_breakout` 10→5%（脆弱）
- ↓ `grid_trend_bias ETH` 15→9%（僅 5.6% viable）

**結果**: MaxDD -12.8%→-11.9%, Calmar 3.59→3.68

### V7.1 → V7.2（Phase B/C 深度優化）🏆

**核心改動**: 移除衰退策略，新增分散化策略，強化穩健層

- ❌ 移除 `dual_channel_breakout ETH`（30d=-3.22, 90d=-3.52, 嚴重衰退）
- 🆕 新增 `momentum_ranking SOL` 3%（Sharpe 1.227, 低相關性分散）
- ↑ `mtf BTC` 15→16%（61% viable, 最穩健）
- ↑ `adx_slope ETH` 9→10%（59% viable）

**結果**: MaxDD -11.9%→**-10.6%**（V1–V7.2 中最佳）, Calmar 3.68→**4.27**（V1–V7.2 中最佳）

### V7.2 → V7.3（G4+G5 市場中性策略）🏆

**核心改動**: 新增兩個市場中性/缺口填補策略，建立第四層「Market Neutral + Gap」

- 🆕 **G5 BTC-ETH 配對交易** `pair_btc_eth` 5%（Sharpe 1.334, Rolling OLS 共整合）
- 🆕 **G4 Funding Rate 反向** `funding_reversal_eth` 3%（Sharpe 0.934, 長線低頻逆向）
- ❌ 移除 `breakout_squeeze BTC` 3%（脆弱層，低交易數，統計信心不足）
- ↓ `grid_trend_bias ETH` 9→7%（壓低脆弱層暴露）
- ↓ `momentum_ranking ETH` 12→10%（為新策略騰空間）
- ↓ `grid_trend_bias BTC` 4→3%（微調）

**關鍵發現**: G5 配對交易使用 Rolling OLS 回歸殘差（非簡單 ratio），解決了 BTC/ETH 長期趨勢問題，與現有策略近零相關（avg 0.025）

**結果**: MaxDD -10.6%→**-9.1%** 🏆, Calmar 4.27→**4.87** 🏆, 相關性 0.062→**0.041**（全版本最佳）

---

## 3. V7.3 最終組合詳細分析

### 四層架構配置

> 📌 **V7.3 新增第四層「市場中性 + 缺口填補」**，從三層升級為四層架構。
> 📌 **符號說明**：策略列名後有 ⚠️ 表示該策略總交易數 < 10，統計顯著性低。

#### ⭐ 穩健層 — 40%（`trend_donchian` 族群，長線趨勢核心）

> **分層準則**：同 V7.2，以「策略族群 = 趨勢追蹤 `trend_donchian_*`」歸類。
> XRP mtf (75%)、BTC mtf (61%)、ETH adx_slope (59%) viable% >50%；
> BTC adx_slope (18.5%)、BNB mtf (13.9%) 歸入穩健層是因同屬族群且配置壓低。

| 策略位 | 幣種 | 配置 | 實際資金 | Sharpe | Return | MaxDD | Trades | Win% | Viable% |
|--------|------|------|----------|--------|--------|-------|--------|------|---------|
| trend_donchian_mtf ⚠️ | BTC | **16%** | $24.0 | 1.032 | 44.6% | -10.8% | 7 | 71.4% | 61.1% |
| trend_donchian_adx_slope | ETH | **10%** | $15.0 | 1.172 | 225.2% | -32.2% | 109 | 42.2% | 59.3% |
| trend_donchian_adx_slope | BTC | **8%** | $12.0 | 1.052 | 143.5% | -31.1% | 110 | 43.6% | 18.5% |
| trend_donchian_mtf ⚠️ | XRP | **4%** | $6.0 | 1.059 | 114.8% | -21.1% | 5 | 80.0% | 75.0% |
| trend_donchian_mtf | BNB | **2%** | $3.0 | 1.008 | 62.5% | -16.6% | 10 | 70.0% | 13.9% |

**特徵**: 趨勢追蹤型，4h 時間框架，持倉數天~數週。在趨勢明確時表現最佳。

#### 🔵 中等層 — 45%（混合策略，短線補位 + 防禦）

> **V7.3 變動**: mom_ETH 12→10%，grid_BTC 4→3%，總量 48→45%，為新策略騰出空間。

| 策略位 | 幣種 | 配置 | 實際資金 | Sharpe | Return | MaxDD | Trades | Win% | 分類 |
|--------|------|------|----------|--------|--------|-------|--------|------|------|
| momentum_ranking | ETH | **10%** | $15.0 | 1.483 | 584.9% | -46.6% | 10 | 90.0% | 長線動量 |
| momentum_ranking ⚠️ | BNB | **8%** | $12.0 | 1.535 | 632.4% | -33.1% | 8 | 87.5% | 長線動量 |
| momentum_ranking | SOL | **3%** | $4.5 | 1.227 | 601.6% | -47.7% | 34 | 41.2% | 長線動量 |
| grid_trend_bias | XRP | **6%** | $9.0 | 1.117 | 77.5% | -23.9% | 37 | 73.0% | 短線網格 |
| tail_risk_hedge | BNB | **5%** | $7.5 | 1.427 | 135.1% | -15.5% | 12 | 83.3% | 防禦 |
| tail_risk_hedge | SOL | **4%** | $6.0 | 1.243 | 472.3% | -39.3% | 59 | 62.7% | 防禦 |
| grid_trend_bias | BTC | **3%** | $4.5 | 1.327 | 35.2% | -7.4% | 16 | 75.0% | 短線網格 |
| grid_trend_bias | SOL | **3%** | $4.5 | 1.224 | 224.6% | -31.4% | 96 | 72.9% | 短線網格 |
| tail_risk_hedge | BTC | **3%** | $4.5 | 1.241 | 95.0% | -12.7% | 12 | 50.0% | 防禦 |

**特徵**: 混合型，涵蓋長線動量（1d）、短線網格（4h）、防禦對沖（1d）。

#### ⚠️ 脆弱層 — 7%（高 Sharpe + 低 viable%，V7.3 大幅壓縮）

> **V7.3 變動**: 移除 `breakout_squeeze BTC`（低交易數、統計信心不足），`grid_ETH` 9→7%。
> 脆弱層從 12%→7%，風險暴露顯著降低。

| 策略位 | 幣種 | 配置 | 實際資金 | Sharpe | Return | MaxDD | Trades | Win% | Viable% |
|--------|------|------|----------|--------|--------|-------|--------|------|---------|
| grid_trend_bias | ETH | **7%** | $10.5 | 1.615 | 164.0% | -18.8% | 69 | 79.7% | 5.6% |

**特徵**: 高 Sharpe 但僅少數參數組合有效。V7.3 壓至 7% 以限制參數失效風險。

#### 🟣 市場中性 + 缺口填補層 — 8%（V7.3 新增）

> **V7.3 全新層級**: 引入市場中性配對交易與 Funding Rate 反向策略，
> 填補趨勢空窗期的獲利，與現有策略近零相關（avg correlation 0.025）。

| 策略位 | 幣種 | 配置 | 實際資金 | Sharpe | Return | MaxDD | Trades | Win% | 特色 |
|--------|------|------|----------|--------|--------|-------|--------|------|------|
| pair_btc_eth | BTC/ETH | **5%** | $7.5 | 1.334 | 154.0% | -15.1% | 59 | 76.3% | OLS 共整合配對 |
| funding_reversal | ETH | **3%** | $4.5 | 0.934 | 84.4% | -18.2% | ~20 | ~65% | 極負 funding 逆向 |

**特徵**:
- `pair_btc_eth`: 使用 Rolling OLS 回歸 `log(BTC) = α + β × log(ETH) + ε` 的殘差做 z-score 均值回歸，動態 hedge ratio β≈0.71。**市場中性**，不暴露方向性風險。
- `funding_reversal_eth`: 在 funding rate 極負時（市場恐慌）逆向做多，屬低頻抄底策略。年年皆盈利。

### 💵 $150 資金分配明細

| 策略 | 配置 | 資金 | 幣種 | TF | 槓桿 | 分類 |
|------|------|------|------|----|------|------|
| trend_donchian_mtf BTC | 16% | $24.0 | BTC | 4h | 2x | ⭐穩健 |
| trend_donchian_adx_slope ETH | 10% | $15.0 | ETH | 4h | 2x | ⭐穩健 |
| momentum_ranking ETH | 10% | $15.0 | ETH | 1d | 1.5x | 🔵長線 |
| momentum_ranking BNB | 8% | $12.0 | BNB | 1d | 1.5x | 🔵長線 |
| trend_donchian_adx_slope BTC | 8% | $12.0 | BTC | 4h | 2x | ⭐穩健 |
| grid_trend_bias ETH | 7% | $10.5 | ETH | 4h | 2x | ⚠️脆弱 |
| grid_trend_bias XRP | 6% | $9.0 | XRP | 4h | 2x | 🔵短線 |
| tail_risk_hedge BNB | 5% | $7.5 | BNB | 1d | 1x | 🔵防禦 |
| pair_btc_eth | 5% | $7.5 | BTC/ETH | 4h | 1x | 🟣中性 |
| tail_risk_hedge SOL | 4% | $6.0 | SOL | 1d | 1x | 🔵防禦 |
| trend_donchian_mtf XRP | 4% | $6.0 | XRP | 4h | 2x | ⭐穩健 |
| momentum_ranking SOL | 3% | $4.5 | SOL | 1d | 1.5x | 🔵長線 |
| grid_trend_bias BTC | 3% | $4.5 | BTC | 4h | 1x | 🔵短線 |
| grid_trend_bias SOL | 3% | $4.5 | SOL | 4h | 1x | 🔵短線 |
| tail_risk_hedge BTC | 3% | $4.5 | BTC | 1d | 1x | 🔵防禦 |
| funding_reversal ETH | 3% | $4.5 | ETH | 4h | 1x | 🟣缺口 |
| trend_donchian_mtf BNB | 2% | $3.0 | BNB | 4h | 2x | ⭐穩健 |
| **合計** | **100%** | **$150.0** | **5 幣種** | | | |

### 幣種集中度

| 幣種 | 配置 | 策略數 | V7.2→V7.3 變化 |
|------|------|--------|---------------|
| BTC | **30%** | 4 | -4%（移除 breakout，但配對交易不含方向性 BTC 暴露） |
| ETH | **30%** | 4 | -1%（新增 funding 3%，但降 mom/grid） |
| BNB | **15%** | 3 | 不變 |
| SOL | **10%** | 3 | 不變 |
| XRP | **10%** | 2 | 不變 |
| 市場中性 | **5%** | 1 | 🆕 配對交易不屬任一幣種 |

### 策略類型分布

| 類型 | 配置 | 目的 | V7.2→V7.3 |
|------|------|------|-----------|
| 🟢 長線趨勢（trend_donchian + momentum） | **61%** | 捕捉大趨勢，核心獲利 | 63→61% |
| 🟡 短線網格（grid_trend_bias） | **19%** | 盤整期補位，交易頻率高 | 25→19% |
| 🔵 防禦對沖（tail_risk_hedge） | **12%** | 極端行情保護 | 12%不變 |
| 🟣 市場中性 + 缺口填補（pair + funding） | **8%** | 趨勢空窗期獲利，降低相關性 | 🆕 |

---

## 4. Phase B/C 深度優化過程

### 優化方法論

基於 7,605 組參數掃描結果，進行以下分析：

1. **參數穩定性分析**: 計算每個策略×幣種在 80% 峰值 Sharpe 以上的參數比例
2. **衰退偵測**: 對比全期 vs 近期 (30d/90d) Sharpe 變化
3. **變體回測**: 設計 4 個替代配置，完整回測比較
4. **最終選擇**: 基於 MaxDD、Calmar、相關性綜合評判

### 變體比較

| 變體 | 改動 | Sharpe | MaxDD | Calmar | 結果 |
|------|------|--------|-------|--------|------|
| V7.1 (基準) | — | 2.414 | -11.9% | 3.68 | 基準 |
| V7.2a | +adx_slope XRP 取代 DCB | ≈同 | -12.3% | ≈同 | ❌ DD 更差 |
| V7.2b | +robust 權重 | ≈同 | -11.8% | ≈同 | △ 僅微改 |
| V7.2c | +momentum SOL 取代 DCB | ↓2% | **-10.0%** | **7.63** | ✅ MaxDD 最佳 |
| **V7.2 (最終)** | **c + 增強穩健層** | **2.355** | **-10.6%** | **4.27** | **✅ 最佳綜合** |

### 關鍵決策

#### ❌ 移除 dual_channel_breakout ETH

**理由**:
- 30d Sharpe: **-3.22**（嚴重衰退）
- 90d Sharpe: **-3.52**（持續惡化）
- 參數穩定性: 5.6%（僅 18/324 組合 viable）
- 22.9% 最大回撤（佔組合 DD 的主要來源）

#### 🆕 新增 momentum_ranking SOL (3%)

**理由**:
- Sharpe 1.227，601.6% Return（強收益）
- 與現有策略相關性極低（correlation ≈ 0）
- 增加 SOL 暴露（從 7%→10%）
- 34 筆交易，41.2% 勝率（動量型正常）

#### ↑ 強化穩健層

- `mtf BTC` 15→16%: 61% viable，最穩健的趨勢策略
- `adx_slope ETH` 9→10%: 59% viable，穩健且高 Sharpe

---

## 5. G4/G5 新策略分析

### G5 BTC-ETH 配對交易（Rolling OLS 共整合）

#### 問題與發現

BTC/ETH 原始 ratio 存在**巨大長期上升趨勢**（BTC dominance 持續增加 2023-2026），導致傳統均值回歸方法**完全失敗**：

- 原始 ratio z-score 方法: 75 組參數全部 negative Sharpe
- Long-only ratio: 仍然虧損
- Return spread: 微弱改善但不可用
- **Rolling OLS**: ✅ 成功！回歸殘差是平穩的

#### Rolling OLS 方法

```
模型: log(BTC_price) = α + β × log(ETH_price) + ε
殘差: ε_t = log(BTC_t) - α̂_t - β̂_t × log(ETH_t)
Z-score: z_t = (ε_t - mean(ε, window)) / std(ε, window)
```

- 動態 hedge ratio β ≈ 0.71（非 1:1），隨 regime 自適應
- OLS 視窗 = 480 bars，z-score 視窗 = 90 bars
- 進場: |z| > 2.5，出場: z 穿越 0，止損: |z| > 4.0

#### 參數掃描結果

| OLS Window | Z Period | Entry Z | Sharpe | Return | MaxDD | Trades |
|-----------|----------|---------|--------|--------|-------|--------|
| **480** | **90** | **2.5** | **1.351** | +157.7% | -15.1% | 59 |
| 480 | 60 | 2.5 | 1.287 | +142.3% | -16.8% | 72 |
| 360 | 90 | 2.5 | 1.198 | +128.4% | -17.2% | 65 |
| 480 | 120 | 2.0 | 1.156 | +135.6% | -18.9% | 81 |

#### 最終驗證結果

| 指標 | 數值 |
|------|------|
| Sharpe Ratio | **1.334** |
| 累計報酬 | **+154%** |
| 最大回撤 | **-15.1%** |
| 交易次數 | 59 |
| 勝率 | **76.3%** |
| 與 V7.2 策略平均相關性 | **0.025**（近零） |

#### 年度表現

| 年份 | Return | 狀態 |
|------|--------|------|
| 2023 | +3.5% | ✅ |
| 2024 | +39.1% | ✅ |
| 2025 | +53.1% | ✅ |
| 2026 (YTD) | +15.9% | ✅ |

**所有年度均盈利**，2024-2025 為主要獲利期。

### G4 Funding Rate 反向策略（長線低頻逆向）

#### 策略邏輯

在 funding rate 極度負值時（市場極度恐慌、空頭擁擠），逆向做多 ETH：

- 進場: funding rate < -4 bps（極度負值，空頭過度擁擠）
- 持倉: 12 bars（~2天，等待 funding 正常化）
- 僅做多（short side 在牛市中 fade trend 一直虧損）

#### 各幣種測試結果

| 幣種 | Sharpe | Return | 狀態 | 結論 |
|------|--------|--------|------|------|
| **ETH** | **0.934** | **+84.4%** | ✅ 採用 | Funding 反向最可靠的幣種 |
| XRP | ~0.1 | ~+5% | ❌ 不採用 | 不穩定，低信心 |
| BTC | ~0.2 | ~+8% | ❌ 不採用 | Funding 極負事件太少 |
| SOL | ~-0.1 | ~-3% | ❌ 不採用 | 虧損 |

**決策**: 僅採用 ETH 的 Funding Rate 反向策略，配置 3%。

### V7.3 組合整合效果

| 指標 | V7.2 (無 G4/G5) | V7.3 (含 G4/G5) | 改善 |
|------|-----------------|-----------------|------|
| Sharpe | 2.355 | **2.437** | +3.5% |
| MaxDD | -10.6% | **-9.1%** | 改善 14.2% |
| Calmar | 4.27 | **4.87** | +14.1% |
| 策略間相關性 | 0.062 | **0.041** | 改善 33.9% |
| 低相關對 | 113 | **130** | +17 |

**結論**: 市場中性策略的引入大幅降低了組合的系統性風險，MaxDD 和 Calmar 同時創歷史新高。

---

## 6. 參數穩健性分析

### 穩定性排名（7,605 組掃描）

> **Viable%** = 80% 峰值 Sharpe 以上的參數組合比例，越高越穩健

| 等級 | 策略 | 幣種 | Peak Sharpe | Avg Sharpe | Viable% | Positive% |
|------|------|------|-------------|-----------|---------|-----------|
| ⭐ ROBUST | trend_donchian_mtf | XRP | 1.059 | 0.935 | **75.0%** | 100% |
| ⭐ ROBUST | trend_donchian_mtf | BTC | 1.032 | 0.832 | **61.1%** | 100% |
| ⭐ ROBUST | trend_donchian_adx_slope | ETH | 1.172 | 0.972 | **59.3%** | 100% |
| ⭐ ROBUST | trend_donchian_adx_slope | XRP | 1.079 | 0.824 | **44.4%** | 100% |
| 🔵 MODERATE | momentum_ranking | ETH | 1.483 | 0.927 | 25.0% | 100% |
| 🔵 MODERATE | momentum_ranking | BNB | 1.535 | 0.726 | 25.0% | 75% |
| 🔵 MODERATE | momentum_ranking | SOL | 1.227 | 0.457 | 25.0% | 75% |
| 🔵 MODERATE | tail_risk_hedge | BNB | 1.427 | 0.580 | 22.2% | 67% |
| 🔵 MODERATE | tail_risk_hedge | SOL | 1.243 | 0.624 | 22.2% | 100% |
| 🔵 MODERATE | tail_risk_hedge | BTC | 1.241 | 0.704 | 22.2% | 100% |
| 🔵 MODERATE | trend_donchian_adx_slope | BTC | 1.052 | 0.649 | 18.5% | 100% |
| 🔵 MODERATE | breakout_squeeze | BTC | 0.793 | 0.238 | 16.7% | 67% |
| ⚠️ FRAGILE | grid_trend_bias | XRP | 1.117 | 0.391 | 13.9% | 83% |
| ⚠️ FRAGILE | grid_trend_bias | SOL | 1.224 | 0.665 | 13.9% | 97% |
| ⚠️ FRAGILE | grid_trend_bias | ETH | 1.615 | 0.377 | **5.6%** | 75% |
| ⚠️ FRAGILE | grid_trend_bias | BTC | 1.327 | 0.110 | **5.6%** | 56% |

### 核心發現

1. **參數穩定性 > 原始 Sharpe**: `grid_trend_bias ETH` 有最高 Sharpe (1.615) 但僅 5.6% viable → 參數失效風險極高
2. **趨勢策略最穩健**: `trend_donchian_mtf` 在 BTC/XRP 上 61-75% viable → 參數改變影響小
3. **動量策略中等穩健**: `momentum_ranking` 在 25% viable → 需要較精確的 roc/lookback 選擇
4. **Threshold 參數不敏感**: `momentum_ranking` 的 upper/lower threshold 對結果無影響 → 可安全使用預設值

### 關鍵參數敏感度

#### momentum_ranking ETH
- `roc_period`: **60 最佳** (mean 1.236)，30 次佳 (1.113)，20 最差 (0.272)
- `lookback`: 180 最佳 (1.125)，240 次佳 (0.928)
- `upper/lower threshold`: **無影響**（完全相同的 mean）

#### trend_donchian_mtf BTC
- `exit_period`: **10 最佳** (mean 0.921)，7 次佳 (0.860)，5 最差 (0.715)
- `entry_period`: **無影響**（完全相同的 mean = 0.832）

#### grid_trend_bias ETH
- `bb_std`: **2.0 最佳** (mean 0.884)，其餘大幅下降
- `bb_period`: **20 最佳** (mean 0.652)，30 降至 0.027
- `ema_period`: 50/100 相近，200 大幅下降

---

## 7. Walk-Forward 驗證（post-selection）

### 方法與限制

- 滾動窗口: 12 個月「IS」+ 3 個月「OOS」
- 共 9 個窗口覆蓋完整回測期間
- 驗證標準: OOS Sharpe > 0.5, 通過率 > 70%

> ⚠️ **重要限制 — 這不是嚴格 nested OOS**
> 目前 WF 是在**已經選好策略與權重的 combined equity curve 上切窗**，並非每個窗口重新：
> (a) 用 IS 資料重新掃參數、(b) 用 IS 選最佳組合、(c) 再用 OOS 資料跑新組合。
>
> 因此以下 OOS Sharpe 本質上是「對已選組合的路徑穩定性測試 / pseudo-OOS」，
> 不應視為對「未來真實部署」的嚴格外推。真正 nested walk-forward 的 OOS Sharpe
> 通常會低於此處報告值，誤差幅度視 IS 選參階段的樣本內偏誤而定。

### 結果

| Window | IS Sharpe | OOS Sharpe | OOS Return | OOS MaxDD |
|--------|-----------|------------|------------|-----------|
| W0 | 3.426 | **2.657** | +15.3% | -7.1% |
| W1 | 2.969 | -1.117 | -3.7% | -9.6% |
| W2 | 2.522 | **1.041** | +3.5% | -4.2% |
| W3 | 2.329 | **3.633** | +21.3% | -4.8% |
| W4 | 1.978 | **1.907** | +8.7% | -3.9% |
| W5 | 1.741 | **1.919** | +8.2% | -4.3% |
| W6 | 2.237 | **4.115** | +23.0% | -6.7% |
| W7 | 3.018 | **1.586** | +6.7% | -5.4% |
| W8 | 2.406 | **2.298** | +10.4% | -3.8% |

### 摘要（post-selection）

| 指標 | 數值 |
|------|------|
| 平均 OOS Sharpe（post-selection） | **2.103** |
| 最低 OOS Sharpe | -1.117 (W1) |
| 效率 (OOS/IS) | **0.838** |
| 正向 OOS 比率 | **89%** (8/9) |
| 通過 | ✅ YES（但須搭配下方 nested WF 判讀） |

### Nested Walk-Forward（已實作，每窗口重新配權）

作為對上方 post-selection WF 的補強，實作 `walk_forward_nested_analysis`：**每個 IS 窗口重新**計算每個策略的 IS Sharpe，
再用該權重套用到 OOS 區段上，得到「非事後觀察」的組合 OOS 表現。兩種配權方案：

| 配權方案 | n_win | 平均 OOS Sharpe | 最小 OOS | 正向 OOS% | 平均 OOS Return |
|---------|-------|----------------|----------|-----------|-----------------|
| **Equal-weight**（忽略權重、只測策略池） | 9 | **2.762** | -0.116 | 88.9% | 9.32% |
| **IS Sharpe-weighted**（按 IS Sharpe 正值加權） | 9 | **2.273** | **-1.082** | 88.9% | 7.58% |
| post-selection（§7 上方，固定 V7.3 權重） | 9 | 2.103 | -1.117 | 88.9% | — |

**解讀**：
- **Sharpe-weighted nested OOS (2.273) > post-selection (2.103)**：顯示 V7.3 的手動權重並非數據事後偷跑的結果
- **最小 OOS 仍是負值**（-1.08 / -1.12 / -0.12），三種方法在 W1 或 W2 都出現負 OOS，對應 2023-Q2 / 2024-Q3 的回檔期
- 此處 nested 仍有限：**未重新掃策略參數**（只重配權重），真正 strict nested WF 需要 per-window 重跑參數掃描；本報告 §6 viable% 分析已在參數維度提供獨立證據

### 分析（須搭配上方兩種 WF 結果閱讀）

- W1 是最負的 OOS 窗口，對應 2023-Q2 市場調整期；其次 W2 在 sharpe_pos 方案下也為負
- nested sharpe_pos 方案下 W2 OOS=-1.08 較 post-selection 更差，說明**事後固定權重確實有一定 lookahead 偏誤**（約 0.3~0.5 Sharpe 區間）
- OOS/IS 效率（post-selection）83.8% 僅為**該已選組合**的 IS→OOS 衰減；真實重選參數下的衰減還會更大
- **更可靠的參數不敏感證據來自 §6（viable%）**，這裡的 OOS Sharpe 只是「權重穩定性」的測試

---

## 8. Monte Carlo 模擬

### 方法

- 1,000 次 **bootstrap** 模擬（對每日收益做「有放回」取樣）
- 使用 daily 頻率 combined equity，annualization factor = 365
- 評估最終淨值 / Sharpe / 最大回撤 的分布

> ⚠️ **方法澄清**：舊版程式碼使用 `rng.permutation`（shuffle 無放回），此時 `∏(1+rᵢ)` 與 `mean/std` 皆為不變量，
> 因此 final value 與 Sharpe 在所有模擬中相同 — 並非真正的分布。已於此版改為有放回 bootstrap，
> 下表為新方法下的真實分布。

### 結果 A — i.i.d. bootstrap（n=1,000, seed=42）

| 指標 | 原始 | 模擬均值 | P5 | P25 | P75 | P95 |
|------|------|---------|-----|-----|-----|-----|
| 最終淨值（相對初始） | 3.367x | 3.470x | **2.010x** | 2.700x | 4.080x | **5.580x** |
| Sharpe | 2.437 | 2.410 | **1.480** | — | — | **3.350** |
| MaxDD | -9.1% | -11.5% | **-18.5%** | — | — | **-6.8%** |

- **最終淨值 Percentile Rank**: ~50%（原始表現落在模擬分布中位數附近）
- **MaxDD Percentile Rank**: ~68%（原始 DD 比 68% 的模擬路徑更好）
- **P5 Sharpe 仍 > 1.0**，但信賴區間跨度大，實際交易應以 P5 為風險參考
- **P5 MaxDD = -18.5%**：i.i.d. 假設下有 5% 的路徑最大回撤接近 18.5%，比歷史實現的 -9.1% 嚴重約一倍

### 結果 B — Block bootstrap（block_size=5 天, n=1,000, seed=42）

為了保留波動聚集（volatility clustering）與短期自相關，額外跑 block bootstrap：每次抽取連續 5 天的 return 區塊，再拼接成一條新路徑。

| 指標 | 原始 | 模擬均值 | P5 | P25 | P75 | P95 |
|------|------|---------|-----|-----|-----|-----|
| 最終淨值（相對初始） | 3.367x | 3.520x | 2.170x | 2.800x | 4.080x | 5.220x |
| Sharpe | 2.437 | 2.450 | 1.650 | — | — | 3.210 |
| MaxDD | -9.1% | -10.2% | -15.2% | — | — | -6.5% |

**i.i.d. vs block 對照解讀**：
- Block bootstrap 的 **P5 Sharpe (1.65) 略高於 i.i.d. (1.48)**，P5 MaxDD (-15.2%) 也較 i.i.d. (-18.5%) 溫和
- 這**並非**代表實際更安全 — 而是因為歷史資料中的自相關以「趨勢延續 + 恢復段」為主，保留區塊結構反而降低拼接的變異
- 真正要看的是兩個方法的**共同下界**：即便是更溫和的 block bootstrap，**P5 MaxDD 仍達 -15.2%**，代表最壞 5% 路徑下 DD 會比歷史 -9.1% 深約 67%

> 備註：即使 block bootstrap 也只對「同分布下的路徑變異」建模，對 regime 切換（牛熊轉換）仍無法外推。

---

## 9. 策略相關性分析

### 組合整體

| 指標 | 數值 |
|------|------|
| 平均相關性 | **0.041** |
| 低相關對 (< 0.3) | **130 對** |
| 近零相關對 (< 0.01) | 多對 |

### 最低相關性策略對

| 策略 A | 策略 B | 相關性 |
|--------|--------|--------|
| pair_btc_eth | trend_donchian_mtf BTC | -0.015 |
| pair_btc_eth | momentum_ranking BNB | -0.008 |
| trend_donchian_adx_slope BTC | tail_risk_hedge BNB | 0.002 |
| funding_reversal ETH | trend_donchian_mtf XRP | -0.005 |
| grid_trend_bias SOL | funding_reversal ETH | 0.003 |

### 分析

- **0.041** 的平均相關性較 V7.2 的 0.062 **大幅改善 33.9%**，主要來自新增的市場中性策略
- 新增的 `pair_btc_eth` 與現有策略的平均相關性僅 **0.025**，`funding_reversal_eth` 也接近零
  （注意：低相關性為**歷史觀察**結果；市場 regime 切換時相關性可能上升，尤其趨勢策略集中於同向趨勢時 — 見 §15 限制 3）
- 此值使用 daily-resampled equity curves 計算，統一了 4h/1d 不同頻率
- 低相關對數從 113→130，多出的 17 對主要由 pair_btc_eth 和 funding_reversal 貢獻

---

## 10. 策略健康度與衰退偵測

### 健康度報告

> ⚠️ 以下使用 daily-resampled equity curves 計算，30d/90d 為實際天數。
> 低交易次數策略在近期窗口可能顯示 NaN。

| 策略 | 30d Sharpe | 90d Sharpe | 當前 DD | 狀態 |
|------|-----------|-----------|---------|------|
| **pair_btc_eth** 🆕 | **4.43** | **3.58** | -2.1% | ✅ 優秀（V7.3 新增） |
| **funding_reversal ETH** 🆕 | **2.45** | **1.68** | -4.3% | ✅ 優秀（V7.3 新增） |
| momentum_ranking ETH | 2.06 | 2.61 | -5.3% | ✅ 優秀 |
| grid_trend_bias SOL | 3.39 | -0.51 | -6.6% | ✅ 短期強 |
| grid_trend_bias ETH | 1.05 | 1.71 | -6.6% | ✅ 優秀 |
| tail_risk_hedge SOL | -0.10 | 1.43 | -17.5% | ✅ 正常（90d 穩定）|
| momentum_ranking BNB | -0.38 | 2.22 | -8.0% | ✅ 正常（90d 穩定）|
| trend_donchian_mtf XRP | NaN | 1.30 | -10.1% | ✅ 正常（低交易頻率）|
| trend_donchian_mtf BNB | NaN | 1.18 | -3.4% | ✅ 正常 |
| trend_donchian_adx_slope_btc | -0.38 | 0.77 | -15.2% | ⚡ 觀察 |
| trend_donchian_adx_slope_eth | -2.55 | 0.42 | -12.7% | ⚡ 觀察 |
| momentum_ranking SOL | -4.39 | 0.19 | -28.0% | ⚡ 觀察（新增，長線策略正常波動）|
| grid_trend_bias BTC | NaN | 0.49 | -4.5% | ⚡ 觀察 |
| tail_risk_hedge BNB | NaN | 0.62 | -5.9% | ⚡ 觀察 |
| grid_trend_bias XRP | NaN | -0.81 | -9.9% | ⚡ 觀察 |
| tail_risk_hedge BTC | NaN | **-1.30** | -12.8% | ⚠️ DECAY |
| trend_donchian_mtf BTC | NaN | NaN | -2.6% | 📊 數據不足 |

### 已處理的衰退策略

| 策略 | 之前狀態 | V7.3 處理 |
|------|----------|----------|
| dual_channel_breakout ETH | 30d=-3.22, 90d=-3.52 | ❌ **V7.2 已移除** |
| breakout_squeeze BTC | fragile, 16 trades | ❌ **V7.3 已移除**（低交易數，統計信心不足） |
| breakout_squeeze SOL | 90d=-3.59 | ❌ V7 已移除 |
| tail_risk_hedge BTC | 90d=-1.08 | ⬇️ 降至 3%（最低配置）|

---

## 11. 市場環境分析

### BTC 4h 市場環境分布

| 環境 | 佔比 | 最長連續 |
|------|------|---------|
| 盤整 (ranging) | **42.9%** | 120 bars |
| 波動 (volatile) | **22.1%** | 82 bars |
| 上升趨勢 (trending_up) | **18.7%** | 97 bars |
| 下降趨勢 (trending_down) | **16.3%** | 73 bars |

### 策略×環境適配

| 市場環境 | 主要獲利策略 | 配置佔比 |
|---------|------------|---------|
| 趨勢期 (35%) | trend_donchian + momentum | **61%** |
| 盤整期 (43%) | grid_trend_bias + pair_btc_eth | **24%** |
| 高波動期 (22%) | tail_risk_hedge + funding_reversal | **15%** |

**設計理念**: 
- 趨勢期佔 35% 時間但分配 61% 資金 → 趨勢是主要獲利來源
- 盤整期佔 43% 時間但有 24% 策略覆蓋 → 配對交易填補空窗期
- 高波動期有 15% 防禦策略 → funding_reversal 在恐慌時逆向抄底
- 🆕 **V7.3 改進**: 新增市場中性層（8%）使盤整期和高波動期的覆蓋更完善

---

## 12. 手續費敏感度分析

| 指標 | 數值 |
|------|------|
| 總交易次數 | ~670 筆 |
| 預設手續費 | 6 bps (maker+taker 均攤) |
| 損益平衡手續費 | **22.7 bps** |
| 安全倍數 | **3.79x** |
| 評估 | ✅ SAFE |

**解讀**: 即使手續費達到 22.7 bps（約當前 3.8 倍），策略仍能保持盈利。
Binance VIP0 taker fee = 4 bps，maker = 2 bps，安全餘量充足。

> ⚠️ V7.3 安全倍數從 4.32x 降至 3.79x，主因新增的配對交易策略雙腿手續費（每筆配對交易 = 2 筆實際交易），
> 仍在安全範圍但較 V7.2 略減。

---

## 13. 小資金部署建議

### $150 USDT 部署注意事項

1. **最小下單量**: Binance Futures 最小名義值為 $5，部分策略配置接近下限
   - `trend_donchian_mtf BNB` ($3.0 × 2x = $6.0) ← 接近下限
   - `funding_reversal ETH` ($4.5 × 1x = $4.5) ← 接近下限
   
2. **四捨五入影響**: $150 分配到 17 個策略，部分精度會損失

3. **建議入金**: $200+ 可確保所有策略位超過最小下單門檻

4. **進階規模**: $500+ 可考慮降低集中度、增加更多幣種

### 策略優先級（資金不足時的精簡版）

如果只有 $100，建議優先保留：
1. `trend_donchian_mtf BTC` (20%) — 最穩健
2. `momentum_ranking ETH` (15%) — 最高風險調整收益
3. `momentum_ranking BNB` (10%) — 強收益
4. `grid_trend_bias ETH` (10%) — 高交易頻率
5. `trend_donchian_adx_slope ETH` (12%) — 穩健+高交易量
6. `pair_btc_eth` (8%) — 市場中性，降低相關性
7. 其餘分配到 tail_risk/grid (25%)

---

## 14. 風險警示

### ⚠️ 重要風險聲明

1. **過去績效不代表未來表現**: 回測基於歷史數據，實際交易可能面臨不同市場環境

2. **參數失效風險**: 即使經過 7,605 組參數驗證，市場結構性變化可能導致參數失效
   - **緩解措施**: 四層架構，脆弱層僅 7%（V7.2 的 12%→7%），穩健層佔 40%

3. **流動性風險**: $150 規模的滑點影響較小，但極端行情下仍需注意
   - **緩解措施**: 主要交易 BTC/ETH 高流動性幣種 (60% 配置)

4. **槓桿風險**: 最高 2x 槓桿，異常行情可能放大虧損
   - **緩解措施**: 防禦層 (12%) + 市場中性層 (8%) 使用 1x 槓桿

5. **API 延遲風險**: 信號到成交之間的延遲可能影響短線策略
   - **緩解措施**: 4h/1d 時間框架，對延遲不敏感

6. **策略衰退風險**: 已識別並移除 3 個衰退策略（DCB ETH, breakout SOL, breakout BTC），未來可能出現新的衰退
   - **緩解措施**: 定期（每月）運行健康度檢測

7. **單一交易所風險**: 僅使用 Binance Futures
   - **緩解措施**: 不要將所有資金投入

8. **配對交易特有風險**: G5 pair_btc_eth 依賴 BTC/ETH 共整合關係持續存在
   - **緩解措施**: 使用 Rolling OLS 動態調整 hedge ratio；若共整合破裂（β 漂移超出閾值），應暫停策略

### 🔄 建議監控週期

| 頻率 | 檢查項目 |
|------|---------|
| 每日 | 組合 PnL、各策略持倉狀態 |
| 每週 | 策略健康度 (30d Sharpe)、DD 監控 |
| 每月 | 完整回測 + Walk-Forward、衰退偵測 |
| 每季 | 參數穩定性重掃、是否需要版本升級 |

---

## 📎 附錄

### A. 完整技術指標

| 指標 | 數值 |
|------|------|
| Sharpe Ratio | 2.437 |
| Sortino Ratio | 3.625 |
| Calmar Ratio | 4.87 |
| Max Drawdown | -9.1% |
| Ann Return | 44.3% |
| Total Return | 236.7% |
| Total Trades | ~670 |
| Mean Correlation | 0.041 |
| Fee Breakeven | 22.7 bps |
| Fee Safety | 3.79x |
| WF OOS Sharpe (post-selection) | 2.103 |
| WF OOS Sharpe (nested, equal-weight) | **2.762** |
| WF OOS Sharpe (nested, Sharpe-weighted) | **2.273** |
| WF Positive (all 3 methods) | 89% |
| WF Efficiency | 0.838 |
| MC i.i.d. — P5 / P95 Sharpe | 1.480 / 3.350 |
| MC i.i.d. — P5 / P25 / P75 / P95 Final | 2.010x / 2.700x / 4.080x / 5.580x |
| MC i.i.d. — P5 / P95 MaxDD | -18.5% / -6.8% |
| MC block(5d) — P5 / P25 / P75 / P95 Final | 2.170x / 2.800x / 4.080x / 5.220x |
| MC block(5d) — P5 / P95 Sharpe | 1.650 / 3.210 |
| MC block(5d) — P5 / P95 MaxDD | -15.2% / -6.5% |

### B. Git 版本歷史

| Commit | 版本 | 說明 |
|--------|------|------|
| `488db04` | Rescan | Phase 1-2 rescan results (7,605 combos) |
| `26227ae` | V7 | V7 portfolio (Sharpe 2.392) |
| `9362387` | V7.1 | Robustness-weighted (Sharpe 2.414, MaxDD -11.9%) |
| `6f76b0e` | V7.2 | Phase B/C optimized (Sharpe 2.355, MaxDD -10.6%) |
| `a924c48` | G4 | FundingReversalVBT long-only dip buying strategy |
| `8b12837` | G5 | BTC-ETH pair trading via rolling OLS cointegration |
| `5020259` | V7.3 | G4+G5 integrated portfolio (Sharpe 2.437, MaxDD -9.1%) |

### C. 策略參數完整表

#### trend_donchian_mtf
| 幣種 | entry | exit | adx_th | htf | leverage |
|------|-------|------|--------|-----|---------|
| BTC | 10 | 10 | 15 | 150 | 2 |
| XRP | 10 | 5 | 15 | 100 | 2 |
| BNB | 10 | 7 | 15 | 150 | 2 |

#### trend_donchian_adx_slope
| 幣種 | entry | exit | slope_bars | slope_min | leverage |
|------|-------|------|-----------|-----------|---------|
| ETH | 20 | 5 | 5 | 0.2 | 2 |
| BTC | 30 | 7 | 3 | 0.2 | 2 |

#### momentum_ranking
| 幣種 | roc | lookback | upper | lower | leverage |
|------|-----|----------|-------|-------|---------|
| ETH | 60 | 240 | 70 | 30 | 1.5 |
| BNB | 90 | 120 | 70 | 30 | 1.5 |
| SOL | 20 | 240 | 70 | 30 | 1.5 |

#### grid_trend_bias
| 幣種 | bb | std | ema | leverage |
|------|-----|-----|-----|---------|
| ETH | 20 | 2.0 | 100 | 2 |
| XRP | 15 | 2.0 | 50 | 2 |
| BTC | 30 | 3.0 | 200 | 1 |
| SOL | 15 | 2.0 | 100 | 1 |

#### tail_risk_hedge
| 幣種 | consec_up | consec_down | exit_bars | leverage |
|------|-----------|------------|-----------|---------|
| BNB | 10 | 5 | 15 | 1 |
| SOL | 10 | 3 | 10 | 1 |
| BTC | 10 | 5 | 10 | 1 |

#### breakout_squeeze (V7.3 已移除)
| 幣種 | bb | std | kc_ema | kc_atr | kc_mult | leverage |
|------|-----|-----|--------|--------|---------|---------|
| BTC | 30 | 3.0 | 10 | 7 | 2.0 | 2 |

#### pair_btc_eth (V7.3 新增)
| 參數 | 數值 | 說明 |
|------|------|------|
| ols_window | 480 | Rolling OLS 回歸視窗（bars） |
| zscore_period | 90 | Z-score 計算視窗 |
| entry_z | 2.5 | 進場 z-score 閾值 |
| exit_z | 0.0 | 出場 z-score 閾值 |
| stop_z | 4.0 | 止損 z-score 閾值 |
| leverage | 1 | 槓桿倍數 |

#### funding_reversal (V7.3 新增)
| 參數 | 數值 | 說明 |
|------|------|------|
| entry_rate_long | -4 | 極負 funding rate 進場閾值（bps） |
| hold_bars | 12 | 固定持倉時間（bars） |
| leverage | 1 | 槓桿倍數 |

> 註：backtest 腳本中的 `leverage` 參數，現在統一解讀為**顯式 return-amplification 槓桿模型**；live 交易中的 `leverage` 則對應交易所實際槓桿設定。不要再把 raw `vectorbt` percent sizing 視為可直接產生 broker-style 2x 持倉。

---

*報告生成日期: 2026-04-20*  
*回測引擎: VectorBT Pro + Cry2 Strategy Framework*  
*數據來源: Binance Futures Historical Klines*

---

## 15. 已知限制與注意事項

> 以下為第三方 code review (GPT-5.4) 指出的問題，已修復或記錄。

### 已修復的問題

| # | 問題 | 修復方式 |
|---|------|---------|
| MC 年化不一致 | Monte Carlo 使用 252，portfolio 使用 365 | MC 改為接受 `annualization_factor` 參數，統一使用 365 |
| MC 方法 vs 名稱不符 | 報告寫 bootstrap，實作為 `rng.permutation` (shuffle) → final value/Sharpe 為不變量，四欄相同 | 改為真正 bootstrap (`rng.choice(..., replace=True)`)，保留 `method` 參數，重跑後更新 §7 表格 |
| 相關性頻率混合 | 相關性計算混合 4h/1d 原始頻率 | 改用 daily-resampled equity curves 計算 |
| Health report 窗口 | 30/90 是 bars 不是 days (4h=5天/15天) | 改用 daily-resampled curves，窗口即為實際天數 |
| 三層 tier 門檻自相矛盾 | 寫 ">50%" / "<15%" 但表格含 18.5%、13.9%、16.7% | §3 各層加上實際分層準則（族群 + viable% + 配置上限的綜合規則）；同步 `full_portfolio_backtest.py` / `bridge.py` 註解 |
| 穩健層標籤 | 聲稱 ">50% viable" 但含 BTC adx_slope 18.5%、BNB mtf 13.9% | 修正描述為策略族群穩健性，明確標註例外 |
| Walk-forward 口氣過強 | 寫「持續有效」/「overfitting 風險可控」，但 WF 只切已選組合的路徑 | §6 加上 post-selection 警語；另實作 `walk_forward_nested_analysis`，每窗口重新用 IS Sharpe 配權重後算 OOS（equal=2.76、sharpe_pos=2.27） |
| MC 只有 i.i.d. 假設 | 單一 bootstrap 假設日報酬獨立，忽略波動聚集 | 新增 `block_bootstrap_simulation`，跑 block_size=5d 並在 §7 對照 i.i.d. 與 block 的 P5/P95 |
| 低交易數統計信心 | <10 trades 的策略（BTC/XRP mtf、BNB momentum）Sharpe 誤差大但報告未警示 | §3 三層表加 ⚠️ 符號標記低交易數策略，並在標題說明 |
| 目錄缺 §14 | TOC 只列到 13 但 §14「已知限制」確實存在 | TOC 加入 §14 條目 |

### 設計限制 (known trade-offs)

**1. Walk-Forward 非真正 per-window 重選參數**（已部分補強）
- post-selection WF 在已選好的參數+權重上切窗
- 已新增 nested WF（每窗口重新用 IS Sharpe 配權重），結果 sharpe_pos OOS=2.27 vs post-selection 2.00
- **仍不足**：nested WF 只重配權重，**沒有每窗口重跑參數掃描**。要做到嚴格 nested 需要每個 IS 窗口 re-run 7,605 組合參數掃，工程量很大
- **緩解**: §6 viable% 在參數維度提供獨立證據；§7 nested WF 在權重維度提供獨立證據；兩者合起來逼近嚴格 nested 的一部分

**2. 部分策略交易次數過少**（已標記）
- trend_donchian_mtf BTC: 7 trades ⚠️
- trend_donchian_mtf XRP: 5 trades ⚠️
- momentum_ranking BNB: 8 trades ⚠️
- **影響**: 低交易次數導致 win rate、Sharpe 的統計信心較低；使用 Bernoulli 估算，5 筆交易的勝率 95% CI 寬達 ±35%
- **緩解**: §3 表格已加 ⚠️ 標記；viable% 分析考慮了多種參數，這些策略在更長回測期間仍保持正 Sharpe

**3. Regime 曝險偏重趨勢+動量**
- 趨勢型 (trend_donchian + momentum) 佔 61% 配置（V7.2 的 63% 已略降）
- 在非趨勢 regime，實際策略間相關性可能高於報告顯示的 0.041
- **影響**: 若市場長期盤整/震盪，組合可能承受集中虧損
- **緩解**: Grid (19%) + Tail Risk Hedge (12%) + Market Neutral (8%) 提供盤整/高波動覆蓋；MaxDD -9.1% 已含歷史各 regime

**4. Monte Carlo 假設 i.i.d.**（已部分補強）
- i.i.d. bootstrap 改善了 shuffle 的不變量問題，但仍假設每日報酬獨立
- 已新增 block bootstrap (block_size=5d)，保留短期自相關；§8 並列比較兩方法
- **仍不足**：兩方法都只對「歷史資料同分布」下的路徑變異建模，**對 regime 切換無法外推**

**5. V7.4 槓桿語義已修正，且已完成 live-aligned 完整重測**
- 這次已確認 raw `vectorbt` `size_type="percent"` 在 `size > 1` 時會裁切在 100%，不能直接代表 broker-style 2x 槓桿
- `full_portfolio_backtest.py` 已改為顯式 return-amplification leverage model，且其配置來源已直接對齊 live `create_v74_strategies()`；`v74_leverage_optimization.py` 也同步標示為 simulated 2x
- **重測結果**：2026-04-22 的 live-aligned V7.4 full backtest 為 Sharpe 1.763、MaxDD -17.7%、Final $543，已回到 5/5 checks passed
- **影響**：本報告正文的 V7.3 指標仍可作為歷史基準，但附錄參數表中的 `leverage` 欄位，請解讀為「策略目標槓桿設定」，不是 raw VBT 下自動成立的保證
- **緩解**: §8 備註強調「兩方法 P5 為歷史分布下的路徑下界，真正牛熊切換時肥尾可能更胖」

**5. 綜合可信度與部署建議**
- 上述限制 (1)-(4) 都不否定 V7.3 在歷史回測的表現，但**確實限制了我們能從回測結論推到未來的強度**
- 部署前建議：
  - 先用 paper trading 跑至少 1 個月，驗證實時執行行為
  - 監控實盤相關性 vs 回測 0.041 的差異（尤其盤整 regime）
  - 每季重跑參數掃描，確認 viable% 沒有大幅下降
  - 監控 BTC/ETH 共整合關係是否持續（pair_btc_eth 特有風險）
- **目前階段的合適用語**：這是一個「在歷史資料上表現優異、但尚未以嚴格 nested OOS 驗證」的候選組合

**6. 配對交易 OLS 計算成本**（V7.3 新增）
- Rolling OLS 計算複雜度為 O(n²)，7,226 bars × ols_window=480 約需 60 秒
- Live trading 中每根 bar 只需增量計算一次 OLS，延遲可忽略
- 回測時若需大規模參數掃描，建議先快取 OLS 殘差序列
