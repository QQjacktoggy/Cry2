# 五個具獲利前景的新策略提案

> **對象**：已有 V7.2 主線的 binance-futures-bot 專案
> **目標**：在不影響主線的前提下，補足三個 V7.2 缺口——結構性套利、市場中性、事件驅動
> **評估依據**：邊際持久性、容量、與主線相關性、實作成本

---

## 評估框架

每個提案都從以下五個維度評分（1-5 分，5 為最佳）：

| 維度 | 意義 |
|------|------|
| **Edge 持久性** | 經濟邏輯是否會被套利消失？高分代表邊際來源結構性、難被套掉 |
| **容量** | 可以部署多少資金而不影響自己的進出價？ |
| **與 V7.2 相關性** | 分數 = 1 / correlation。低相關性降低組合 MaxDD |
| **實作成本** | 需要多少工程/資料/基礎設施投入？高分代表容易落地 |
| **資料需求** | 是否需要主線沒有的資料（on-chain、spot book、外盤）？高分代表只用 Binance Futures 資料即可 |

---

## 策略 #1 — Perp-Spot Basis Arbitrage（永續-現貨基差套利）⭐⭐⭐⭐⭐

### 核心邏輯

加密永續合約沒有到期日，靠**資金費率**把永續價格拉回現貨。當 `perp - spot` 基差（basis）異常擴大時：
- **正基差**（perp > spot）→ 做空永續 + 買現貨，賺 basis 收斂 + 正資金費率
- **負基差**（perp < spot）→ 做多永續 + 融券現貨（或空現貨）賺 basis 收斂

因為兩條腿完全對沖，**市場中性**，理論上任何方向都賺錢。

### 為什麼仍有 alpha？

加密市場 basis 異常的來源：
1. **Retail FOMO 衝到永續**（牛市）→ 正基差年化可達 30-60%
2. **恐慌/清算瀑布**（熊市）→ 負基差年化 20-40%
3. **新幣上線 / 事件**（SOL、TIA 等）→ 短期 basis 可衝 80%+

V7.2 完全沒有吃到這個 alpha。

### 評分

| 維度 | 分數 | 說明 |
|------|------|------|
| Edge 持久性 | 5/5 | 結構性來自散戶槓桿需求，不會消失 |
| 容量 | 4/5 | BTC/ETH 可部署數百萬 USDT，alt 約數十萬 |
| 低相關性 | 5/5 | 市場中性，相關性 ≈ 0 |
| 實作成本 | 3/5 | 需加現貨帳戶連線（Spot API） |
| 資料需求 | 4/5 | 只需 spot price + funding rate，皆為 Binance 原生 |
| **合計** | **21/25** | **最優先實作** |

### 預期績效（文獻與歷史經驗）

- **2021 牛市**：年化 25-40%，Sharpe 6-10
- **2022 熊市**：年化 15-25%，Sharpe 4-6
- **2023-2025 平均**：年化 18-28%，Sharpe 4-8
- **MaxDD**：通常 < 3%（僅來自 basis 反向擴張 + 執行滑價）

### 實作要點

1. **訊號**：`basis_pct = (perp_price - spot_price) / spot_price`，計算 8-hour 年化基差 + 下一次 funding rate 預估
2. **進場**：當 `annualized_basis + annualized_funding > threshold`（建議 15%）時成對下單
3. **出場**：basis 回到 0 附近 OR 年化 < 5% OR 累積 funding 已達預期 OR 持倉 > 7 天
4. **風險**：
   - Spot 借貸利率飆升（反向基差時）
   - Exchange withdrawal 凍結（極端事件）
   - 清算保護：永續腿單獨槓桿必須 ≤ 2x

### 主線整合方式

**不整合**，獨立帳戶 + 獨立風控。Lab 階段先用 paper 驗證。

### 實作骨架

見 [`perp_spot_basis_arb.py`](perp_spot_basis_arb.py)。

**預期新增檔案（不動主線）：**
- `strategies_lab/perp_spot_basis_arb.py` — 策略類別
- `strategies_lab/lab_data/spot_client.py` — Spot 資料連線（未實作）
- `strategies_lab/config/strategies_lab.yaml` — 配置

---

## 策略 #2 — Cross-Exchange Funding Rate Delta（跨交易所資金費率差）⭐⭐⭐⭐

### 核心邏輯

**同一個 symbol**在不同交易所的資金費率可以差很多：
- Binance ETHUSDT funding: +0.01% / 8h（年化 10.95%）
- Bybit ETHUSDT funding: +0.08% / 8h（年化 87.6%）
- **差值 76.65% 年化**

