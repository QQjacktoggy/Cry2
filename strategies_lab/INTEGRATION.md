# strategies_lab ← → V7.2 主線 整合指南

> **對象**：未來接手這個 repo 的 AI agent（或人類工程師）
> **前提**：你已經讀過 `README.md` 和 `proposals.md`。本文是「怎麼把 lab 策略接到主線」的可執行手冊。
> **鐵律**：**永遠不要直接修改 `config/strategies.yaml` 或 `src/bot/strategy/registry.py`**。整合動作全部走獨立入口 + 合併層。

---

## 0. 你要先搞清楚的三件事

1. **主線 V7.2 已經是生產配置**（Sharpe 2.355 / Ann 45.2% / MaxDD -10.6%）。它跑在 `config/strategies.yaml`、被 `run_live.py` / `run_paper.py` / `run_backtest.py` 直接吃。
2. **Lab 策略預設完全隔離**——不呼叫 `register_lab_strategies()` 就不存在。這是特性不是 bug。
3. **Lab 的五個策略中只有兩個可以「立刻」併入**：
   - ✅ `lab_stat_arb_pairs` — 只用 Binance futures price，立即可用
   - ✅ `lab_liquidation_hunter` — 只用 Binance bar data，立即可用
   - ⚠️ `lab_perp_spot_basis` — **需要先擴充 exchange 層接 spot API**
   - ⚠️ `lab_cross_exchange_funding` — **需要 ccxt 或多所 SDK**
   - ⚠️ `lab_btc_dominance_rotation` — **需要接市值資料源（CoinGecko/DefiLlama）**

**如果你只有 1 個 session 的時間，只做前兩個就好。**

---

## 1. 決策樹（給 agent 直接跟著走）

```
[start]
  │
  ├─ 使用者要求「把 lab 接上主線」？
  │    │
  │    ├─ yes → 跳到 §2 Phase 1（最穩妥、最快見效）
  │    │
  │    └─ no ─┐
  │          ▼
  ├─ 使用者要求「評估要不要加某策略」？
  │    │
  │    └─ yes → 跳到 §4 評估框架 + 跑 paper 30 天
  │
  ├─ 使用者要求「實作 spot/cross-exchange 層」？
  │    │
  │    └─ yes → 跳到 §5 Phase 2（大工程，請確認 scope）
  │
  └─ 使用者只是問意見？
       │
       └─ 引用 `proposals.md` 的評分表，不要寫 code
```

---

## 2. Phase 1 — 把「立即可用」的兩個策略整合進主線回測（推薦起手式）

**目標產出：** 讓 `run_backtest.py` 能吃到 `lab_stat_arb_pairs` + `lab_liquidation_hunter`，但主線原本的 4-16 個策略行為**零改變**。

**目前 repo 狀態（已落地）**：
- 已有 `strategies_lab/integration.py`，可組裝 `baseline` / `v8a` / `v8b` / `v8c`
- 已有 `scripts/run_v8_backtest.py`，直接用 **V7.4 control baseline** 疊加 phase-1 lab sleeves
- `lab_stat_arb_pairs` 已補成雙腿訊號；`lab_liquidation_hunter` 仍受限於 15m parquet 是否存在

**DoD（Definition of Done）：**
- [ ] 可以跑 `python scripts/run_backtest.py --lab` 啟用 lab，不加 flag 則保持 V7.2 行為
- [ ] 主線回測 Sharpe / Return / MaxDD 數值在不加 flag 時**完全一致**（逐 penny 比對）
- [ ] lab 策略的訊號有被事件總線接收、產生 Trade、寫入 journal
- [ ] `pytest tests/ strategies_lab/tests/` 全部通過

### 步驟 1：建立合併層（不動主線 config）

建立 `strategies_lab/integration.py`：

```python
"""Integration layer — merges V7.2 main-line config with lab config.

Do NOT modify config/strategies.yaml directly. Always use merge_with_lab().
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml


def load_merged_config(
    main_config_path: Path,
    enable_lab: bool = False,
    lab_config_path: Path | None = None,
) -> dict[str, Any]:
    """Return merged config dict.

    When enable_lab=False, returns main config untouched.
    When enable_lab=True, overlays strategies_lab entries without replacing main.
    """
    with open(main_config_path) as f:
        cfg = yaml.safe_load(f)

    if not enable_lab:
        return cfg

    lab_path = lab_config_path or (Path(__file__).parent / "config" / "strategies_lab.yaml")
    with open(lab_path) as f:
        lab_cfg = yaml.safe_load(f)

    # Merge strategies dict — lab keys are prefixed with "lab_" so no collision
    cfg.setdefault("strategies", {}).update(lab_cfg.get("strategies", {}))
    # Append lab capital_allocation entries
    cfg.setdefault("capital_allocation", {}).update(
        lab_cfg.get("capital_allocation_lab", {})
    )
    return cfg
```

