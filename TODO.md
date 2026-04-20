# Cry2 待完成事項

## 完成項目 ✅

### GPT-5.4 Code Review 修復
- [x] **High** — BacktestRunner 傳 symbol/timeframe 給 FundingReversalVBT
- [x] **High** — PairTrading pair data 載入失敗時 raise 而非 silent fallback
- [x] **Medium** — max_hold_bars 實作（VBT `_apply_max_hold` + `run_backtest`）
- [x] **Low** — `.claude/` 移出版本控制
- [x] **Low** — 新增 20 個 regression tests（pair_trading, block_bootstrap, nested WF）

### TradeJournal 交易紀錄系統
- [x] SQLite 持久化（fills + trades 配對）
- [x] EventBus 整合（自動記錄 FillEvent）
- [x] `reconcile()` 斷線恢復（從幣安 REST 補回遺漏成交）
- [x] `open_trades()` 查詢未平倉
- [x] 12 個單元測試全數通過

### 系統整合
- [x] `run_live.py` / `run_paper.py` 接入 TradeJournal
- [x] 啟動時自動 reconcile（查詢 open trades + 24h 活躍 symbols）
- [x] 關閉時 graceful `journal.close()`
- [x] TradeAnalyzer bridge（live trades → backtest_tool analytics/robustness）
- [x] `BinanceRestClient.get_account_trades()` API 新增

---

## 待完成事項 📋

### 🔥 V7.4 後續優化 (高優先)

- [ ] **混合槓桿策略**: 穩定策略 (trend_donchian, tail_risk_hedge) 維持 2x，高DD策略 (momentum_ranking MaxDD -67%, grid_sol -62%) 降至 1-1.5x
- [ ] **更新 BACKTEST_REPORT_V7.md**: 加入 V7.4 槓桿分析章節，更新所有績效數字
- [ ] **V7.4 已知問題**: 理論模型 ($1,527) vs 實際VBT ($616) 差距大，源於策略層級vs組合層級槓桿差異

### 📊 Phase D: 風控進階優化

- [ ] **D2 Regime Detection 校準**: 基於實際數據校準 ADX 閾值 (trending/ranging 切換)
- [ ] **D3 波動率自適應槓桿**: ATR percentile > 80 → 降至 1x, < 20 → 允許 2-3x
- [ ] **D1 組合風控**: Portfolio Stop (MaxDD>20%暫停8天), 連虧5次暫停24 bars

### 🚀 Phase F: Live Trading 部署

- [ ] **F1** 更新 bridge.py → V7.4 策略配置 (目前仍為 V6)
- [ ] **F2** 更新 run_live.py → V7.4 參數 + 2x 槓桿
- [ ] **F3** User Data Stream (限價單追蹤)
- [ ] **F4** Paper Trading 模式測試
- [ ] **F5** 部署建議書 + 監控指標

### 1. GPT-5.4 最終 Code Review
- [ ] 推送所有變更後請 GPT-5.4 做一次完整 review
- [ ] 檢查是否有新的高優先問題

### 2. reconcile 單元測試
- [ ] Mock Binance API 回傳的 trade 格式
- [ ] 測試 dedup 邏輯（已存在的 order_id 不重複插入）
- [ ] 測試空 journal → 跳過 reconcile
- [ ] 測試有 open trades → 正確查詢對應 symbols

### 3. Dashboard / 分析介面
- [ ] 即時顯示 TradeJournal 統計（勝率、PnL、持倉）
- [ ] TradeAnalyzer 健康報告視覺化（rolling Sharpe, decay detection）
- [ ] 策略相關性矩陣圖表
- [ ] Monte Carlo 模擬結果圖

### 4. 進階優化
- [ ] 將 `_reconcile_from_binance` 抽成共用模組（目前 run_live / run_paper 重複）
- [ ] WebSocket 斷線重連時也觸發 reconcile
- [ ] 加入 strategy name 自動推斷（根據 client_order_id 前綴）
- [ ] 定時自動 snapshot + reconcile（每小時）

### 5. 測試覆蓋率提升
- [ ] CircuitBreaker 3 個 pre-existing 失敗修復
- [ ] TradeAnalyzer 單元測試
- [ ] LiveExecutor 整合測試（mock exchange）

---

## 驗證指令

```bash
# 單元測試
PYTHONPATH=src python -m pytest tests/ -q              # 74/77 pass (3 pre-existing CB failures)

# Backtest 測試
python -m pytest backtest_tool/tests/ -q               # 105 pass

# Regression 測試
python -m pytest backtest_tool/tests/test_regression.py -v  # 20 pass

# Trade Journal 測試
PYTHONPATH=src python -m pytest tests/unit/test_trade_journal.py -v  # 12 pass

# V7.4 組合回測
$env:PYTHONPATH="src;."; python backtest_tool/scripts/full_portfolio_backtest.py
# Expected: Sharpe 2.161, MaxDD -14.0%, $150→$616, 17 strats, 2x leverage
```

## 架構筆記

```
交易流程:
WebSocket → MarketEvent → Strategy → SignalEvent → RiskManager → OrderEvent
→ LiveExecutor → Binance REST → FillEvent → Portfolio + TradeJournal(SQLite)

啟動恢復流程:
Bot 啟動 → TradeJournal 讀取 SQLite → 印出 resume info
→ _reconcile_from_binance() → 查 open_trades + 24h fills → 補回遺漏

分析流程:
TradeJournal.export_*() → TradeAnalyzer → backtest_tool analytics/robustness
```
