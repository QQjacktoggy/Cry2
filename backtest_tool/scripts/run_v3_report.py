#!/usr/bin/env python3
"""V3 短線回測報告：MACDScalper1H + SupertrendScalper1H 三幣別分析。

策略定位：
  V2（日線）= 波段持倉，每年 10-15 次，大波段獲利
  V3（1小時）= 短線積累，每年 150-300 次，小步快走
  合併使用：V2 捕大波段，V3 在每日活躍交易中累積

用法：
    python -m backtest_tool.scripts.run_v3_report
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import DataStore
from backtest_tool.engine import BacktestRunner
from backtest_tool.strategies import STRATEGY_MAP

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"
STORE = DataStore()

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# ── 資金配置 ──────────────────────────────────────────────
# V3 獨立運行（$150 總資金）
MACD_CAP    = 80.0   # MACD 短線（BTC + ETH）
ST_CAP      = 70.0   # Supertrend 短線（BTC）
TOTAL_CAP   = 150.0

# 與 V2 結合運行（BTC $150 範例）
COMBO_MRV2  = 100.0
COMBO_MACD  = 30.0
COMBO_ST    = 20.0


def run_one(strategy_name: str, symbol: str, timeframe: str, capital: float, params: dict | None = None):
    runner = BacktestRunner(config_path=str(CONFIG_PATH))
    runner.initial_capital = capital
    df = STORE.load_klines(symbol, timeframe)
    cls = STRATEGY_MAP[strategy_name]
    strat = cls(params=params)
    result = runner.run_single(strat, df, symbol=symbol, timeframe=timeframe, initial_capital=capital)
    return result.metrics, result


def monthly_pnl(result, capital: float) -> pd.Series:
    try:
        equity = result.portfolio.value()
        monthly = equity.resample("ME").last()
        pnl = monthly.diff()
        pnl.iloc[0] = monthly.iloc[0] - capital
        return pnl
    except Exception:
        return pd.Series(dtype=float)


def print_section(title: str) -> None:
    print(f"\n{'='*65}")
    print(f"  {title}")
    print(f"{'='*65}")


def main() -> None:
    print("\n" + "█" * 65)
    print("  V3 短線回測報告｜1h 時框｜小賺累積型")
    print("  MACDScalper1H + SupertrendScalper1H")
    print("█" * 65)

    # ── 1. MACDScalper1H 三幣別 ──────────────────────────────
    print_section("MACDScalper1H — 三幣別（$50/幣）")
    macd_results = {}
    for sym in SYMBOLS:
        m, res = run_one("macd_scalper_1h", sym, "1h", 50.0)
        macd_results[sym] = (m, res)
        status = "✅" if m.get("total_return", 0) > 0 else "❌"
        print(f"\n  {status} 【{sym}】")
        print(f"    總收益率:    {m.get('total_return', 0):+.2f}%")
        print(f"    年化收益率:  {m.get('annual_return', 0):+.2f}%")
        print(f"    Sharpe:      {m.get('sharpe_ratio', 0):.4f}")
        print(f"    最大回撤:    {m.get('max_drawdown', 0):.2f}%")
        print(f"    勝率:        {m.get('win_rate', 0):.1f}%")
        print(f"    交易次數:    {int(m.get('total_trades', 0))} 次（月均 {int(m.get('total_trades',0))//24} 次）")
        print(f"    最終資產:    ${m.get('final_value', 0):.2f}")

    # ── 2. SupertrendScalper1H 三幣別 ────────────────────────
    print_section("SupertrendScalper1H — 三幣別（$50/幣）")
    st_results = {}
    for sym in SYMBOLS:
        m, res = run_one("supertrend_scalper_1h", sym, "1h", 50.0)
        st_results[sym] = (m, res)
        status = "✅" if m.get("total_return", 0) > 0 else "❌"
        print(f"\n  {status} 【{sym}】")
        print(f"    總收益率:    {m.get('total_return', 0):+.2f}%")
        print(f"    年化收益率:  {m.get('annual_return', 0):+.2f}%")
        print(f"    Sharpe:      {m.get('sharpe_ratio', 0):.4f}")
        print(f"    最大回撤:    {m.get('max_drawdown', 0):.2f}%")
        print(f"    勝率:        {m.get('win_rate', 0):.1f}%")
        print(f"    交易次數:    {int(m.get('total_trades', 0))} 次（月均 {int(m.get('total_trades',0))//24} 次）")
        print(f"    最終資產:    ${m.get('final_value', 0):.2f}")

    # ── 3. MACD BTC 月度損益 ─────────────────────────────────
    print_section("MACDScalper1H（BTC）月度損益")
    btc_macd_m, btc_macd_res = macd_results["BTCUSDT"]
    mpnl = monthly_pnl(btc_macd_res, 50.0)
    if not mpnl.empty:
        cum = 0.0
        positive = sum(1 for v in mpnl if v > 0)
        total_m = len(mpnl)
        print(f"  {'月份':<12} {'月損益(USD)':>12} {'累計(USD)':>12}")
        print("  " + "-" * 38)
        for dt, val in mpnl.items():
            cum += val
            sign = "▲" if val >= 0 else "▼"
            print(f"  {dt.strftime('%Y-%m'):<12} {sign} {val:>+8.2f}     {cum:>+8.2f}")
        print(f"\n  盈利月份：{positive}/{total_m} ({positive/total_m*100:.0f}%)")

    # ── 4. V3 與 V2 對比表 ───────────────────────────────────
    print_section("策略對比：V2 日線 vs V3 短線（BTC）")

    # V2 MomentumRankingV2
    mrv2_params = dict(roc_period=30, lookback=90, upper_pct=75, lower_pct=25,
                       adx_threshold=15, atr_mult=1.5)
    mv2_m, _ = run_one("momentum_ranking_v2", "BTCUSDT", "1d", 100.0, mrv2_params)
    macd_m = macd_results["BTCUSDT"][0]
    st_m   = st_results["BTCUSDT"][0]

    print(f"\n  {'指標':<22} {'V2 MRV2(1d)':>14} {'MACD(1h)':>12} {'ST(1h)':>10}")
    print("  " + "-" * 58)
    metrics_map = [
        ("total_return",   "總收益率(%)"),
        ("annual_return",  "年化收益率(%)"),
        ("sharpe_ratio",   "Sharpe"),
        ("max_drawdown",   "最大回撤(%)"),
        ("win_rate",       "勝率(%)"),
        ("total_trades",   "交易次數"),
    ]
    for key, label in metrics_map:
        v2   = mv2_m.get(key, 0)
        macd = macd_m.get(key, 0)
        st   = st_m.get(key, 0)
        print(f"  {label:<22} {v2:>14.2f} {macd:>12.2f} {st:>10.2f}")

    # ── 5. 合併使用：V2 + MACD（BTC，$150）──────────────────
    print_section("組合方案：V2(1d) $100 + MACD(1h) $30 + ST(1h) $20（BTC，$150）")

    macd30_m, _ = run_one("macd_scalper_1h", "BTCUSDT", "1h", 30.0)
    st20_m, _   = run_one("supertrend_scalper_1h", "BTCUSDT", "1h", 20.0)

    mrv2_final  = mv2_m.get("final_value", 100.0)
    macd30_final = macd30_m.get("final_value", 30.0)
    st20_final   = st20_m.get("final_value", 20.0)
    combo_final  = mrv2_final + macd30_final + st20_final
    combo_ret    = (combo_final / 150.0 - 1) * 100

    print(f"\n  V2 MRV2 $100  → ${mrv2_final:.2f}  ({mv2_m.get('total_return',0):+.2f}%)")
    print(f"  MACD   $30   → ${macd30_final:.2f}  ({macd30_m.get('total_return',0):+.2f}%)")
    print(f"  ST     $20   → ${st20_final:.2f}  ({st20_m.get('total_return',0):+.2f}%)")
    print(f"  {'─'*45}")
    print(f"  組合總計 $150 → ${combo_final:.2f}  （{combo_ret:+.2f}%）")
    print(f"  MACD 月均交易次數：{int(macd30_m.get('total_trades',0))//24} 次")
    print(f"  ST   月均交易次數：{int(st20_m.get('total_trades',0))//24} 次")
    print(f"  V2   月均交易次數：{int(mv2_m.get('total_trades',0))//24} 次")
    print(f"  合計月均交易次數：{(int(macd30_m.get('total_trades',0)) + int(st20_m.get('total_trades',0)) + int(mv2_m.get('total_trades',0)))//24} 次")

    # ── 6. 行情適應說明 ──────────────────────────────────────
    print_section("行情適應邏輯")
    print("""
  牛市（BTC > EMA200）：
    MACDScalper  → 捕捉 MACD 金叉做多（金叉 + MACD>0 + EMA200上方）
    SupertrendST → Supertrend 翻多後追蹤（方向翻轉確認再進）
    → 兩策略均順勢做多，行情越強越有利

  熊市（BTC < EMA200）：
    MACDScalper  → 捕捉 MACD 死叉做空（死叉 + MACD<0 + EMA200下方）
    SupertrendST → Supertrend 翻空後追蹤
    → 自動切換空頭模式，無需人工判斷

  橫盤市（ADX 低）：
    MACDScalper  → ADX > 20 過濾，幾乎不交易
    SupertrendST → EMA200 方向不明，不交易
    → 橫盤自動休息，避免反覆被打臉

  設計優點：不需要人工判斷市場狀態，EMA200 + ADX 自動切換。
    """)

    print(f"✅ V3 報告完成")


if __name__ == "__main__":
    main()