### 步驟 2：在 `scripts/run_backtest.py` 加一個 `--lab` flag（唯一對主線的觸碰，且純加法）

**這是允許的修改**（主線 run_backtest 加一個 opt-in flag），因為它不改 default 行為。如果使用者拒絕，改用方法 B（獨立 runner）。

```python
# scripts/run_backtest.py 的 argparse 處加入：
parser.add_argument("--lab", action="store_true",
                    help="Enable strategies_lab opt-in strategies")

# 在原本載入 config 的地方改為：
if args.lab:
    from strategies_lab.integration import load_merged_config
    from strategies_lab.register_lab import register_lab_strategies
    register_lab_strategies()
    config = load_merged_config(Path(args.config), enable_lab=True)
else:
    config = load_main_config(args.config)  # 原邏輯
```

**方法 B（如果不能碰 scripts/）：** 建立 `strategies_lab/examples/run_lab_backtest.py`，複製 `run_backtest.py` 的邏輯並加入 lab hook。缺點是要維護兩份 runner。

### 步驟 3：驗證主線零回歸

```bash
# A. 不加 flag 跑一次，記錄結果
python scripts/run_backtest.py --start 2024-01-01 --end 2024-06-30 > /tmp/baseline.log

# B. 加 flag 跑一次（但把 lab 策略都 disabled）
python scripts/run_backtest.py --start 2024-01-01 --end 2024-06-30 --lab > /tmp/with_lab.log

# C. 比對 — 除了啟用的兩個 lab 策略產生額外訊號外，主線數字必須完全一致
diff <(grep "main_line_" /tmp/baseline.log) <(grep "main_line_" /tmp/with_lab.log)
```

### 步驟 4：Risk 協調

Lab 策略**必須共用主線的 `RiskManager`**（避免雙重下單突破風控上限）。具體做法：

- ✅ Lab 策略產生的 `SignalEvent` 走同一個 event bus
- ✅ 同一個 `RiskManager.validate_signal()` 檢查槓桿、單日虧損、部位大小
- ❌ **不要**給 lab 建另一個 RiskManager 實例——這會導致兩邊各自看到自己的 equity，突破真實總風險上限

**特例：** `strategies_lab/config/strategies_lab.yaml` 的 `lab_risk_limits` 區塊是「**更嚴格**的限制」，用法：在 `RiskManager` 裡對 `strategy_name.startswith("lab_")` 的訊號套用更嚴的 threshold。

### 步驟 5：Journal / Dashboard 整合

- Lab 訊號會自然流進 `TradeJournal`（因為共用 event bus）
- Dashboard 無需改動——lab 策略在 `strategy_name` 欄會出現 `lab_*`，使用者可按前綴篩選
- **唯一要加的**：在 `src/bot/monitoring/dashboard/app.py` 加一個 checkbox「只顯示 lab 策略」或「排除 lab 策略」。**如果不能動主線，把這個放到 Phase 3。**

---

## 3. Phase 1 的資金配置建議

假設使用者一開始只想用 **最小風險驗證**：

```yaml
# 在 strategies_lab/config/strategies_lab.yaml 的 capital_allocation_lab 調整：
capital_allocation_lab:
  lab_stat_arb_pairs:     0.05   # 5% 小額驗證
  lab_liquidation_hunter: 0.02   # 2% 極小額
  # 其他 3 個暫 disabled
```

**主線維持 93%，lab 只佔 7%**。這是「paper 驗證用」配置，30 天後 Sharpe / MaxDD 符合預期再討論放大。

### 漸進式放大表

| 階段 | 時機 | lab_stat_arb | lab_liq_hunter | 主線 |
|------|------|--------------|----------------|------|
| Week 1-4 | Paper 首輪 | 5% | 2% | 93% |
| Week 5-8 | Paper 延長 / 小額實盤 | 7% | 3% | 90% |
| Month 3+ | 完整整合 | 8% | 3% | 89% |
| Month 6+ | 加 Phase 2 策略 | 8% | 3% | 65-75% |

