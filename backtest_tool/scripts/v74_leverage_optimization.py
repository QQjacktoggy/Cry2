# ruff: noqa: E402
"""V7.4 Portfolio Optimization — simulated 2x leverage with MaxDD < -15%.

Strategy:
  1. Run all current V7.4 strategies individually, capture equity curves
  2. Analyze per-strategy MaxDD contribution during portfolio DD periods
  3. Sweep allocation adjustments to find optimal weights
  4. Verify at 2x leverage that MaxDD stays under -15%

Target: simulated 2x leverage, MaxDD ≤ -15%, Sharpe ≥ 2.0, maximize returns
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from backtest_tool.engine.runner import BacktestRunner
from backtest_tool.scripts.full_portfolio_backtest import (
    INITIAL_CAPITAL,
    PORTFOLIO,
    run_single_strategy,
)

TARGET_LEVERAGE = 2.0
MAX_DD_LIMIT = -15.0  # at 2x leverage
BASE_DD_LIMIT = MAX_DD_LIMIT / TARGET_LEVERAGE  # -7.5% base


def run_all_strategies() -> dict[str, dict]:
    """Run all strategies, return {name: {equity, allocation, ...}}."""
    runner = BacktestRunner()
    results = {}
    for name, cfg in PORTFOLIO.items():
        result = run_single_strategy(name, cfg, runner)
        if result is None:
            continue
        results[name] = {
            "equity": result["equity"],
            "allocation": cfg["allocation"],
            "sharpe": result.get("sharpe", 0),
            "return%": result.get("return%", 0),
            "maxdd%": result.get("maxdd%", 0),
            "symbol": cfg["symbol"],
            "strategy_name": cfg["strategy_name"],
        }
    return results


def build_portfolio(results: dict, allocations: dict[str, float]) -> dict:
    """Build portfolio from strategy results with given allocations."""
    curves = {}
    for name, alloc in allocations.items():
        if name not in results or alloc <= 0:
            continue
        eq = results[name]["equity"]
        normalized = eq / eq.iloc[0] * alloc
        curves[name] = normalized

    if not curves:
        return {"error": True}

    combined = pd.DataFrame(curves)
    combined = combined.ffill().bfill()
    portfolio_curve = combined.sum(axis=1)
    portfolio_value = portfolio_curve * INITIAL_CAPITAL

    returns = portfolio_curve.pct_change().dropna()
    ann_factor = np.sqrt(252 * 6)
    sharpe = returns.mean() / returns.std() * ann_factor if returns.std() > 0 else 0

    total_return = (portfolio_value.iloc[-1] / INITIAL_CAPITAL - 1) * 100
    running_max = portfolio_value.cummax()
    drawdown = (portfolio_value - running_max) / running_max
    max_dd = drawdown.min() * 100
    calmar = total_return / abs(max_dd) if max_dd != 0 else 0
    final_value = float(portfolio_value.iloc[-1])

    return {
        "sharpe": sharpe,
        "return%": total_return,
        "maxdd%": max_dd,
        "calmar": calmar,
        "final": final_value,
        "portfolio_curve": portfolio_curve,
        "drawdown": drawdown,
    }


def simulate_leverage(portfolio_curve: pd.Series, leverage: float) -> dict:
    """Apply leverage to portfolio returns."""
    returns = portfolio_curve.pct_change().dropna()
    lev_returns = returns * leverage
    lev_curve = (1 + lev_returns).cumprod()
    lev_value = lev_curve * INITIAL_CAPITAL

    ann_factor = np.sqrt(252 * 6)
    sharpe = lev_returns.mean() / lev_returns.std() * ann_factor if lev_returns.std() > 0 else 0
    total_pct = (lev_value.iloc[-1] / INITIAL_CAPITAL - 1) * 100
    running_max = lev_value.cummax()
    drawdown = (lev_value - running_max) / running_max
    max_dd = drawdown.min() * 100
    calmar = total_pct / abs(max_dd) if max_dd != 0 else 0

    return {
        "sharpe": round(sharpe, 3),
        "return%": round(total_pct, 1),
        "maxdd%": round(max_dd, 1),
        "calmar": round(calmar, 2),
        "final": round(float(lev_value.iloc[-1]), 1),
    }


def analyze_dd_contribution(results: dict, allocations: dict) -> pd.DataFrame:
    """Find which strategies contribute most to portfolio drawdown."""
    portfolio = build_portfolio(results, allocations)
    if "error" in portfolio:
        return pd.DataFrame()

    dd = portfolio["drawdown"]
    worst_dd_idx = dd.idxmin()
    # Window: 20 bars before and after worst DD point
    window_start = max(0, dd.index.get_loc(worst_dd_idx) - 50)
    window_end = min(len(dd), dd.index.get_loc(worst_dd_idx) + 10)
    dd_window = dd.iloc[window_start:window_end]

    rows = []
    for name, alloc in allocations.items():
        if name not in results or alloc <= 0:
            continue
        eq = results[name]["equity"]
        strat_return = eq / eq.iloc[0]
        # Strategy return during DD window
        if worst_dd_idx in strat_return.index:
            peak_idx = dd_window.index[0]
            trough_idx = worst_dd_idx
            if peak_idx in strat_return.index and trough_idx in strat_return.index:
                strat_dd = (strat_return[trough_idx] / strat_return[peak_idx] - 1) * 100
            else:
                strat_dd = 0
        else:
            strat_dd = 0

        rows.append({
            "name": name,
            "allocation": alloc * 100,
            "strategy_dd%": round(strat_dd, 2),
            "weighted_dd%": round(strat_dd * alloc, 2),
            "sharpe": round(results[name]["sharpe"], 3),
            "return%": round(results[name]["return%"], 1),
            "maxdd%": round(results[name]["maxdd%"], 1),
        })

    df = pd.DataFrame(rows).sort_values("weighted_dd%")
    return df


def optimize_allocations(results: dict, base_allocations: dict) -> dict:
    """Systematic allocation optimization for lower MaxDD.

    Strategy:
    1. Group strategies into tiers by their DD behavior
    2. Try reducing high-DD strategies, redistributing to low-DD ones
    3. Find Pareto-optimal allocation at 2x leverage
    """
    # Get strategy-level MaxDD and Sharpe
    strat_info = {}
    for name in base_allocations:
        if name in results:
            strat_info[name] = {
                "maxdd": results[name]["maxdd%"],
                "sharpe": results[name]["sharpe"],
                "return": results[name]["return%"],
            }

    # Sort strategies by MaxDD (worst first)
    sorted_by_dd = sorted(strat_info.items(), key=lambda x: x[1]["maxdd"])

    # Categorize: high-DD (>30%), medium-DD (15-30%), low-DD (<15%)
    high_dd = [n for n, v in sorted_by_dd if v["maxdd"] < -30]
    med_dd = [n for n, v in sorted_by_dd if -30 <= v["maxdd"] < -15]
    low_dd = [n for n, v in sorted_by_dd if v["maxdd"] >= -15]

    print("\n  Strategy DD Categories:")
    print(f"    High DD (>{30}%): {high_dd}")
    print(f"    Med DD (15-30%): {med_dd}")
    print(f"    Low DD (<15%):   {low_dd}")

    # Generate candidate allocations by:
    # 1. Reducing high-DD strategies by 1-3% each
    # 2. Redistributing to low-DD strategies
    candidates = []

    # Candidate 0: Baseline
    candidates.append(("baseline", dict(base_allocations)))

    # Candidate 1: Reduce all high-DD by 2%, give to low-DD
    c1 = dict(base_allocations)
    freed = 0
    for name in high_dd:
        reduce = min(c1.get(name, 0), 0.02)
        c1[name] = c1.get(name, 0) - reduce
        freed += reduce
    if low_dd and freed > 0:
        per_low = freed / len(low_dd)
        for name in low_dd:
            c1[name] = c1.get(name, 0) + per_low
    candidates.append(("reduce_high_dd", c1))

    # Candidate 2: Cap fragile tier at 5% total
    c2 = dict(base_allocations)
    fragile = ["grid_trend_bias_eth"]
    freed2 = 0
    for name in fragile:
        if c2.get(name, 0) > 0.05:
            freed2 += c2[name] - 0.05
            c2[name] = 0.05
    # Give freed to pair trading (lowest correlation)
    if "pair_btc_eth" in c2:
        c2["pair_btc_eth"] += freed2
    candidates.append(("cap_fragile_5pct", c2))

    # Candidate 3: Boost defensive strategies
    c3 = dict(base_allocations)
    # Reduce momentum (high DD) by 2%, increase tail_risk (defensive)
    for name in ["momentum_ranking_eth", "momentum_ranking_bnb"]:
        if name in c3:
            c3[name] = max(0.02, c3[name] - 0.01)
    for name in ["tail_risk_hedge_bnb", "tail_risk_hedge_sol", "tail_risk_hedge_btc"]:
        if name in c3:
            c3[name] = c3[name] + 0.01
    candidates.append(("boost_defensive", c3))

    # Candidate 4: Boost market-neutral
    c4 = dict(base_allocations)
    # Reduce grid_eth by 3%, increase pair and funding
    if "grid_trend_bias_eth" in c4:
        c4["grid_trend_bias_eth"] = max(0.03, c4["grid_trend_bias_eth"] - 0.04)
    if "pair_btc_eth" in c4:
        c4["pair_btc_eth"] += 0.03
    if "funding_reversal_eth" in c4:
        c4["funding_reversal_eth"] += 0.01
    candidates.append(("boost_neutral", c4))

    # Candidate 5: Max diversification — cap everything at 10%
    c5 = dict(base_allocations)
    over = 0
    for name in c5:
        if c5[name] > 0.10:
            over += c5[name] - 0.10
            c5[name] = 0.10
    under = [n for n in c5 if c5[n] < 0.10]
    if under:
        per = over / len(under)
        for name in under:
            c5[name] = min(0.10, c5[name] + per)
    candidates.append(("max_diversify", c5))

    # Candidate 6: Reduce grid_trend_bias family (most fragile)
    c6 = dict(base_allocations)
    grid_names = [n for n in c6 if "grid_trend_bias" in n]
    freed6 = 0
    for name in grid_names:
        reduce = c6[name] * 0.4  # reduce each by 40%
        c6[name] -= reduce
        freed6 += reduce
    # Redistribute to trend_donchian (most robust)
    donchian_names = [n for n in c6 if "trend_donchian" in n]
    if donchian_names:
        per = freed6 / len(donchian_names)
        for name in donchian_names:
            c6[name] += per
    candidates.append(("reduce_grid_boost_trend", c6))

    # Candidate 7: Aggressive — pair trading to 10%, cut fragile
    c7 = dict(base_allocations)
    if "pair_btc_eth" in c7:
        extra_pair = 0.10 - c7["pair_btc_eth"]
        c7["pair_btc_eth"] = 0.10
        # Take from grid_eth
        if "grid_trend_bias_eth" in c7:
            c7["grid_trend_bias_eth"] = max(0.03, c7["grid_trend_bias_eth"] - extra_pair)
    candidates.append(("pair_10pct", c7))

    # Candidate 8: Combo — reduce grid, boost pair + defensive
    c8 = dict(base_allocations)
    # Reduce all grid by 30%
    freed8 = 0
    for name in [n for n in c8 if "grid_trend_bias" in n]:
        r = c8[name] * 0.3
        c8[name] -= r
        freed8 += r
    # Split between pair and tail_risk
    if "pair_btc_eth" in c8:
        c8["pair_btc_eth"] += freed8 * 0.5
    tail_names = [n for n in c8 if "tail_risk" in n]
    if tail_names:
        per = freed8 * 0.5 / len(tail_names)
        for n in tail_names:
            c8[n] += per
    candidates.append(("combo_grid_to_pair_tail", c8))

    return candidates


def main():
    print("=" * 80)
    print("🔬 V7.4 OPTIMIZATION — simulated 2x leverage with MaxDD ≤ -15%")
    print("=" * 80)
    print("  Target: 2.0x leverage, MaxDD ≤ -15%, Sharpe ≥ 2.0")
    print("  Requires base MaxDD ≤ -7.5% (currently -9.2%)")

    # Step 1: Run all strategies
    print(f"\n📊 Step 1: Running all {len(PORTFOLIO)} strategies...")
    results = run_all_strategies()
    print(f"  ✅ {len(results)} strategies loaded")

    # Step 2: Analyze DD contribution
    base_alloc = {name: cfg["allocation"] for name, cfg in PORTFOLIO.items()}
    print("\n📊 Step 2: Analyzing drawdown contributions...")
    dd_df = analyze_dd_contribution(results, base_alloc)
    if len(dd_df) > 0:
        print("\n  Strategy DD Contribution (during worst portfolio DD):")
        print(f"  {'Name':<35} {'Alloc%':>7} {'StratDD%':>10} {'WeightDD%':>10} {'Sharpe':>8}")
        print("  " + "-" * 72)
        for _, row in dd_df.iterrows():
            print(f"  {row['name']:<35} {row['allocation']:>6.1f}% "
                  f"{row['strategy_dd%']:>9.2f}% {row['weighted_dd%']:>9.2f}% "
                  f"{row['sharpe']:>8.3f}")

    # Step 3: Generate and test allocation candidates
    print(f"\n📊 Step 3: Testing {9} allocation candidates...")
    candidates = optimize_allocations(results, base_alloc)

    print(f"\n  {'Config':<30} {'Base':>6} {'Sharpe':>7} {'Ret%':>8} {'BaseDD%':>8} "
          f"{'2xDD%':>7} {'2x$':>8} {'Pass':>6}")
    print("  " + "-" * 85)

    best_passing = None
    all_results = []

    for name, alloc in candidates:
        # Normalize allocations to sum to 1.0
        total = sum(alloc.values())
        if total > 0:
            alloc = {k: v / total for k, v in alloc.items()}

        p = build_portfolio(results, alloc)
        if "error" in p:
            continue

        # Simulate 2x leverage
        lev = simulate_leverage(p["portfolio_curve"], TARGET_LEVERAGE)

        passes = lev["maxdd%"] >= MAX_DD_LIMIT
        tag = "✅" if passes else "❌"

        result_row = {
            "name": name,
            "base_maxdd": round(p["maxdd%"], 2),
            "sharpe_1x": round(p["sharpe"], 3),
            "return_1x": round(p["return%"], 1),
            "sharpe_2x": lev["sharpe"],
            "maxdd_2x": lev["maxdd%"],
            "final_2x": lev["final"],
            "passes": passes,
            "allocations": alloc,
        }
        all_results.append(result_row)

        print(f"  {name:<30} {p['maxdd%']:>5.1f}% {p['sharpe']:>7.3f} "
              f"{p['return%']:>7.1f}% {p['maxdd%']:>7.1f}% "
              f"{lev['maxdd%']:>6.1f}% ${lev['final']:>7.0f} {tag:>5}")

        if passes and (best_passing is None or lev["final"] > best_passing["final_2x"]):
            best_passing = result_row

    # Step 4: Report winner
    print(f"\n{'=' * 80}")
    if best_passing:
        print(f"🏆 WINNER: {best_passing['name']}")
        print(f"   At 2x leverage: Sharpe {best_passing['sharpe_2x']}, "
              f"Return {best_passing['return_1x'] * 2:.0f}%+, "
              f"MaxDD {best_passing['maxdd_2x']}%, "
              f"$150 → ${best_passing['final_2x']:.0f}")
        print("\n   V7.4 Allocation Changes:")
        base = {name: cfg["allocation"] for name, cfg in PORTFOLIO.items()}
        for name in sorted(best_passing["allocations"].keys()):
            old = base.get(name, 0) * 100
            new = best_passing["allocations"][name] * 100
            if abs(old - new) > 0.1:
                arrow = "↑" if new > old else "↓"
                print(f"     {arrow} {name}: {old:.1f}% → {new:.1f}%")
    else:
        print("❌ No configuration achieves MaxDD ≤ -15% at 2x leverage.")
        print("   Closest options:")
        passing_near = sorted(all_results, key=lambda x: abs(x["maxdd_2x"] - MAX_DD_LIMIT))[:3]
        for r in passing_near:
            print(f"     {r['name']}: 2x DD={r['maxdd_2x']}%, $={r['final_2x']}")

    # Save results
    out = ROOT / "backtest_tool" / "reports" / "output" / "v74_leverage_optimization.csv"
    df = pd.DataFrame([{k: v for k, v in r.items() if k != "allocations"} for r in all_results])
    df.to_csv(out, index=False)
    print(f"\n  📁 Results saved to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