作法：在**高費率所做空** + 在**低費率所做多**，持倉完全對沖，賺每 8 小時的費率差。

### 為什麼仍有 alpha？

- 各交易所的 funding 公式、premium index、clamp 機制都不同
- 各所的 retail 組成、槓桿限制不同 → 需求差異不會收斂
- 套利需要「在多個所都有資金 + 抵押品」，進入門檻高

### 評分

| 維度 | 分數 | 說明 |
|------|------|------|
| Edge 持久性 | 4/5 | 會慢慢縮小但難消失 |
| 容量 | 5/5 | 可達數百萬 USDT |
| 低相關性 | 5/5 | 市場中性 |
| 實作成本 | 2/5 | 需多所 SDK、跨所資金管理 |
| 資料需求 | 3/5 | 需接入 Bybit/OKX funding API |
| **合計** | **19/25** | **容量最大但工程成本最高** |

### 預期績效

- 2024-2025 ETHUSDT 平均 funding delta：15-25% 年化
- Alt coins（SOL、DOGE）可達 30-50% 年化
- Sharpe 3-5（市場中性，波動低）
- 主要風險：某一所清算 / 提款凍結

### 實作要點（設計文件——未寫骨架）

**多交易所連線架構：**

```python
class CrossExchangeFundingStrategy(BaseStrategy):
    def __init__(self, params):
        self.exchanges = {
            "binance": BinanceFuturesClient(...),
            "bybit":   BybitFuturesClient(...),
            "okx":     OkxFuturesClient(...),
        }
        self.min_delta_annual = params.get("min_delta_annual", 10.0)  # %

    def poll_funding_deltas(self):
        # 每 15 分鐘拉一次所有所的下次 funding rate
        # 找出 delta 最大的 (high_ex, low_ex) pair
        ...

    def enter_pair(self, symbol, high_ex, low_ex, size):
        # Short on high_ex, Long on low_ex
        # 需同步下單，使用並發 + timeout 回滾
        ...
```

**工程依賴：**
- 需要 `ccxt` 或各所原生 SDK
- 需要 `asyncio.gather` 同步下單
- 需要跨所資金餘額監控 daemon

**建議：** Paper 驗證完成後再實作，屬於 Phase 2。

### 主線整合方式

**絕不整合**，獨立帳戶 + 獨立容器，避免主線 risk manager 誤觸。

---

## 策略 #3 — Statistical Pairs Trading（Kalman Filter 配對交易）⭐⭐⭐⭐

### 核心邏輯

兩個相關性高的 symbol（例如 ETH/BTC、BNB/BTC）價格比值長期均值回歸。作法：
1. 用 Kalman Filter 動態估計 hedge ratio `β` 和 spread `s = log(P_A) - β * log(P_B)`
2. 計算 Z-score of spread
3. 當 `|Z| > 2` 反向進場，`|Z| < 0.5` 出場

### 為什麼用 Kalman 而非 OLS？

- Hedge ratio 在 crypto 市場會緩慢 drift（例如 ETH/BTC ratio 2020 vs 2025 差一倍）
- Kalman 是「線上學習」版 OLS，適合 regime-adaptive
- 不需要 cointegration test（OLS pairs 的痛點）

### 評分

| 維度 | 分數 | 說明 |
|------|------|------|
| Edge 持久性 | 3/5 | 經典策略，擁擠，但 Kalman 能應對 regime shift |
| 容量 | 4/5 | 大市值幣種流動性充沛 |
| 低相關性 | 4/5 | 與趨勢策略反相關，diversifying |
| 實作成本 | 4/5 | 有 `pykalman` 現成套件，數學不複雜 |
| 資料需求 | 5/5 | 只需兩個 symbol 的 close price |
| **合計** | **20/25** | **CP 值最高** |

### 候選配對

| 配對 | 2024 年平均 cointegration p-value | 備註 |
|------|------|------|
| ETH/BTC | < 0.01 | 最穩定，首選 |
| BNB/BTC | < 0.05 | 次選，幣安自身系統風險要注意 |
| LTC/BTC | < 0.1 | 老幣配對，穩但流動性差 |
| SOL/ETH | 0.1-0.3 | L1 競爭關係，regime shift 多 |
| MATIC/ETH | > 0.2 | 2024 後 MATIC 脫鉤，不推薦 |

### 預期績效