**停損條件（自動觸發 disable）：**
- 任一 lab 策略 30 天 Sharpe < 0.5 → 立刻停用
- 任一 lab 策略 MaxDD > 5% → 立刻停用
- lab 策略與主線相關性 > 0.6 → 不符合分散化初衷，停用

---

## 4. 評估某個 lab 策略要不要進主線的框架

當使用者問「XX 策略可以開了嗎？」，用這張表回答：

| 項目 | 通過標準 | 指令 |
|------|----------|------|
| Paper 跑滿 30 天 | Trade 數 > 20 | 查 `journal` DB |
| Sharpe | > 1.5 | `python scripts/analyze_paper.py --strategy lab_xxx` |
| MaxDD | < 5% | 同上 |
| 與主線相關性 | < 0.5 | 算 daily pnl correlation |
| 訊號品質 | Slippage 合理、無 silent fail | 查 `logs/bot.log` |
| 風控無違規 | 0 次拒單 | 查 risk manager log |

**全部通過才能併入主線配置**（即允許把 lab config 的 `enabled: true` 對應項合併進 `config/strategies.yaml`）。任何一項沒過就維持 opt-in 狀態。

---

## 5. Phase 2 — 實作需要基礎設施的三個策略

**只有在使用者明確要求、且 Phase 1 的兩個策略已驗證成功後才進入 Phase 2。** 否則回答「建議先完成 Phase 1」。

### 5.1 lab_perp_spot_basis 需要什麼

- 新增 `src/bot/exchange/spot_client.py`（對應既有的 `futures_client.py`）
- 新增 `src/bot/core/events.py` 的 `SpotPriceEvent` 型別
- `Executor` 要能同時下永續和現貨兩條腿，且具備 rollback
- 獨立的 **現貨帳戶餘額/借貸** 風控
- 預估工作量：2-3 個 session

### 5.2 lab_cross_exchange_funding 需要什麼

- 引入 `ccxt` 或為 Bybit / OKX 各建 client
- 跨所資金/保證品餘額 daemon
- 兩所訂單同步下單 + 任一失敗 rollback
- 跨所 settlement reconciliation
- 預估工作量：4-6 個 session，風險極高

### 5.3 lab_btc_dominance_rotation 需要什麼

- 新增 `strategies_lab/lab_data/market_cap_client.py` 用 CoinGecko 或 DefiLlama API
- 日線 scheduler（每日 00:00 UTC 拉一次 BTC.D / OTHERS.D）
- 快取層（避免 rate limit）
- 預估工作量：1 個 session（最簡單的 Phase 2 項目）

**如果使用者要排優先順序，建議 5.3 → 5.1 → 5.2**（簡單 → 中等 → 複雜）。

---

## 6. 主線 regime_routing 跟 lab 策略怎麼共存

`config/strategies.yaml` 有 `regime_strategy_routing`，根據市場 regime 動態選策略。**lab 策略預設不在 routing 表裡**。做法二選一：

**A. 完全獨立**（推薦）
- Lab 策略不進 routing
- Lab 策略在所有 regime 都以**固定比例**運行
- 理由：lab 策略多半是市場中性（basis / pairs），不需要 regime 過濾

**B. 加入 routing**（進階）
- 在合併 config 時把 lab 策略加到對應 regime：
  - `lab_stat_arb_pairs` → `ranging`（最適合橫盤）
  - `lab_liquidation_hunter` → `volatile`
  - `lab_perp_spot_basis` → 四種 regime 都加（完全中性）
- 要小心：這會讓 `regime_sizing` 也作用在 lab 上，降低 lab 的實際配置

**如果使用者沒特別說，用 A。**

---

## 7. 絕對不能做的事 (Guardrails)

| ❌ 禁止 | 🧠 理由 | ✅ 該怎麼做 |
|-------|---------|-----------|
| 直接改 `config/strategies.yaml` 加入 lab 策略 | 汙染主線、無法回退 | 用 `integration.py::load_merged_config` 動態合併 |
| 在 `register_default_strategies()` 加 `lab_*` | 讓 lab 變成 default，違反 opt-in 原則 | 永遠只在 `register_lab_strategies()` 註冊 |
| 為 lab 建第二個 `RiskManager` | 雙 risk 管理會突破總風險上限 | 共用主線 RiskManager，用 `strategy_name.startswith("lab_")` 套更嚴 threshold |
| 在 lab 策略裡直接呼叫 Binance REST API | 破壞事件驅動架構、無法 backtest | 只透過 `inject_*()` 方法接收外部資料 |
| 直接把 `proposals.md` 預期績效當成「已驗證」 | 那是文獻估計，不是回測結果 | Paper 30 天後才能引用實測 Sharpe |
| 把 `--lab` 設為預設 true | 讓主線行為默默變化 | 預設一律 false，opt-in |
| 為了讓 test 通過去改策略邏輯 | 策略是唯一事實來源 | 改測試 fixture，邏輯不動 |

