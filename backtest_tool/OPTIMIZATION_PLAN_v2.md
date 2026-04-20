# 📈 Cry2 策略優化總計畫 v2.0 — 長線穩定 + 短線補位 + 新策略探索

**目標**: 長線穩定獲利為核心，短線策略補充空窗期獲利，全面參數優化 + 10 個新策略方向
**日期**: 2026-04-20
**基於**: 54 策略回測結果 + OPTIMIZATION_PLAN.md 50 項任務
**版本**: v2.0（進取版目標 + 新策略探索）

---

## 🎯 核心策略定位（依回測結果重新分類）

### 🟢 長線核心（60% 資金）— 穩定趨勢追蹤

| 策略 | 報酬率 | Sharpe | MaxDD | 時間框架 | 定位 |
|------|--------|--------|-------|----------|------|
| **momentum_ranking** | +143.5% | 1.43 | -22.8% | 1d | 🌟主力長線 |
| **trend_donchian_mtf** | +16.5% | 0.48 | -20.5% | 4h | 多週期趨勢 |
| **trend_donchian_adx_slope** | +14.4% | 0.37 | -29.0% | 4h | ADX 強化趨勢 |
| **trend_donchian** (優化版) | +48.4% | 1.18 | -10.4% | 4h | 優化後核心 |

### 🟡 短線補位（30% 資金）— 填補趨勢空窗

| 策略 | 報酬率 | Sharpe | MaxDD | 時間框架 | 定位 |
|------|--------|--------|-------|----------|------|
| **grid_trend_bias** | +25.9% | 0.66 | -19.7% | 4h | 盤整期網格 |
| **breakout_squeeze** | +14.0% | 0.43 | -16.7% | 4h | 盤整→突破 |
| **mean_reversion_bb** (優化版) | +9.4% | 0.58 | -7.2% | 1h | 短線回歸 |
| **grid_funding_aware** | +7.9% | 0.31 | -21.0% | 4h | 資費感知網格 |

### 🔵 防禦/對沖（10% 資金）— 極端行情保護

| 策略 | 定位 |
|------|------|
| **long_horizon_eth** | 長線持有 ETH（+2.7%，低波動） |
| **regime_switcher** | 市場環境自動切換 |

---

## 📋 執行階段（共 7 個 Phase，37 項任務）

### Phase 1: 長線核心策略參數優化
> 目標：將 4 個長線策略的參數從「預設」調至「最佳」

- **1A** momentum_ranking 全幣種參數掃描（BTC/ETH/SOL）
  - 掃描 ROC period、排名週期、持倉天數
  - 目標：保持 Sharpe > 1.0，MaxDD < 25%
- **1B** trend_donchian 最佳化參數驗證
  - 已知最佳：entry=30, exit=5, ADX≥30
  - 跨幣種驗證（ETH/SOL 上是否仍有效）
  - 加入 stop_loss_pct 固定止損
- **1C** trend_donchian_mtf 參數掃描
  - 測試多時間框架組合（4h+1d、4h+8h）
  - 目標：Sharpe > 0.5
- **1D** trend_donchian_adx_slope 參數掃描
  - 掃描 ADX slope 閾值、entry/exit period
  - 目標：MaxDD 從 -29% 降至 < -20%

### Phase 2: 短線補位策略優化
> 目標：讓短線策略在趨勢空窗期有效獲利

- **2A** grid_trend_bias 參數掃描（目前最強網格）
  - 掃描 grid_count、trend_bias 強度、EMA period
  - 目標：Sharpe > 0.7
- **2B** breakout_squeeze 參數優化
  - 掃描 squeeze detection 閾值、突破確認 bar 數
  - 加入成交量確認過濾
- **2C** mean_reversion_bb 止損修復
  - 已知最佳：bb=15, std=2.5, RSI 30/75
  - 替換固定止損為 ATR trailing stop
  - ETH 上回撤必須從 -82% 降至 < -30%
- **2D** grid_funding_aware 優化
  - 結合 funding rate 數據調整網格方向
  - 目標：交易次數從 15 提升至 > 30

### Phase 3: 風控機制強化
> 目標：所有策略 MaxDD < 30%，組合 MaxDD < 20%

- **3A** 全局組合止損（Portfolio-Level Stop）
  - 組合回撤 > 20% → 暫停所有交易 8 天
