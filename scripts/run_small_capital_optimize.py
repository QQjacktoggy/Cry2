#!/usr/bin/env python3
"""Fine-tuned backtest for 150 USDT small capital.

Runs trend_donchian (4h) and mean_reversion_bb (1h) as SEPARATE engines
with capital split by allocation, then combines equity curves.
"""
from __future__ import annotations
import copy, sys, json, itertools
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bot.backtest.engine import BacktestEngine
from bot.backtest.metrics import MetricsCalculator
from bot.config.env import load_env
from bot.config.loader import load_config
from bot.core.logger import setup_logging
from bot.data.storage import ParquetStorage
from bot.execution.fee_model import FeeModel
from bot.execution.slippage import SlippageModel
from bot.strategy.registry import StrategyRegistry, register_default_strategies
from bot.utils.time_utils import datetime_to_ms, parse_date

INITIAL_CAPITAL = 150.0
START = "2024-01-01"
END = "2024-12-31"

PARAM_GRID = {
    "leverage": [1, 2],
    "trend_risk_pct": [0.5, 1.0, 1.5],
    "bb_risk_pct": [0.3, 0.5, 1.0],
    "alloc_trend": [0.0, 0.25, 0.50, 0.75, 1.0],
}


def _make_engine(config, strategies, start_ms, end_ms, capital):
    """Build a BacktestEngine for a list of same-timeframe strategies."""
    all_symbols = sorted({sym for s in strategies for sym in s.symbols})
    exec_cfg = config.get("execution", {})
    risk_limits = config.get("risk_limits", {})
    cb_cfg = {
        "bar_change_threshold_pct": risk_limits.get("circuit_breaker_bar_pct", 5.0),
        "cooldown_minutes": risk_limits.get("circuit_breaker_cooldown_min", 30),
    }
    return BacktestEngine(
        strategies=strategies,
        symbols=all_symbols,
        start_ms=start_ms,
        end_ms=end_ms,
        initial_capital=capital,
        storage=ParquetStorage(config.get("paths", {}).get("data_dir", "./data")),
        slippage_model=SlippageModel(
            model_type=exec_cfg.get("slippage_model", "fixed_bps"),
            fixed_bps=exec_cfg.get("slippage_bps", 2),
            bps_base=exec_cfg.get("slippage_bps_base", 1),
        ),
        fee_model=FeeModel(
            maker_rate=exec_cfg.get("fee_rate_maker", 0.0002),
            taker_rate=exec_cfg.get("fee_rate_taker", 0.0004),
        ),
        circuit_breaker_config=cb_cfg,
    )


def run_one(config, start_ms, end_ms, leverage, trend_risk, bb_risk, alloc_trend):
    cfg = copy.deepcopy(config)
    register_default_strategies()

    alloc_bb = round(1.0 - alloc_trend, 2)
    cap_trend = INITIAL_CAPITAL * alloc_trend
    cap_bb = INITIAL_CAPITAL * alloc_bb

    combined_fills = []
    combined_equity = []
    total_final = 0.0

    # --- Run trend_donchian (4h) ---
    if alloc_trend > 0 and cap_trend >= 1.0:
        cfg_t = copy.deepcopy(cfg)
        sc = cfg_t.get("strategies", {})
        for k in sc:
            sc[k]["enabled"] = False
        sc["trend_donchian"]["enabled"] = True
        sc["trend_donchian"]["leverage"] = leverage
        sc["trend_donchian"]["risk_per_trade_pct"] = trend_risk
        strats_t = StrategyRegistry.create_all(sc)
        if strats_t:
            eng_t = _make_engine(cfg_t, strats_t, start_ms, end_ms, cap_trend)
            res_t = eng_t.run()
            combined_fills.extend(res_t["fills"])
            combined_equity.extend(res_t["equity_curve"])
            total_final += res_t["equity_curve"][-1][1] if res_t["equity_curve"] else cap_trend

    # --- Run mean_reversion_bb (1h) ---
    if alloc_bb > 0 and cap_bb >= 1.0:
        cfg_b = copy.deepcopy(cfg)
        sc = cfg_b.get("strategies", {})
        for k in sc:
            sc[k]["enabled"] = False
        sc["mean_reversion_bb"]["enabled"] = True
        sc["mean_reversion_bb"]["leverage"] = leverage
        sc["mean_reversion_bb"]["risk_pct"] = bb_risk
        strats_b = StrategyRegistry.create_all(sc)
        if strats_b:
            eng_b = _make_engine(cfg_b, strats_b, start_ms, end_ms, cap_bb)
            res_b = eng_b.run()
            combined_fills.extend(res_b["fills"])
            combined_equity.extend(res_b["equity_curve"])
            total_final += res_b["equity_curve"][-1][1] if res_b["equity_curve"] else cap_bb

    if not combined_equity:
        return None

    # Merge equity curves by time: sum equities at each timestamp
    eq_map: dict[int, float] = {}
    for ts, eq in combined_equity:
        eq_map[ts] = eq_map.get(ts, 0.0) + eq
    merged_eq = sorted(eq_map.items())

    calc = MetricsCalculator(
        equity_curve=merged_eq,
        fills=combined_fills,
        initial_capital=INITIAL_CAPITAL,
    )
    return calc.calculate_all()


