# 📋 部署建議書 (Deployment Recommendation)

> 版本: 1.0 | 基於: 回測優化計畫 OPTIMIZATION_PLAN.md

---

## 1. 推薦策略組合

| 策略 | 幣種 | 時間框架 | 配置比例 | 說明 |
|------|------|---------|---------|------|
| grid_futures | ETH/SOL | 4h | 40% | 穩健正收益，適合盤整市 |
| trend_donchian | BTC | 4h | 25% | 趨勢市捕捉，ADX 過濾 |
| mean_reversion_bb | BTC/ETH | 1h | 20% | 超買超賣回歸 |
| funding_arb | SOL | 8h | 15% | funding 套利 |

**最佳參數**: 詳見 `config/optimized_params.yaml`

---

## 2. 風控設定

### 組合級止損
```python
PortfolioStopLoss(
    max_drawdown_pct=20.0,  # 組合回撤超過 20% → 停止交易
    cooldown_bars=48,        # 冷卻 48×4h = 8 天
)
```

### 連續虧損保護
```python
ConsecutiveLossGuard(
    max_consecutive_losses=5,  # 5 連敗暫停
    pause_bars=24,              # 暫停 24 根 bar
)
```

### 波動率自適應槓桿
```python
VolatilityLeverageAdapter(
    max_leverage=3,             # 低波動允許最高 3x
    min_leverage=1,             # 高波動降至 1x
    high_vol_percentile=80.0,   # ATR > 80th pct → 最低槓桿
)
```

### 倉位管理
- 模式: `fixed_fraction` (每次使用 50% 可用資金)
- 最大單策略曝險: 不超過總資金 80%

---

## 3. 市場環境適應

```python
# 根據市場環境選擇策略
regime_map = {
    "trending_up":   ["trend_donchian"],
    "trending_down": ["trend_donchian", "funding_arb"],
    "ranging":       ["grid_futures", "mean_reversion_bb"],
    "volatile":      [],   # 高波動暫停所有開倉
}
```

---

## 4. 監控閾值

| 指標 | 警告閾值 | 停機閾值 |
|------|---------|---------|
| 日回撤 | > 5% | > 10% |
| 週回撤 | > 12% | > 20% |
| 月回撤 | > 20% | > 30% |
| 連續虧損 | > 3 次 | > 5 次 |
| Sharpe (30日) | < 0 | < -0.5 |
| 交易執行延遲 | > 500ms | > 2000ms |
| API 連線失敗 | 3 次/小時 | 10 次/小時 |

---

## 5. 維護計畫

### 定期任務
- **每週**: 檢查 30 日滾動 Sharpe，確認策略有效性
- **每月**: 重新執行參數掃描 (前 30 天資料)
- **每季**: 完整 Walk-Forward 驗證
- **每年**: 重新評估策略邏輯，可能退役/更新

### 參數更新觸發條件
- 連續 2 週 Sharpe < 0
- 最大回撤超出歷史 1.5 倍
- 市場結構性改變 (e.g., 監管變化、交易所費率調整)

---

## 6. 最低資金需求

| 策略 | 最低資金 (USDT) | 建議資金 |
|------|----------------|---------|
| grid_futures | 100 | 500+ |
| trend_donchian | 200 | 1000+ |
| mean_reversion_bb | 150 | 500+ |
| funding_arb | 100 | 300+ |
| **組合全部** | **500** | **2000+** |

---

## 7. 穩健性信心評分

基於 Walk-Forward 和 Monte Carlo 分析:

| 策略 | 信心評分 (0-10) | Overfit 風險 | 建議 |
|------|---------------|------------|------|
| grid_futures | 7/10 | 低 | ✅ 可上線 |
| mean_reversion_bb | 5/10 | 中 | ⚠️ 需監控 |
| trend_donchian | 4/10 | 中 | ⚠️ 僅 BTC/ETH |
| funding_arb | 3/10 | 高 (樣本少) | ❌ 需更多驗證 |

---

## 8. 上線前最終 Checklist

- [ ] Walk-forward OOS Sharpe > 0.3
- [ ] Monte Carlo 95th pct 最大回撤 < 50%
- [ ] 組合最大回撤 < 30%
- [ ] 年化收益 > 5%
- [ ] API 連線測試通過 (testnet)
- [ ] 風控觸發測試通過
- [ ] 監控告警設定完成
- [ ] 緊急停機程序演練完成