- **3B** 動態倉位管理
  - 實作 fixed_fraction (50%) + ATR-based sizing
  - 高波動 → 小倉位，低波動 → 大倉位
- **3C** 波動率自適應槓桿
  - ATR percentile > 80 → 降至 1x
  - ATR percentile < 20 → 允許 2-3x
- **3D** 連續虧損保護
  - 連虧 5 次 → 暫停 24 bars
- **3E** 最大持倉時間限制
  - 長線：120 bars (20天)，短線：168 bars (7天)

### Phase 4: 市場環境感知 + 策略切換
> 目標：趨勢期啟動長線，盤整期切換短線，高波動期降低曝險

- **4A** Market Regime Detector
  - trending_up/down（ADX>25 + EMA方向）
  - ranging（ADX<20）
  - volatile（ATR percentile>80）
- **4B** 環境感知策略選擇器
  - trending → momentum_ranking + trend_donchian（長線核心）
  - ranging → grid_trend_bias + mean_reversion_bb（短線補位）
  - volatile → 降低倉位 50% 或暫停
- **4C** 策略間相關性分析
  - 找出相關性 < 0.3 的策略對
  - 作為組合配置依據
- **4D** 滾動窗口 Sharpe 分析
  - 30 天/90 天滾動 Sharpe 視覺化
  - 標記策略衰退期

### Phase 5: 組合優化 + 穩健性驗證
> 目標：找到最佳資金配置，確認不是 overfitting

- **5A** 多維度組合優化
  - 等權重 vs Sharpe最佳化 vs Calmar最佳化 vs 最小回撤
  - 跨幣種 × 策略的 12 維配置
- **5B** Walk-Forward 分析（最關鍵）
  - 12 月 train + 3 月 test，步進 3 月
  - OOS Sharpe > 0.5 才算通過
- **5C** Monte Carlo 模擬
  - 1000 次 PnL 順序隨機打亂
  - 量化 95% CI 的最大回撤
- **5D** 參數穩定性熱力圖
  - 鄰近參數 Sharpe 變化 → 判斷 overfit 風險
- **5E** 壓力測試
  - 極端事件期間 ±7 天獨立回測
- **5F** 手續費敏感度分析
  - 找出每個策略的盈虧平衡手續費

### Phase 6: 最終整合 + 部署準備
> 目標：產出可上線的策略組合 + 部署建議

- **6A** 更新 `config/strategies.yaml` 資金配置
  - 長線核心 60%，短線補位 30%，防禦 10%
- **6B** 完整最終回測
  - 全部優化結果 + 風控 + 環境適應
  - Walk-forward 驗證
- **6C** 部署建議書
  - 推薦策略組合、風控設定、監控指標
  - 策略失效判斷條件（30天 Sharpe < -1）
- **6D** 更新 `config/optimized_params.yaml`
  - 每策略 × 每幣種的最佳參數

### Phase 7: 新策略探索（10 個新方向）

#### 趨勢增強（長線）

**7A — 多幣種動量輪動 (Multi-Asset Momentum Rotation)**
- 每週計算 BTC/ETH/SOL 的 N 日動量排名
- All-in 最強幣種，或按排名加權配置
- 時間框架：1d
- 目標：強化版 momentum_ranking，Sharpe > 1.5

**7B — 趨勢強度加權 (Trend Strength Sizing)**
- ADX 值越高 → 倉位比例越大（信心映射倉位）
- ADX < 25 = 0%，ADX 30 = 50%，ADX 40+ = 100%
- 可與任何趨勢策略疊加
- 目標：同策略 Sharpe 提升 20%+

**7C — 雙通道突破 (Dual Channel Breakout)**
- Donchian Channel + Keltner Channel 同時突破才進場
- 大幅降低假突破，勝率預期 > 50%
- 時間框架：4h
- 目標：勝率從 35% → 50%+

#### 波動率獲利（中線）

**7D — 波動率均值回歸 (Volatility Mean Reversion)**
- 計算 ATR percentile 作為 VIX-like 指標
- ATR > 90th percentile → 做空波動（期待回歸）
- ATR < 10th percentile → 做多波動（期待爆發）
- 實際操作：高波動做 MR，低波動做 Breakout
- 時間框架：4h