def main():
    load_env()
    config = load_config("config/config.yaml", environment="backtest")
    setup_logging("WARNING", json_format=False)

    start_ms = datetime_to_ms(parse_date(START))
    end_ms = datetime_to_ms(parse_date(END))

    combos = list(itertools.product(
        PARAM_GRID["leverage"],
        PARAM_GRID["trend_risk_pct"],
        PARAM_GRID["bb_risk_pct"],
        PARAM_GRID["alloc_trend"],
    ))
    print(f"Testing {len(combos)} parameter combinations...\n")

    results = []
    for i, (lev, tr, br, at) in enumerate(combos, 1):
        try:
            m = run_one(config, start_ms, end_ms, lev, tr, br, at)
            if m is None:
                continue
            entry = {
                "leverage": lev,
                "trend_risk_pct": tr,
                "bb_risk_pct": br,
                "alloc_trend": at,
                "alloc_bb": round(1.0 - at, 2),
                "total_return": m.get("total_return", -999),
                "sharpe": m.get("sharpe_ratio", 0),
                "max_dd": m.get("max_drawdown_pct", -999),
                "win_rate": m.get("win_rate", 0),
                "profit_factor": m.get("profit_factor", 0),
                "trades": m.get("total_trades", 0),
                "final_equity": m.get("final_equity", 0),
                "monthly": m.get("monthly_returns", []),
            }
            results.append(entry)
        except Exception as e:
            print(f"  ERROR combo #{i}: {e}")
        if i % 15 == 0:
            print(f"  Progress: {i}/{len(combos)}")

    safe = [r for r in results if r["max_dd"] > -1.0]
    risky = [r for r in results if r["max_dd"] <= -1.0]
    safe.sort(key=lambda r: r["total_return"], reverse=True)
    risky.sort(key=lambda r: r["total_return"], reverse=True)

    print(f"\n{'='*80}")
    print(f"RESULTS: {len(safe)} safe combos (DD > -100%), {len(risky)} risky combos")
    print(f"{'='*80}")

    print(f"\n--- TOP 10 SAFE COMBOS (max DD > -100%) ---")
    for i, r in enumerate(safe[:10], 1):
        print(f"  #{i}: Return {r['total_return']:>8.2%} | Sharpe {r['sharpe']:>6.2f} | "
              f"MaxDD {r['max_dd']:>8.2%} | WR {r['win_rate']:>5.1%} | PF {r['profit_factor']:>5.2f} | "
              f"Trades {r['trades']:>3}")
        print(f"       Lev={r['leverage']} TrendRisk={r['trend_risk_pct']}% BBRisk={r['bb_risk_pct']}% "
              f"Alloc: trend={r['alloc_trend']:.0%} bb={r['alloc_bb']:.0%}")

    if safe:
        best = safe[0]
        print(f"\n{'='*72}")
        print(f"BEST SAFE COMBO MONTHLY BREAKDOWN")
        print(f"{'='*72}")
        month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        print(f"{'Month':<12} {'Start':>12} {'End':>12} {'Profit':>12} {'Return':>10}")
        print("-" * 72)
        for m in best["monthly"]:
            label = f"{m['year']}-{month_names[m['month']]}"
            sign = "+" if m["profit"] >= 0 else ""
            print(f"{label:<12} {m['start_equity']:>12.2f} {m['end_equity']:>12.2f} "
                  f"{sign}{m['profit']:>11.2f} {m['return_pct']:>9.2%}")
        if best["monthly"]:
            tp = best["monthly"][-1]["end_equity"] - INITIAL_CAPITAL
            print("-" * 72)
            print(f"{'TOTAL':<12} {INITIAL_CAPITAL:>12.2f} {best['monthly'][-1]['end_equity']:>12.2f} "
                  f"{'+' if tp >= 0 else ''}{tp:>11.2f} {tp/INITIAL_CAPITAL:>9.2%}")
        print("=" * 72)

    print(f"\n--- TOP 5 RISKY COMBOS (for reference) ---")
    for i, r in enumerate(risky[:5], 1):
        print(f"  #{i}: Return {r['total_return']:>8.2%} | MaxDD {r['max_dd']:>8.2%} | "
              f"Lev={r['leverage']} trend={r['alloc_trend']:.0%} bb={r['alloc_bb']:.0%}")

    out = Path("data/backtest_results/small_capital_optimization.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump({"safe": safe[:20], "risky": risky[:10]}, f, indent=2, default=str)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
