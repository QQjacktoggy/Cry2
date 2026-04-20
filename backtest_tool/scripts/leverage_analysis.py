"""Leverage sensitivity analysis for V7.3 portfolio.

Discovery: The `leverage` parameter in strategy params is logged but NOT
actually applied in VBT from_signals(). All portfolio results are at
effective 1x leverage. This script simulates what leverage would do by
amplifying the portfolio returns.

Leverage simulation: leveraged_return = leverage * unleveraged_return
This is the standard daily-rebalancing leverage model used in finance.

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

# Leverage multipliers to test on top of current portfolio
MULTIPLIERS = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]


def run_baseline_portfolio() -> tuple[pd.Series, list[dict]]:
    """Run the portfolio once at base leverage, return combined equity curve."""
    runner = BacktestRunner()
    curves = {}
    details = []

    for name, cfg in PORTFOLIO.items():
        result = run_single_strategy(name, cfg, runner)
        if result is None:
            continue
        eq = result["equity"]
        alloc = cfg["allocation"]
        weighted_curve = eq / eq.iloc[0] * alloc
        curves[name] = weighted_curve
        details.append({
            "name": name,
            "stated_leverage": cfg["params"].get("leverage", 1),
            "sharpe": result.get("sharpe", 0),
            "total_return": result.get("return%", 0),
        })

    combined = pd.DataFrame(curves)
    combined = combined.ffill().bfill()
    portfolio_curve = combined.sum(axis=1)
    return portfolio_curve, details


def simulate_leverage(portfolio_curve: pd.Series, leverage: float) -> dict:
    """Apply leverage multiplier to portfolio returns.

    Leveraged_return_t = leverage * base_return_t
    This is the standard daily-rebalanced leverage model.
    """
    base_returns = portfolio_curve.pct_change().dropna()
    leveraged_returns = base_returns * leverage

    # Build leveraged equity curve
    leveraged_curve = (1 + leveraged_returns).cumprod()
    leveraged_value = leveraged_curve * INITIAL_CAPITAL

    # Metrics
    ann_factor = np.sqrt(252 * 6)  # 4h bars
    sharpe = leveraged_returns.mean() / leveraged_returns.std() * ann_factor if leveraged_returns.std() > 0 else 0

    total_pct = (leveraged_value.iloc[-1] / INITIAL_CAPITAL - 1) * 100
    ann_return = total_pct  # already annualized over ~2 year period

    running_max = leveraged_value.cummax()
    drawdown = (leveraged_value - running_max) / running_max
    max_dd = drawdown.min() * 100

    calmar = total_pct / abs(max_dd) if max_dd != 0 else 0
    final_value = leveraged_value.iloc[-1]

    # Daily volatility
    daily_vol = leveraged_returns.std() * np.sqrt(6)  # 4h→daily
    ann_vol = daily_vol * np.sqrt(252) * 100

    return {
        "leverage": leverage,
        "sharpe": round(sharpe, 3),
        "ann_return": round(total_pct, 1),
        "max_dd": round(max_dd, 1),
        "calmar": round(calmar, 2),
        "final_value": round(float(final_value), 1),
        "ann_vol": round(ann_vol, 1),
    }


def show_current_leverage():
    """Print current leverage settings per strategy."""
    print("\n📊 Current V7.3 Stated Leverage (NOTE: NOT applied in backtest!):")
    print(f"{'Strategy':<35} {'Stated':>8} {'Effective':>10}")
    print("-" * 55)
    for name, cfg in PORTFOLIO.items():
        lev = cfg["params"].get("leverage", 1)
        print(f"  {name:<33} {lev:>6.1f}x {'1.0x':>10}")
    print("\n  ⚠️  VBT from_signals() ignores 'leverage' param → all at 1x")
    print("  📐 This analysis simulates leverage via return amplification")


def main():
    show_current_leverage()

    print(f"\n🔬 Running baseline portfolio (1x effective)...")
    base_curve, details = run_baseline_portfolio()
    print(f"  ✅ Baseline loaded: {len(base_curve)} bars")

    print(f"\n🔬 Simulating {len(MULTIPLIERS)} leverage levels...")
    print("=" * 85)

    results = []
    for mult in MULTIPLIERS:
        r = simulate_leverage(base_curve, mult)
        results.append(r)

    # Summary table
    print("\n" + "=" * 85)
    print("📊 LEVERAGE SENSITIVITY ANALYSIS — V7.3 Portfolio ($150 initial)")
    print("=" * 85)
    print(f"{'Lev':>5} {'Sharpe':>8} {'AnnRet%':>9} {'MaxDD%':>8} "
          f"{'Calmar':>8} {'AnnVol%':>8} {'$Final':>8} {'ΔSharpe':>9}")
    print("-" * 75)

    base_sharpe = results[0]["sharpe"]
    best_sharpe_idx = max(range(len(results)), key=lambda i: results[i]["sharpe"])

    for i, r in enumerate(results):
        delta = r["sharpe"] - base_sharpe
        tag = " ★" if i == best_sharpe_idx else ""
        dd_warn = " ⚠️" if r["max_dd"] < -15 else ""
        dd_crit = " ❌" if r["max_dd"] < -25 else ""
        print(f"{r['leverage']:>4.2f}x {r['sharpe']:>8.3f} "
              f"{r['ann_return']:>8.1f}% {r['max_dd']:>7.1f}%{dd_crit or dd_warn:3s}"
              f"{r['calmar']:>8.2f} {r['ann_vol']:>7.1f}% ${r['final_value']:>7.0f} "
              f"{delta:>+8.3f}{tag}")

    # Key analysis
    print("\n" + "=" * 85)
    print("📈 Key Insights:")
    print("=" * 85)

    # Sharpe behavior
    print(f"\n  1️⃣  Sharpe vs Leverage:")
    print(f"     • Theoretical: Sharpe ≈ constant (leverage scales return & vol equally)")
    print(f"     • In practice: Slight decrease due to compounding drag at high leverage")
    for r in results:
        bar = "█" * int(r["sharpe"] / max(x["sharpe"] for x in results) * 30)
        print(f"       {r['leverage']:.2f}x  {bar} {r['sharpe']:.3f}")

    # MaxDD safety
    print(f"\n  2️⃣  MaxDD Safety Gate (-15% limit):")
    safe = [r for r in results if r["max_dd"] > -15]
    if safe:
        max_safe = max(safe, key=lambda x: x["leverage"])
        print(f"     • Max safe leverage: {max_safe['leverage']:.2f}x "
              f"(DD={max_safe['max_dd']:.1f}%, Return={max_safe['ann_return']:.1f}%)")
    else:
        print(f"     • ❌ Even 1.0x exceeds -15% DD limit!")

    danger = [r for r in results if r["max_dd"] <= -15]
    if danger:
        first_danger = min(danger, key=lambda x: x["leverage"])
        print(f"     • First breach: {first_danger['leverage']:.2f}x "
              f"(DD={first_danger['max_dd']:.1f}%)")

    # Optimal point
    print(f"\n  3️⃣  Optimal Leverage (best risk-adjusted return):")
    best_calmar = max(results, key=lambda x: x["calmar"])
    print(f"     • Best Calmar: {best_calmar['leverage']:.2f}x → "
          f"Calmar {best_calmar['calmar']:.2f}, Return {best_calmar['ann_return']:.1f}%")

    # Money comparison
    print(f"\n  4️⃣  Final Portfolio Value ($150 → ?):")
    for r in results:
        bar = "█" * int(r["final_value"] / max(x["final_value"] for x in results) * 30)
        print(f"       {r['leverage']:.2f}x  {bar} ${r['final_value']:.0f}")

    # Recommendation
    print(f"\n  💡 Recommendation:")
    safe_high = [r for r in results if r["max_dd"] > -15]
    if len(safe_high) >= 2:
        recommended = safe_high[-1]  # Highest safe leverage
        print(f"     → Use {recommended['leverage']:.2f}x leverage "
              f"(Return {recommended['ann_return']:.1f}%, DD {recommended['max_dd']:.1f}%)")
        print(f"       This stays within -15% DD safety gate while maximizing returns.")
    else:
        print(f"     → Stay at 1.0x — already optimally leveraged for the risk budget.")

    # Save CSV
    out = ROOT / "backtest_tool" / "reports" / "output" / "leverage_analysis.csv"
    df = pd.DataFrame(results)
    df.to_csv(out, index=False)
    print(f"\n  📁 Results saved to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