---

## 8. 常見陷阱與對應

### 陷阱 1：Lab 策略看似有訊號但不下單

**症狀：** `logger.info("signal_generated")` 有，但 `journal` 沒紀錄。
**原因 99%：** 主線 `RiskManager.validate_signal()` 拒絕（通常是槓桿超限或 daily loss）。
**查法：** `grep signal_rejected logs/bot.log | grep lab_`

### 陷阱 2：`lab_stat_arb_pairs` Kalman beta 發散

**症狀：** `beta` 跳到 1e6 或 NaN。
**原因：** Recursive 更新的 residual / lb 在 lb 極小時爆炸。
**修法：** 在 `_update_beta` 加 clamp：`beta = max(0.01, min(100.0, beta))`。

### 陷阱 3：Liquidation hunter 在低流動性市場被二次清算

**症狀：** 進場後 3 根 bar 都打到 stop。
**修法：** 在 `risk_limits.yaml` 設 `min_symbol_volume_usd: 10_000_000` 過濾掉低流動性 alt。

### 陷阱 4：主線 `bridge.py` 和 lab 同時動作導致 position 雙倍

**症狀：** 某 symbol 實際 position 是設定值的 2 倍。
**原因：** 主線 `trend_donchian_mtf` 和 `lab_stat_arb_pairs` 同時對 ETHUSDT 開單，而 `Executor` 沒有按 `strategy_name` 維度分倉。
**修法：** 確認 `Executor.open_position()` 用 `(symbol, strategy_name)` 當 key，而不是只用 `symbol`。
**驗證：** 查 `src/bot/execution/executor.py` 的 positions dict key 結構。

### 陷阱 5：回測結果看起來太好

**症狀：** lab 策略回測 Sharpe 8+。
**可能原因：**
- Lookahead bias（用了未來資料算 basis/funding）
- 未計入手續費 / funding cost
- 資料本身有 survivorship bias
**驗證：** 強制用 `event.timestamp` 作為訊號時間戳 + 使用 `next_bar_open` 價執行。

---

## 9. 檔案責任對照表

| 任務 | 該動 | 不該動 |
|------|------|--------|
| 改 lab 策略邏輯 | `strategies_lab/*.py` | `src/bot/strategy/*` |
| 新增 lab 策略 | `strategies_lab/new_xxx.py` + `register_lab.py` | `registry.py::register_default_strategies` |
| 調 lab 參數 | `strategies_lab/config/strategies_lab.yaml` | `config/strategies.yaml` |
| 加 lab 測試 | `strategies_lab/tests/` | `tests/`（主線測試） |
| 整合 runner | `strategies_lab/integration.py` | `scripts/run_*.py`（僅允許加 opt-in flag） |
| 加 spot / cross-ex / 市值資料源 | `src/bot/exchange/` + `strategies_lab/lab_data/` | 既有的 `futures_client.py` |

---

## 10. 給下一個 agent 的開場動作（Copy-Paste 用）

如果你剛接手，**先做這三件事確認環境正常**：

```bash
# 1. 確認主線測試全綠
PYTHONPATH=src python -m pytest tests/ -q

# 2. 確認 lab 測試全綠
PYTHONPATH=src python -m pytest strategies_lab/tests/ -v

# 3. 確認 lab smoke runner 能跑
PYTHONPATH=src python strategies_lab/examples/run_lab_smoke.py
```

三個都綠，才開始動任何新程式。如果 (1) 紅了，**先通報使用者**——那不是你造成的，是 pre-existing。

---

## 11. 一句話總結

> **Phase 1 的目標是「用 7% 資金 + 完全 opt-in 架構證明 lab 策略確實有邊際」；Phase 2 才考慮基礎設施擴充。不要反過來。**

如果使用者跳過 Phase 1 直接要 Phase 2，**明確告訴他這是高風險路徑**並建議至少跑 `lab_stat_arb_pairs` 的 2 週 paper 作為 sanity check。
