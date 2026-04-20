"""Leverage sensitivity analysis for V7.3 portfolio.

Tests different leverage multipliers on the full portfolio to understand
risk/reward tradeoffs of increasing leverage.

Usage:
    python -m backtest_tool.scripts.leverage_analysis
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from backtest_tool.scripts.full_portfolio_backtest import (
    INITIAL_CAPITAL,
    PORTFOLIO,
    load_data,
    run_single_strategy,
)
from backtest_tool.engine.runner import BacktestRunner

# Leverage multipliers to test
MULTIPLIERS = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]


def run_portfolio_with_leverage_mult(multiplier: float) -> dict:
    """Run full portfolio with all strategy leverages scaled by multiplier."""
    portfolio = copy.deepcopy(PORTFOLIO)
    for cfg in portfolio.values():
        base_lev = cfg["params"].get("leverage", 1)
        cfg["params"]["leverage"] = round(base_lev * multiplier, 2)

    runner = BacktestRunner()
    curves = {}
    details = []

    for name, cfg in portfolio.items():
        result = run_single_strategy(name, cfg, runner)
        if result is None:
            continue
        alloc = cfg["allocation"]
        eq = result["equity"]
        weighted_curve = eq / eq.iloc[0] * alloc
        curves[name] = weighted_curve
        details.append({
            "name": name,
            "leverage": cfg["params"]["leverage"],
            "sharpe": result.get("sharpe", 0),
            "total_return": result.get("return%", 0),
            "max_dd": result.get("maxdd%", 0),
        })

    if not curves:
        return {"error": True}

    # Combine equity curves
    combined = pd.DataFrame(curves)
    combined = combined.ffill().fillna(method="bfill")
    portfolio_curve = combined.sum(axis=1)
    portfolio_value = portfolio_curve * INITIAL_CAPITAL

    # Calculate metrics
    returns = portfolio_curve.pct_change().dropna()
    ann_factor = np.sqrt(252 * 6)  # 4h bars
    sharpe = returns.mean() / returns.std() * ann_factor if returns.std() > 0 else 0
    total_return = (portfolio_value.iloc[-1] / INITIAL_CAPITAL - 1) * 100
    running_max = portfolio_value.cummax()
    drawdown = (portfolio_value - running_max) / running_max
    max_dd = drawdown.min() * 100
    calmar = total_return / abs(max_dd) if max_dd != 0 else 0
    final_value = portfolio_value.iloc[-1]

    return {
        "multiplier": multiplier,
        "sharpe": round(sharpe, 3),
        "ann_return": round(total_return, 1),
        "max_dd": round(max_dd, 1),
        "calmar": round(calmar, 2),
        "final_value": round(final_value, 1),
        "details": details,
    }


def show_current_leverage():
    """Print current leverage settings per strategy."""
    print("\n📊 Current V7.3 Leverage Settings:")
    print(f"{'Strategy':<35} {'Leverage':>8}")
    print("-" * 45)
    for name, cfg in PORTFOLIO.items():
        lev = cfg["params"].get("leverage", 1)
        print(f"  {name:<33} {lev:>6.1f}x")


def main():
    show_current_leverage()

    print(f"\n🔬 Testing {len(MULTIPLIERS)} leverage multipliers...")
    print("=" * 80)

    results = []
    for mult in MULTIPLIERS:
        print(f"\n  Running {mult:.2f}x multiplier...", end=" ", flush=True)
        r = run_portfolio_with_leverage_mult(mult)
        if "error" in r:
            print("❌ FAILED")
            continue
        results.append(r)
        tag = " ← BASELINE" if mult == 1.0 else ""
        print(f"Sharpe {r['sharpe']:.3f} | Return {r['ann_return']:.1f}% | "
              f"MaxDD {r['max_dd']:.1f}% | ${r['final_value']:.0f}{tag}")

    # Summary table
    print("\n" + "=" * 80)
    print("📊 LEVERAGE SENSITIVITY ANALYSIS — V7.3 Portfolio")
    print("=" * 80)
    print(f"{'Mult':>6} {'EffLev':>8} {'Sharpe':>8} {'AnnRet%':>9} {'MaxDD%':>8} "
          f"{'Calmar':>8} {'$Final':>8} {'vs Base':>8}")
    print("-" * 72)

    base_sharpe = results[0]["sharpe"] if results else 1
    base_return = results[0]["ann_return"] if results else 1

    for r in results:
        # Show effective leverage range
        levs = [d["leverage"] for d in r["details"]]
        eff_range = f"{min(levs):.1f}-{max(levs):.1f}"
        delta_sharpe = r["sharpe"] - base_sharpe
        tag = " ★" if r["sharpe"] == max(x["sharpe"] for x in results) else ""
        print(f"{r['multiplier']:>5.2f}x {eff_range:>8} {r['sharpe']:>8.3f} "
              f"{r['ann_return']:>8.1f}% {r['max_dd']:>7.1f}% "
              f"{r['calmar']:>8.2f} ${r['final_value']:>7.0f} "
              f"{delta_sharpe:>+7.3f}{tag}")

    # Analysis
    print("\n📈 Key Takeaways:")
    if len(results) >= 2:
        best = max(results, key=lambda x: x["sharpe"])
        worst_dd = min(results, key=lambda x: x["max_dd"])
        print(f"  • Best Sharpe:  {best['multiplier']:.2f}x → Sharpe {best['sharpe']:.3f}")
        print(f"  • Worst MaxDD:  {worst_dd['multiplier']:.2f}x → MaxDD {worst_dd['max_dd']:.1f}%")
        print(f"  • 15% DD limit: ", end="")
        safe = [r for r in results if r["max_dd"] > -15]
        if safe:
            best_safe = max(safe, key=lambda x: x["ann_return"])
            print(f"Max safe multiplier = {best_safe['multiplier']:.2f}x "
                  f"(Return {best_safe['ann_return']:.1f}%, DD {best_safe['max_dd']:.1f}%)")
        else:
            print("Even 1.0x exceeds -15% DD")

    # Save CSV
    out = ROOT / "backtest_tool" / "reports" / "output" / "leverage_analysis.csv"
    df = pd.DataFrame([{k: v for k, v in r.items() if k != "details"} for r in results])
    df.to_csv(out, index=False)
    print(f"\n  Results saved to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
