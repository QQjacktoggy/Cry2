# Jackbot_V1 — 幣安合約網格日內交易機器人

自動在幣安合約市場 (USDT-M Futures) 建立網格對策，靠格間價差快速累積利潤。

## 核心特性

- **自動市場評估** — ADX/BB/ATR 分析市場狀態，自動決定網格方向 (做多/做空/中性)
- **動態槓桿** — 根據波動率自動計算 5x~20x 逐倉槓桿
- **日內目標管理** — 累計利潤達標 (10 USDT) 後自動切換保守對策
- **風控保護** — 日虧上限、BTC 暴跌偵測、最大網格數限制
- **Telegram 通知** — 即時推送格間利潤、模式切換、風控警報

## 快速開始

```bash
# 1. 安裝依賴
pip install -r requirements.txt

# 2. 設定環境變數
cp .env.example .env
# 編輯 .env 填入 API Key

# 3. 驗證接線
python scripts/run.py --dry-run

# 4. 開始交易
python scripts/run.py
```

## 設定檔

所有參數集中在 `config/settings.yaml`：

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `total_capital_usd` | 150 | 總資金 (USDT) |
| `max_leverage` | 20 | 最高逐倉槓桿 |
| `min_leverage` | 5 | 最低逐倉槓桿 |
| `daily_profit_target_usd` | 10 | 日標 (達成後切換保守) |
| `daily_loss_limit_usd` | 15 | 日虧上限 (觸發暫停) |
| `timeframe` | 5m | K 線周期 |
| `symbols` | BTCUSDT, ETHUSDT | 交易對 |

## 架構

```
DayTrader (日內編排器)
  ├── MarketAssessor (市場評估)  → ADX/BB/ATR → 方向 & 區間
  ├── GridEngine (網格引擎)      → 格點管理 / 掛單 / 補單 / 利潤追蹤
  └── RiskManager (風控)         → 日虧 / 暴跌 / 持倉限制
```

## 測試

```bash
python -m pytest tests/ -v
```
