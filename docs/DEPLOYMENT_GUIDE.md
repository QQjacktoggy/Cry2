# 🚀 Cry2 部署建議書 — v2.0 Optimized

## 📋 系統總覽

| 項目 | 值 |
|------|-----|
| 策略數量 | 10 個（4 長線 + 4 短線 + 2 防禦）|
| 總策略庫 | 64 個策略（含 Phase 7 新策略）|
| 交易幣種 | BTC, ETH, SOL, BNB, XRP |
| 時間框架 | 1h / 4h / 8h / 1d |
| 初始資金 | 10,000 USDT（建議最低）|

---

## 🎯 推薦策略組合

### 長線核心 (60%)
| 策略 | 配置 | 幣種 | Sharpe | 定位 |
|------|------|------|--------|------|
| momentum_ranking | 25% | BTC/ETH/SOL | 1.43-1.93 | 主力動量 |
| trend_donchian | 20% | BTC/ETH/SOL | 0.85-1.18 | 趨勢突破 |
| trend_donchian_mtf | 10% | BTC/ETH | 1.02 | 多週期確認 |
| trend_donchian_adx_slope | 5% | BTC/ETH | 0.65 | ADX 強化 |

### 短線補位 (30%)
| 策略 | 配置 | 幣種 | Sharpe | 定位 |
|------|------|------|--------|------|
| grid_trend_bias | 12% | BTC/ETH | 1.14-1.76 | 盤整網格 |
| breakout_squeeze | 8% | BTC/ETH | 0.92-1.33 | 擠壓突破 |
| mean_reversion_bb | 6% | BTC/ETH | 0.42-0.58 | 均值回歸 |
| grid_funding_aware | 4% | BTC/ETH | 0.28-0.31 | 資費網格 |

### 防禦 (10%)
| 策略 | 配置 | 幣種 | 定位 |
|------|------|------|------|
| long_horizon_eth | 5% | ETH | 長線持有 |
| regime_switcher | 5% | BTC/ETH | 環境自動切換 |

---

## ⚙️ 風控設定

### Phase 3 風控模組
| 控制 | 設定 | 說明 |
|------|------|------|
| Portfolio Stop | DD > 20% → 暫停 48 bars | 組合保護 |
| Position Sizing | ATR-based, 每筆風險 2% | 波動率倉位 |
| Adaptive Leverage | 1x–3x, ATR percentile 調控 | 自適應槓桿 |
| Consecutive Loss | 連虧 5 次 → 暫停 24 bars | 連虧保護 |
| Max Hold | 長線 120 bars / 短線 168 bars | 超時退出 |

### Phase 4 環境感知
| 環境 | 條件 | 策略路由 | 倉位倍率 |
|------|------|----------|----------|
| trending_up | ADX > 25 + Close > EMA | 長線核心 | 1.0x |
| trending_down | ADX > 25 + Close < EMA | 動量+突破 | 0.7x |
| ranging | ADX < 20 | 短線補位 | 0.8x |
| volatile | ATR > P80 | 暫停/大幅減倉 | 0.3x |

---

## 📊 監控指標

### 必監控
| 指標 | 閾值 | 動作 |
|------|------|------|
| 30d Rolling Sharpe | < -1.0 | 🔴 策略衰退警告 |
| Portfolio DD | > 15% | 🟡 風控預警 |
| Portfolio DD | > 20% | 🔴 自動暫停 |
| 連續虧損 | > 5 次 | 🔴 自動暫停 24 bars |

### 建議監控
| 指標 | 頻率 | 說明 |
|------|------|------|
| 90d Rolling Sharpe | 每日 | 長期趨勢衰退 |
| 策略間相關性 | 每週 | 相關性 > 0.4 = 需調整 |
| 手續費佔比 | 每週 | 手續費 > 利潤 50% = 過度交易 |
| Regime 分佈 | 每日 | 確認環境感知正常 |

---

## ⚠️ 策略失效判斷

以下任一條件觸發，應停止該策略並重新評估：

1. **30 天 Sharpe < -1.0** — 策略持續虧損
2. **連續 90 天 Calmar < 0.3** — 風險報酬比過低
3. **MaxDD 突破歷史最大 1.5 倍** — 異常回撤
4. **勝率低於 25%（連續 50 筆交易）** — 策略完全失效
5. **Walk-Forward OOS Sharpe < 0 連續 3 窗口** — 過度擬合確認

---

## 🔧 部署步驟

### Paper Trading (建議至少 2 週)
1. 設定 `config/environments/paper.yaml`
2. 使用 `config/strategies.yaml` (已更新) 配置策略
3. 使用 `config/optimized_params.yaml` 最佳化參數
4. 確認所有風控模組正常運作
5. 驗證 Regime 偵測切換正確

### Live Trading
1. 確認 Paper Trading 結果符合回測預期 (偏差 < 30%)
2. 從 50% 資金開始 (半倉啟動)
3. 觀察 1 週，確認無異常
4. 逐步放大至 100% 資金
5. 設定自動監控 + 報警

### 緊急處理
- **全局暫停**: Portfolio DD > 20% 自動觸發
- **單策略暫停**: Sharpe < -1 持續 30 天
- **手動 Kill Switch**: `risk_limits.yaml` 中的 circuit breaker

---

## 📈 驗收標準 vs 目標

| 指標 | 目標 | Phase 1+2 結果 | 狀態 |
|------|------|----------------|------|
| 組合 Sharpe | > 0.8 | Top3 avg 1.48 | ✅ |
| 年化報酬 | > 25% | Top3 avg 48.7% | ✅ |
| MaxDD | < 20% | Top3 avg -14.5% | ✅ |
| WF OOS Sharpe | > 0.5 | 需實際數據驗證 | ⏳ |
| 策略間相關性 | < 0.4 | 需實際數據驗證 | ⏳ |
| Calmar | > 1.0 | Top3 avg 1.85 | ✅ |

---

## 📂 關鍵檔案

| 檔案 | 說明 |
|------|------|
| `config/strategies.yaml` | 策略配置 + 資金配置 + 環境路由 |
| `config/optimized_params.yaml` | 每策略 × 每幣種最佳參數 |
| `config/risk_limits.yaml` | 全局風控限制 |
| `backtest_tool/engine/regime.py` | 市場環境偵測器 |
| `backtest_tool/engine/risk_manager.py` | 風控模組 |
| `backtest_tool/engine/robustness.py` | 穩健性驗證工具 |
| `backtest_tool/engine/analytics.py` | 相關性 + 滾動 Sharpe |
| `backtest_tool/OPTIMIZATION_PLAN_v2.md` | 完整優化計畫 |