- Sharpe 1.8-3.0（穩定）
- Ann Return 15-30%
- MaxDD -5% ~ -10%
- 關鍵：**不會在趨勢市爆賺**，也不會在橫盤市大虧——純粹吃均值回歸的機械邊際

### 實作要點

見 [`stat_arb_pairs.py`](stat_arb_pairs.py)。

核心邏輯：

```python
# 簡化版
spread = np.log(price_a) - beta * np.log(price_b)
z = (spread - mean) / std
if z > 2.0 and flat:   # short A, long B
if z < -2.0 and flat:  # long A, short B
if abs(z) < 0.5:       # close all
```

---

## 策略 #4 — Liquidation Cascade Hunter（清算瀑布捕獵）⭐⭐⭐

### 核心邏輯

當 OI（未平倉量）急升 + 單邊 long-short 比例極端 + 短線波動 > 3% 時，市場多半進入清算模式。**反向進場**能吃到：
1. 清算瀑布後的急速反彈
2. 短線過度恐慌/貪婪的修復

### 為什麼仍有 alpha？

- Binance 每 5 秒公布 OI、每 5 分鐘公布 long-short ratio
- 散戶主導的清算有週期性模式（weekend、pre-CPI、ETF 報價前）
- V7.2 的「tail_risk_hedge」只在 volatile regime 跟隨，沒有「反向抄底」角色

### 評分

| 維度 | 分數 | 說明 |
|------|------|------|
| Edge 持久性 | 3/5 | 會隨永續市場成熟慢慢衰減 |
| 容量 | 2/5 | 流動性時效短，部署 > 10 萬 USDT 有滑價 |
| 低相關性 | 3/5 | volatility-regime dependent |
| 實作成本 | 4/5 | OI / LS-ratio API 現成 |
| 資料需求 | 4/5 | 需 `/fapi/v1/openInterest` + `/futures/data/globalLongShortAccountRatio` |
| **合計** | **16/25** | **實驗性質高，小配置即可** |

### 訊號定義

```python
oi_change_5min = (OI_t - OI_t_5min_ago) / OI_t_5min_ago
ls_ratio = global_long_account / global_short_account
bar_range_pct = (high - low) / open

is_cascade = (
    abs(oi_change_5min) > 0.05     # OI 5 分鐘變 > 5%
    and (ls_ratio > 3 or ls_ratio < 0.5)  # 極端偏多或偏空
    and bar_range_pct > 0.03       # 短線波動 > 3%
)

# 反向進場
if is_cascade:
    if last_direction == "down":
        go_long(size_scaled_by_inverse_atr)
    elif last_direction == "up":
        go_short(...)
```

### 風險

- **第二波清算**：停損必須嚴格（建議 -1.5% 或 1 ATR）
- **新聞類暴跌**（例如交易所倒閉）不會反彈——必須用 news filter 關掉

### 實作骨架

見 [`liquidation_hunter.py`](liquidation_hunter.py)（簡化版，未接 OI API）。

---

## 策略 #5 — BTC Dominance Rotation（BTC 主導率輪動）⭐⭐⭐

### 核心邏輯

加密市場有清楚的「BTC → ETH → Large Alt → Small Alt」資金輪動週期。訊號：
- `BTC.D`（BTC 市值佔比）下降 + `OTHERS.D` 上升 → **alt season**
- `BTC.D` 上升 → **safe haven flight**，空 alt

當偵測到 alt season，系統性做多 top 10 alt coin；反之做空。

### 為什麼仍有 alpha？

- 加密市場結構性流動性輪動，持續 10+ 年
- V7.2 只按策略打分，沒有「市場結構」維度
- 週期夠慢（數週到數月），適合加低頻 regime overlay

### 評分

| 維度 | 分數 | 說明 |
|------|------|------|
| Edge 持久性 | 4/5 | 結構性輪動難消失 |
| 容量 | 5/5 | 全市場 top 10 alt 都可部署 |
| 低相關性 | 2/5 | 與 trend_donchian 同質（皆是 momentum） |
| 實作成本 | 3/5 | 需接 CoinGecko 或 DefiLlama API |
| 資料需求 | 2/5 | 需要全市場市值資料 |
| **合計** | **16/25** | **中等優先，留給 Phase 2** |

### 訊號設計