**7E — Gamma Scalping 模擬 (Regime-Adaptive Grid)**
- 高波動期 → 密集網格（gamma scalping 邏輯）
- 低波動期 → 寬網格 + 趨勢偏差
- ATR 動態調整 grid spacing
- 時間框架：1h-4h

#### 市場微結構（短線）

**7F — Funding Rate 趨勢跟隨 (Funding Contrarian)**
- Funding Rate > +0.03% → 大眾偏多 → 做空
- Funding Rate < -0.03% → 大眾偏空 → 做多
- 反向大眾心理，利用過度槓桿的清算壓力
- 時間框架：8h（配合 funding 結算週期）

**7G — 價格-成交量背離 (Price-Volume Divergence)**
- 價漲量縮 → 上漲動力減弱 → 短線做空
- 價跌量增 → 恐慌出貨 → 等量縮後反轉做多
- OBV / MFI 等指標輔助判斷
- 時間框架：4h

**7H — 時間段過濾 (Time-of-Day Filter)**
- 統計歷史每小時/每日平均收益與勝率
- 只在勝率最高的時段開倉（例如亞洲時段 UTC+8 09:00-12:00）
- 可疊加到任何策略上作為 overlay
- 時間框架：1h

#### 組合/對沖

**7I — BTC-ETH 配對交易 (Pairs Trading)**
- 計算 BTC/ETH 價格 ratio 的 z-score
- z-score > 2 → 做空 BTC / 做多 ETH（ratio 回歸）
- z-score < -2 → 做多 BTC / 做空 ETH
- 市場中性，降低系統性風險
- 時間框架：4h-1d

**7J — 尾部風險對沖 (Tail Risk Hedge)**
- 用 5-10% 資金做極端行情反向部位
- 指標：連續上漲天數 > 14 → 小額做空（黑天鵝保護）
- 連續下跌天數 > 7 → 小額做多（恐慌反彈）
- 成本控制：每月最多虧損 1% 本金
- 目的：降低組合尾部風險

---

## 📊 新的資金配置方案（vs 舊版）

### 舊版（不合理）
```
funding_arb: 60%  ← 回測虧損，交易太少
trend_donchian: 25%
grid_futures: 10%
mean_reversion_bb: 5%
```

### 新版（依回測結果）
```
# 長線核心 60%
momentum_ranking:        25%  ← Sharpe 1.43，最強策略
trend_donchian (優化版): 20%  ← 優化後 Sharpe 1.18
trend_donchian_mtf:      10%  ← 多週期確認
trend_donchian_adx_slope: 5%  ← ADX 強化

# 短線補位 30%
grid_trend_bias:         12%  ← 最強網格
breakout_squeeze:         8%  ← 盤整突破
mean_reversion_bb (優化): 6%  ← 短線回歸
grid_funding_aware:       4%  ← 資費網格

# 防禦 10%
long_horizon_eth:         5%
regime_switcher:          5%
```

---

## ⚡ 執行優先級（Quick Wins）

1. **Phase 1B** — trend_donchian 優化參數跨幣種驗證（已有結果，只需驗證）
2. **Phase 2C** — mean_reversion_bb 止損修復（ETH -82% 是最大痛點）
3. **Phase 3B** — 動態倉位管理（最簡單有效的風控）
4. **Phase 1A** — momentum_ranking 全面掃描（確認王牌策略穩健性）
5. **Phase 5B** — Walk-Forward（決定一切是否可上線）

---

## 🔑 驗收標準（進取版 v2.0）

| 指標 | 目標值 |
|------|--------|
| 組合年化 Sharpe | **> 0.8** |
| 組合年化報酬 | **> 25%** |
| 組合最大回撤 | **< 20%** |
| Walk-Forward OOS Sharpe | **> 0.5** |
| 長線策略勝率 | > 45% |
| 短線策略勝率 | > 55% |
| 策略間相關性 | < 0.4 |
| Calmar Ratio | > 1.0 |

---

## 📅 完整執行順序

```
Phase 1 (長線參數優化)  ──┐
Phase 2 (短線補位優化)  ──┤
Phase 7 (新策略開發)    ──┤
                          ├→ Phase 3 (風控強化)
                          ├→ Phase 4 (環境感知)
                          │
                          ├→ Phase 5 (組合+穩健性)
                          │
                          └→ Phase 6 (最終整合+部署)
```

Phase 1/2/7 可同步進行，是最大並行區。
