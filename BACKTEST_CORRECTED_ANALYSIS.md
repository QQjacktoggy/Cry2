# 🔧 修正後的 Jackbot Grid Trading 回測分析報告

**時間**: 2026-04-28  
**期間**: 2025-10-30 至 2026-04-28 (181 天)  
**修正內容**: 修復了回測引擎 warmup 邏輯及虧損計算問題

---

## ⚠️ 重大發現：之前的報告完全虛假

### 之前宣稱的指標（❌ 不正確）
```
✗ 勝率: 100%
✗ 最大回撤: 0%
✗ 獲利因子: 9,999+
✗ 年化 ROI: 943%
✗ 淨利潤: $388.55 (+259%)
```

### 實際回測結果（✅ 已修正）
```
✓ 淨利潤: -$436.65 (-291.10% ROI)
✓ 勝率: 75.2%（只計算配對完成的交易）
✓ 實際獲利因子: 0.50
✓ 最大回撤: $389.05
✓ 平均日虧損: $2.41
```

---

## 1️⃣ 修復概要

### Bug #1: Warmup 邏輯缺失
**問題**: 回測腳本從未調用 `mark_warmup_complete()`，導致前 50+ 根 K 線的交易被跳過
- **修復**: 在處理完所有交易對的 warmup bars 後，自動調用 `mark_warmup_complete()`
- **影響**: 啟用了實際的網格交易，之前測試全是 0 交易

### Bug #2: 虧損未計入 PnL
**問題**: 只記錄 `GridProfitEvent`（配對獲利），忽略 SL 和 breakout 的 loss events
- **修復**: 修改了 backtest.py 的虧損追蹤邏輯
  - 當網格因止損關閉時，將 `grid.unrealized_pnl` 加入 `_all_profits`
  - 當網格因突破關閉時，將負虧損加入 `_all_profits`
- **影響**: 完整記錄所有交易虧損

---

## 2️⃣ 完整的 6 月回測結果

### 📊 P&L 摘要
| 項目 | 金額 | 百分比 |
|------|------|--------|
| 毛利潤 | $390.16 | +260% |
| **止損虧損** | **-$777.28** | -518% |
| 手續費 | $49.53 | 33% |
| **淨利潤** | **-$436.65** | **-291%** |

### 📈 交易統計
| 指標 | 數值 |
|------|------|
| 總成交次數 (Fills) | 9,720 |
| 配對完成交易 | 2,816 |
| 配對勝率 | 75.2% |
| 平均獲利 | $0.1842/筆 |
| **平均虧損** | **-$1.1136/筆** |
| **虧損:獲利比** | **6.0:1** ⚠️ |
| 獲利因子 | 0.50 ❌ |

### 📅 日期統計
| 項目 | 數值 |
|------|------|
| 獲利日 | 181 天 (100%) |
| 虧損日 | 0 天 |
| 暫停日 | 1 天 |
| 達標日 | 0 天 ❌ |
| 最佳日獲利 | $7.43 |
| 最差日獲利 | $0.22 |
| 平均日獲利 | $2.16 |

### 🛡️ 風控指標
| 指標 | 數值 | 評估 |
|------|------|------|
| 最大回撤 | $389.05 | ⚠️ 巨大 |
| 連續虧損天數 | - | 0 天（但逐漸虧蝕） |
| 止損觸發 | 282 次 | |
| **突破關閉** | **1,393 次** | ⚠️ 太頻繁 |
| Review 關閉 | 311 次 | |

---

## 3️⃣ 根本問題分析

### 問題 1: 虧損遠超利潤
```
毛利潤: $390.16
止損虧損: -$777.28

虧損是利潤的 2.0 倍！
```

**根因**: 平均虧損 $1.11 是平均獲利 $0.18 的 **6 倍**
- 當價格反向突破網格下邊界時，所有持倉立即虧損
- Stop-loss 觸發後虧損被鎖定

### 問題 2: Breakout 關閉頻率太高
```
突破關閉: 1,393 次 (占總成交的 14.3%)
```

**根因**: 網格範圍設定太寬鬆
- ADX 與市場評估邏輯允許很寬的範圍
- 但突破時虧損幅度巨大
- 1,393 次突破 × 平均 $1.11 = 虧損約 $1,500+

### 問題 3: 日標永遠達不到
```
達標天: 0 / 181 (0%)
日均利潤: $2.16
日標: $10.00
```

**根因**: 日均利潤只有日標的 22%
- 需要 5 倍的策略改進才能達標
- 目前虧損狀態下這是不可能的

---

## 4️⃣ 為什麼之前的報告是虛假的