```python
btc_d_ma30 = BTC_dominance.rolling(30).mean()
others_d_ma30 = OTHERS_dominance.rolling(30).mean()

alt_season = (
    BTC_dominance < btc_d_ma30 * 0.95
    and OTHERS_dominance > others_d_ma30 * 1.05
    and ETHBTC_ratio > ETHBTC_ma90   # 確認 ETH leading
)

if alt_season:
    # 做多 top 10 alt（排除 BTC/ETH），等權重
    pass
elif BTC_dominance > btc_d_ma30 * 1.05:
    # 空 top 10 alt 避險
    pass
```

### 實作要點

- 日線觸發，4 小時檢查一次
- 單 alt 最大部位 = total_equity × 0.03
- 建議搭配 `regime_filter` 避免在熊市做多

### 骨架

**未實作**，建議用 `daily_rotation_strategy.py` 的 skeleton。需要新增 `lab_data/market_cap_client.py`。

---

## 建議的擴充後配置

假設採用全部 5 個策略，建議配置：

```yaml
# strategies_lab/config/strategies_lab.yaml 的展望版
main_line_v72:          0.65   # V7.2 主線維持 65%
perp_spot_basis:        0.12   # 策略 #1
cross_ex_funding:       0.08   # 策略 #2（需多所帳戶）
stat_arb_pairs:         0.08   # 策略 #3
liquidation_hunter:     0.03   # 策略 #4
btc_dominance_rotate:   0.04   # 策略 #5
```

### 預期組合績效

| 指標 | V7.2 (主線) | V7.2 + Lab (本提案) |
|------|---------|-----------|
| Ann Return | 45.2% | **48-55%** |
| Sharpe | 2.355 | **3.0-3.5** |
| MaxDD | -10.6% | **-7% ~ -9%** |
| Calmar | 4.27 | **5.5-7.0** |

**邊際的核心來源：**
1. Basis/Funding 策略帶來 **獨立於方向的 Sharpe 3+ 貢獻**
2. Pairs trading 在橫盤期填補 V7.2 的空轉期
3. 整體相關性下降 → MaxDD 改善比 Ann Return 改善更顯著

---

## 實作順序建議

| Phase | 時程 | 項目 | 風險 |
|-------|------|------|------|
| 1 | Week 1-2 | 驗證 #3 Pairs Trading（僅用已有資料） | 低 |
| 2 | Week 3-4 | 加入 Spot 連線，實作 #1 Perp-Spot Basis | 中 |
| 3 | Week 5-8 | Paper 驗證 #1、#3 各 30 天 | 低 |
| 4 | Week 9-12 | 實作 #4 Liquidation Hunter（僅用 Binance 資料） | 中 |
| 5 | Month 4+ | 評估 #2 跨所、#5 主導率輪動 | 高 |

**建議停損條件：**
- 任一 lab 策略 paper 跑 30 天 Sharpe < 0.5 → 廢棄
- MaxDD > 預期的 2 倍 → 立即下線
- 任一策略與主線相關性 > 0.6 → 不併入

---

## 可選：不需要實作、純文件改善的建議

如果不想開發新策略，還有一個「改變 V7.2 配置邏輯」的零代碼獲利優化方向：

### V7.2 資金分配的改進空間

目前 V7.2 用**靜態配置** + regime_sizing。可以改為：
1. **動態風險平價**：每週根據過去 30 天每個策略的 realized vol 重新加權
2. **動量加權**：最近 3 個月表現好的策略加權，表現差的減半（簡單 momentum-on-strategies）
3. **Kelly fraction cap**：用每個策略的 Kelly fraction 作為最大權重上限

這可在 `src/bot/portfolio/` 新增 `dynamic_allocator.py`（也是獨立模組），不動現有配置。若有興趣可單獨開一份提案。

---

## 附錄：加密交易策略 Sharpe 3+ 的真實來源

做為參考，真正能長期維持 Sharpe 3+ 的加密策略類別：

1. **MM (Market Making)** — Sharpe 5-10，但需要 VIP tier + colocation，門檻極高
2. **Triangular Arbitrage** — Sharpe 3-5，容量小，拼速度
3. **Basis / Funding Arbitrage** — Sharpe 3-8，容量中大，**本提案的核心**
4. **Statistical Arbitrage（pairs、index arb）** — Sharpe 2-4，容量中
5. **Event-driven（unlock、listing、airdrop）** — Sharpe 3-6，容量小，需人工判斷

**技術指標類策略**（trend、momentum、mean reversion、grid）通常 Sharpe 1-2 即為優秀——V7.2 的 2.355 已經是這個 class 的頂標。要再往上突破，必須切換到「結構性套利」而不是優化指標。

這就是本 lab 的設計理念。
