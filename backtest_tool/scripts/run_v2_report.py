#!/usr/bin/env python3
"""V2 完整回測報告：MomentumRankingV2 + GridTrendBiasV2 三幣別 + 組合分析。

用法：
    python -m backtest_tool.scripts.run_v2_report
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

# ── 最佳參數（來自 param scan）────────────────────────────
MRV2_BEST = dict(
    roc_period=30, lookback=90, upper_pct=75, lower_pct=25,
    adx_threshold=15, atr_mult=1.5,
)
GTBV2_BEST = dict(
    bb_period=15, bb_std=1.5, ema_period=150,
    rsi_oversold=40, rsi_overbought=65, atr_mult=3.0,
)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
MRV2_CAPITAL = 100.0    # 主策略資金
GTBV2_CAPITAL = 50.0    # 補充策略資金
TOTAL_CAPITAL = 150.0


def run_one(strategy_name: str, params: dict, symbol: str, timeframe: str,
            capital: float) -> dict:
    runner = BacktestRunner(config_path=str(CONFIG_PATH))
    runner.initial_capital = capital
    df = STORE.load_klines(symbol, timeframe)
    cls = STRATEGY_MAP[strategy_name]
    strat = cls(params=params)
    result = runner.run_single(strat, df, symbol=symbol, timeframe=timeframe,
                               initial_capital=capital)
    m = result.metrics
    m["symbol"] = symbol
    m["strategy"] = strategy_name
    return m, result


def monthly_pnl(result, capital: float) -> pd.Series:
    """月度損益（USD）。"""
    try:
        pf = result.portfolio
        equity = pf.value()
        # resample to month-end
        monthly = equity.resample("ME").last()
        pnl = monthly.diff()
        pnl.iloc[0] = monthly.iloc[0] - capital
        return pnl
    except Exception:
        return pd.Series(dtype=float)


def walk_forward(strategy_name: str, params: dict, symbol: str,
                 timeframe: str, capital: float) -> list[dict]:
    """把 2 年數據切三段，各跑一次回測。"""
    df = STORE.load_klines(symbol, timeframe)
    dates = df.index
    total = len(dates)
    seg = total // 3
    segments = [
        (dates[0], dates[seg - 1]),
        (dates[seg], dates[2 * seg - 1]),
        (dates[2 * seg], dates[-1]),
    ]
    results = []
    for i, (s, e) in enumerate(segments, 1):
        runner = BacktestRunner(config_path=str(CONFIG_PATH))
        runner.initial_capital = capital
        seg_df = df[(df.index >= s) & (df.index <= e)]
        cls = STRATEGY_MAP[strategy_name]
        strat = cls(params=params)
        try:
            result = runner.run_single(strat, seg_df, symbol=symbol,
                                       timeframe=timeframe, initial_capital=capital)
            m = result.metrics
            results.append({
                "段落": f"段{i} ({s.strftime('%Y-%m')} → {e.strftime('%Y-%m')})",
                "總收益率": f"{m.get('total_return', 0):+.2f}%",
                "Sharpe": f"{m.get('sharpe_ratio', 0):.4f}",
                "最大回撤": f"{m.get('max_drawdown', 0):.2f}%",
                "交易次數": int(m.get('total_trades', 0)),
            })
        except Exception as ex:
            results.append({"段落": f"段{i}", "錯誤": str(ex)})
    return results


def print_section(title: str) -> None:
    print(f"\n{'='*65}")
    print(f"  {title}")
    print(f"{'='*65}")


def main() -> None:
    print("\n" + "█" * 65)
    print("  V2 完整回測報告｜初始資金 $150 USDT")
    print("  主策略：MomentumRankingV2（$100）")
    print("  輔助策略：GridTrendBiasV2（$50）")
    print("█" * 65)

    # ── 1. MomentumRankingV2 三幣別 ──────────────────────────
    print_section("MomentumRankingV2 — 三幣別回測（$100）")
    mrv2_results = {}
    for sym in SYMBOLS:
        m, res = run_one("momentum_ranking_v2", MRV2_BEST, sym, "1d", MRV2_CAPITAL)
        mrv2_results[sym] = (m, res)
        print(f"\n  【{sym}】")
        print(f"    總收益率:    {m.get('total_return', 0):+.2f}%")
        print(f"    年化收益率:  {m.get('annual_return', 0):+.2f}%")
        print(f"    Sharpe:      {m.get('sharpe_ratio', 0):.4f}")
        print(f"    最大回撤:    {m.get('max_drawdown', 0):.2f}%")
        print(f"    勝率:        {m.get('win_rate', 0):.1f}%")
        print(f"    交易次數:    {int(m.get('total_trades', 0))}")
        print(f"    最終資產:    ${m.get('final_value', 0):.2f}")

    # ── 2. MomentumRankingV2 BTC 月度損益 ────────────────────
    print_section("MomentumRankingV2（BTC）月度損益")
    btc_m, btc_res = mrv2_results["BTCUSDT"]
    mpnl = monthly_pnl(btc_res, MRV2_CAPITAL)
    if not mpnl.empty:
        cum = 0.0
        print(f"  {'月份':<12} {'月損益(USD)':>12} {'累計(USD)':>12}")
        print("  " + "-" * 38)
        for dt, val in mpnl.items():
            cum += val
            sign = "▲" if val >= 0 else "▼"
            print(f"  {dt.strftime('%Y-%m'):<12} {sign} {val:>+8.2f}     {cum:>+8.2f}")
    else:
        print("  （無月度資料）")

    # ── 3. GridTrendBiasV2 三幣別 ────────────────────────────
    print_section("GridTrendBiasV2 — 三幣別回測（$50）")
    gtbv2_results = {}
    for sym in SYMBOLS:
        m, res = run_one("grid_trend_bias_v2", GTBV2_BEST, sym, "4h", GTBV2_CAPITAL)
        gtbv2_results[sym] = (m, res)
        print(f"\n  【{sym}】")
        print(f"    總收益率:    {m.get('total_return', 0):+.2f}%")
        print(f"    年化收益率:  {m.get('annual_return', 0):+.2f}%")
        print(f"    Sharpe:      {m.get('sharpe_ratio', 0):.4f}")
        print(f"    最大回撤:    {m.get('max_drawdown', 0):.2f}%")
        print(f"    勝率:        {m.get('win_rate', 0):.1f}%")
        print(f"    交易次數:    {int(m.get('total_trades', 0))}")
        print(f"    最終資產:    ${m.get('final_value', 0):.2f}")

    # ── 4. 組合損益彙整 ──────────────────────────────────────
    print_section("V2 組合損益彙整（$150 總資金）")
    print(f"  {'幣別':<10} {'MRV2最終':>12} {'GTBv2最終':>12} {'組合最終':>12} {'組合報酬':>10}")
    print("  " + "-" * 56)
    for sym in SYMBOLS:
        mrv2_final = mrv2_results[sym][0].get("final_value", MRV2_CAPITAL)
        gtbv2_final = gtbv2_results[sym][0].get("final_value", GTBV2_CAPITAL)
        combo = mrv2_final + gtbv2_final
        combo_ret = (combo / TOTAL_CAPITAL - 1) * 100
        print(f"  {sym:<10} ${mrv2_final:>10.2f}  ${gtbv2_final:>10.2f}"
              f"  ${combo:>10.2f}  {combo_ret:>+8.2f}%")

    # ── 5. Walk-Forward（BTC MRV2）────────────────────────────
    print_section("Walk-Forward 驗證：MomentumRankingV2 BTC")
    wf = walk_forward("momentum_ranking_v2", MRV2_BEST, "BTCUSDT", "1d", MRV2_CAPITAL)
    for row in wf:
        print(f"\n  {row.get('段落','')}")
        for k, v in row.items():
            if k != "段落":
                print(f"    {k}: {v}")

    # ── 6. 原版 vs V2 對比（BTC）─────────────────────────────
    print_section("MomentumRanking 原版 vs V2 對比（BTC）")
    m_orig, _ = run_one("momentum_ranking", {}, "BTCUSDT", "1d", MRV2_CAPITAL)
    m_v2 = mrv2_results["BTCUSDT"][0]
    print(f"  {'指標':<20} {'原版':>12} {'V2':>12}")
    print("  " + "-" * 44)
    for key, label in [
        ("total_return", "總收益率(%)"),
        ("annual_return", "年化收益率(%)"),
        ("sharpe_ratio", "Sharpe"),
        ("max_drawdown", "最大回撤(%)"),
        ("win_rate", "勝率(%)"),
        ("total_trades", "交易次數"),
    ]:
        v_orig = m_orig.get(key, 0)
        v_v2 = m_v2.get(key, 0)
        print(f"  {label:<20} {v_orig:>12.4f} {v_v2:>12.4f}")

    print(f"\n✅ V2 報告完成")


if __name__ == "__main__":
    main()