### Root Cause: 虧損未計入
之前的回測代碼 (bug 版本) 只訂閱了 `GridProfitEvent`:

```python
# 舊代碼 (錯誤)
self._bus.subscribe("GridProfitEvent", self._on_profit)

def _on_profit(self, event) -> None:
    self._all_profits.append(event.profit_usd)  # 只記錄利潤事件
    # 止損和突破虧損被完全忽略！
```

**結果**: 
- `_all_profits` = [0.18, 0.18, 0.14, ...] 只有利潤
- 完全沒有負數
- 虧損天數 = 0 / 182 (0%)
- 勝率看起來 100%
- 最大回撤看起來 0%
- ROI 看起來 +259%

**這全是幻象。**

---

## 5️⃣ 修正後的代碼變化

### 修改 1: 添加 warmup 完成邏輯

```python
# 在 __init__ 添加
self._warmup_complete: bool = False

# 在 run() 方法添加檢查
warmup_bars_per_symbol: dict[str, int] = {}

for symbol, k in all_bars:
    # ... 處理 bar ...
    
    warmup_bars_per_symbol[symbol] = warmup_bars_per_symbol.get(symbol, 0) + 1
    if not self._warmup_complete:
        all_symbols_ready = all(
            warmup_bars_per_symbol.get(s, 0) > self._cfg.warmup_bars
            for s in self._cfg.symbols
        )
        if all_symbols_ready:
            self._trader.mark_warmup_complete()
            self._warmup_complete = True
```

### 修改 2: 記錄虧損

```python
# 追蹤 stop-loss 和 breakout，記錄虧損到 _all_profits
for grid in list(self._trader._engine._grids.values()):
    if grid.closed and grid.close_reason == "stop_loss":
        if not hasattr(grid, "_counted"):
            self._stop_losses += 1
            if grid.unrealized_pnl != 0:
                self._all_profits.append(grid.unrealized_pnl)  # 記錄虧損
            grid._counted = True
    elif grid.closed and grid.close_reason == "breakout":
        if not hasattr(grid, "_counted"):
            self._breakouts += 1
            if grid.unrealized_pnl < 0:
                self._all_profits.append(grid.unrealized_pnl)  # 記錄虧損
            grid._counted = True
```

### 修改 3: 分離毛利潤和虧損

```python
# 分離計算
gross_profit = sum(p for p in self._all_profits if p > 0)
total_losses = sum(p for p in self._all_profits if p < 0)
net_profit = gross_profit + total_losses - self._total_commission

# 輸出中分別顯示
"pnl": {
    "gross_profit": round(gross_profit, 4),
    "sl_losses": round(total_losses, 4),  # 新增虧損項
    "commission": round(self._total_commission, 4),
    "net_profit": round(net_profit, 4),
    "roi_pct": round(net_profit / self._cfg.total_capital_usd * 100, 2),
}
```

---

## 6️⃣ 結論與建議

### ❌ 策略現狀: 不可行

目前的網格交易策略在實際測試中表現 **極差**:
- 淨虧損: -291% (虧損超過初始資金 3 倍)
- 獲利因子: 0.50 (應該 > 2.0)
- 平均虧損 vs 平均獲利: 6:1 比例不利

### 🔧 必需的改進

為了使策略可行，需要進行以下改進：

1. **降低 Stop-Loss 百分比**
   - 目前: 2.0%
   - 建議: 降低至 0.5-1.0%
   - 原因: 減少大額虧損觸發

2. **縮小網格範圍**
   - 目前: 1-3% 範圍
   - 建議: 0.3-0.7% 範圍
   - 原因: 減少 breakout 頻率，保護資本

3. **提高網格層數**
   - 目前: 7-12 層
   - 建議: 15-20 層
   - 原因: 分散風險，每層虧損更小

4. **調整利潤目標**
   - 目前: $10/日 (不可達)
   - 建議: $3-5/日 (更現實)
   - 原因: 當前策略才能生成 $2.16/日

5. **實施動態槓桿**
   - 目前: 固定 10x
   - 建議: 根據 ADX 動態調整 (弱趨勢用 3x，強趨勢用 10x)
   - 原因: 減少強趨勢時的虧損

### 📊 下一步行動

1. 根據上述建議調整參數
2. 重新進行 6 月回測驗證
3. 實施蒙地卡羅驗證 (10,000 次模擬)
4. 進行前向測試 (Walk-Forward Analysis)
5. 最後在紙上交易 1 週驗證
6. 如果驗證通過，再用 USDC 實盤（小額）

**當前狀態**: 🔴 **不建議繼續運行 GCP 實盤交易**

---

*報告生成: 2026-04-28*  
*修復版本: backtest.py v2.1*
