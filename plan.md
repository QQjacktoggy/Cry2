# Cry2 今日盤點與執行計畫

## 問題 / 目標

先基於目前本地已同步的最新主線，重新盤點 Cry2 的實際進度，辨識哪些工作已經完成、哪些仍未完成，並整理出一份可直接照著做的今日 plan。這次盤點以 **目前 code 為準**，不以舊記憶或舊 TODO 為準。

## 目前實際進度（依目前 code / recent commits）

### 已完成

- GPT review 第一波高優先修正已落地：
  - BacktestRunner 已能向支援的策略下放 `symbol` / `timeframe`
  - Pair trading 缺 pair data 時會直接 raise，不再 silent fallback
  - `max_hold_bars` 已實作，且已有 regression tests
- Live 交易紀錄鏈已落地：
  - `TradeJournal`（SQLite fills / trades）
  - `TradeAnalyzer`
  - `run_live.py` / `run_paper.py` 已接 journal
  - `reconcile_from_binance` 已抽成共用模組
- 斷線恢復與 live 狀態觀測已往前推：
  - `UserDataStream`
  - WebSocket reconnect / periodic reconcile
  - `HealthStateWriter`
  - dashboard `5_health.py`
- 風控 / live bridge 持續演進：
  - `create_v74_strategies()` 已存在
  - `run_live.py` / `run_paper.py` 已支援 `--version v74`
  - CircuitBreaker 舊失敗看起來已修
  - `test_trade_analyzer.py`、`test_user_data_stream.py`、`test_live_executor.py` 已存在

### 尚未完成 / 仍需確認

- `TODO.md` 與現況不同步，已經有不少項目被勾錯或沒更新，需先校正
- V7.4 雖已接到 bridge / live / paper 入口，但仍需做一次 **實際 smoke validation**
- 槓桿問題仍是核心未解：
  - `leverage_analysis.py` 已明寫：策略參數內的 leverage 並未真正套用到 VBT strategy-level backtest
  - 目前仍存在「理論槓桿模型 vs 實際 VBT 組合回測」落差
- 回測 / 報告文件尚未同步最新 V7.4 結論
- GPT-5.4 最終 review 尚未做
- reconcile helper 的測試覆蓋雖然相關能力已有，但是否有獨立、完整的 helper 測試仍需補查 / 補齊

## 今日建議執行順序

1. **先做盤點校正**
   - 重新比對 `TODO.md`、recent commits、關鍵入口檔
   - 把「已完成 / 未完成 / 待驗證」三類分清楚

2. **做 V7.4 執行鏈驗證**
   - 檢查 `run_live.py` / `run_paper.py` 的 v74 路徑
   - 至少完成 dry-run / smoke 等級驗證
   - 確認 user data stream、reconcile、health snapshot 不互相打架

3. **處理槓桿差距這個主問題**
   - 釐清要先「文件化風險」還是「真正修 backtest leverage semantics」
   - 盤點 `full_portfolio_backtest.py`、`leverage_analysis.py`、`v74_leverage_optimization.py`
   - 決定下一步是修模型還是先更新報告與預期值

4. **補文件 / review 收尾**
   - 更新 TODO / 報告
   - 跑 GPT-5.4 final review
   - 把 review 結果再收斂成下一輪 backlog

## 今日 todos

- `audit-current-state`: 依 code 與 commit 重新整理真實進度，修正 TODO 的過期項目
- `validate-v74-runtime-path`: 驗證 live / paper 的 v74 路徑與 reconcile / health / UDS 流程
- `investigate-leverage-gap`: 釐清 VBT 槓桿沒有真正套用的原因、影響面、修法
- `sync-docs-and-report`: 更新 TODO 與回測 / 部署文件，讓描述跟現況一致
- `run-final-review`: 在上述資訊穩定後做 GPT-5.4 最終 review

## 備註

- 目前主線最近幾個合併顯示，雲端已經做了比先前記憶更多的事，所以 **明天開始前不要直接照舊 TODO 開工**，應先以盤點後的最新版 TODO 為準。
- 今天最值得先確認的不是新功能，而是 **哪些事情其實已完成、哪些只是文件沒更新、哪些才是真正還沒做**。
