# strategies_lab — 獲利前景擴展策略實驗室

> **目的**：在不影響 V7.2 主線配置的前提下，提出並原型化 5 個「更有獲利前景」的新策略。
> **原則**：所有新策略都獨立於 `src/bot/strategy/`，不動 `config/strategies.yaml`。需顯式呼叫 `register_lab.py` 才會載入。

---

## 為什麼要擴充？V7.2 的獲利天花板分析

目前主線（V7.2）：

| 指標 | 值 |
|------|----|
| Sharpe | 2.355 |
| Ann Return | 45.2% |
| MaxDD | -10.6% |
| Calmar | 4.27 |

**V7.2 的結構限制：**

1. **全部是方向性策略**（trend / momentum / grid / breakout）——Alpha 來源集中在「行情判斷」，相關性高
2. **沒有利用加密獨有的結構性套利**（funding、basis、跨所）——這些是 Sharpe 3+ 的真正來源
3. **缺少市場中性成分**——當 BTC 橫盤且 ADX 低時，V7.2 會空轉
4. **單交易所依賴**——只用 Binance，錯過了跨所價差/資金費率差

**擴充方向：**

- 加入「**非方向性**」策略（basis、funding、pairs）提升組合 Sharpe
- 加入「**結構性套利**」策略抓真實 alpha，而非擁擠的技術指標
- 加入「**事件驅動**」策略（清算瀑布）利用市場失衡

---

## 五個提案總覽

完整分析請見 [proposals.md](proposals.md)。

| # | 策略 | 預期 Sharpe | 容量 | 實作難度 | 與 V7.2 相關性 | 建議配置 |
|---|------|------|------|----------|-----------|----------|
| 1 | **Perp-Spot Basis Arbitrage** | 4-8 | 中 | ★★★ | 極低 (~0.1) | 10-15% |
| 2 | **Cross-Exchange Funding Delta** | 3-5 | 高 | ★★★★ | 極低 (~0.05) | 8-12% |
| 3 | **Statistical Pairs (Kalman Filter)** | 1.8-3 | 中 | ★★★ | 低 (~0.2) | 8-10% |
| 4 | **Liquidation Cascade Hunter** | 1.5-3 | 低-中 | ★★ | 中 (~0.4) | 3-5% |
| 5 | **BTC Dominance Rotation** | 2-4 週期性 | 高 | ★★ | 中 (~0.5) | 5-8% |

**建議整體：** 主線 V7.2 保留 65-70%，lab 策略合計 30-35%。預期組合 Sharpe 可從 2.355 提升到 **3.0-3.5**，MaxDD 下降到 **-8%** 以內（因為相關性降低）。

---

## 目錄結構

```
strategies_lab/
├── README.md                    # 本檔，總覽
├── proposals.md                 # 完整策略提案分析（最重要）
├── register_lab.py              # 獨立 registry（不動主線 registry）
├── config/
│   └── strategies_lab.yaml      # 獨立配置（不動主線 strategies.yaml）
├── perp_spot_basis_arb.py       # 策略 #1 骨架
├── stat_arb_pairs.py            # 策略 #3 骨架
├── liquidation_hunter.py        # 策略 #4 骨架
└── tests/                       # 策略單測（待補）
```

**未實作骨架：**
- `#2 Cross-Exchange Funding Delta` — 需要多交易所連線層，超出單進程範圍。proposals.md 提供設計文件。
- `#5 BTC Dominance Rotation` — 需接 CoinGecko 或 DefiLlama 的總市值資料，proposals.md 有實作指引。

---

## 如何在不影響主線的情況下試驗

**方法 A — 回測**：

```python
# 在一個 scratch script 或 jupyter notebook 中：
import sys
sys.path.insert(0, "src")

from bot.strategy.registry import StrategyRegistry, register_default_strategies
from strategies_lab.register_lab import register_lab_strategies

register_default_strategies()   # V7.2 主線
register_lab_strategies()       # 加入 lab 策略（可選）

# 用 strategies_lab/config/strategies_lab.yaml 跑回測，不會動主線 config
```

**V8 phase-1 runner（已實作）**：

```powershell
python scripts/run_v8_backtest.py --profile baseline
python scripts/run_v8_backtest.py --profile v8a
python scripts/run_v8_backtest.py --profile v8b
python scripts/run_v8_backtest.py --profile v8c
```

這條 runner 會以目前 **V7.4** 當 control baseline，並 opt-in 疊加 phase-1 lab sleeves。

**方法 B — 直接排除**：
不呼叫 `register_lab_strategies()`，系統行為完全等同 V7.2，零風險。

---

## 風險提醒

1. **Basis/Funding 策略需要 spot 帳戶**——目前 bot 只做合約。實作前請先確認 exchange/execution 層是否需擴充。
2. **跨所策略需要多帳戶同步**——資金管理、風控邏輯都更複雜。建議先在 paper 驗證至少 30 天。
3. **Liquidation Hunter 是反向進場**——在流動性極差時可能被「清算二次波」打到，務必有嚴格的 stop。
4. **統計套利需要 cointegration 檢驗**——ETH/BTC ratio 穩定，但 SOL/BTC、DOGE/BTC 等 regime shift 頻繁，不可盲用。
5. **本 lab 所有策略皆未經回測驗證**——預期 Sharpe 是基於歷史文獻與加密市場觀察，實際落地前必須先做完整回測。

---

## 下一步建議

1. 先讀 [proposals.md](proposals.md) 確認方向是否符合你的資源與交易目標
2. 從策略 #1（Perp-Spot Basis）開始——邊際效應最明確、容量最大、與主線相關性最低
3. 用 `scripts/run_v8_backtest.py --profile ...` 先比對 baseline / V8-A / V8-B / V8-C，再決定是否進入更深的參數 sweep
4. Paper trading 驗證 ≥ 4 週後再討論是否併入主線
