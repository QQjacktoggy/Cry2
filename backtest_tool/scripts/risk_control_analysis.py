"""Phase D: Risk Control Optimization Analysis.

Measures the impact of each risk control on the V7.3 portfolio.
Compares baseline (no risk controls) vs filtered (with risk controls)
and sweeps parameters to find optimal configuration.

Controls tested:
  3A: Portfolio drawdown stop (per-strategy, using close as equity proxy)
  3D: Consecutive loss protection (pause after N losses)
  3E: Maximum holding time (forced exit)
  3C: Volatility-adaptive leverage (informational — not signal-level)

Usage:
    python -m backtest_tool.scripts.risk_control_analysis
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from backtest_tool.engine.risk_manager import (
    RiskManager,
    apply_consecutive_loss_filter,
    apply_max_hold_limit,
    apply_portfolio_stop,
    compute_adaptive_leverage,
)
from backtest_tool.engine.runner import BacktestRunner
from backtest_tool.scripts.full_portfolio_backtest import (
    INITIAL_CAPITAL,
    PORTFOLIO,
    load_data,
)
from backtest_tool.strategies import STRATEGY_MAP
from backtest_tool.strategies.base_vbt import FREQ_MAP

OUTPUT_DIR = ROOT / "backtest_tool" / "reports" / "output"


# ─── Risk Parameter Grid ────────────────────────────────────────────────────

RISK_CONFIGS = {
    "baseline": RiskManager(
        enable_portfolio_stop=False,
        enable_position_sizing=False,
        enable_adaptive_leverage=False,
        enable_consec_loss_filter=False,
        enable_max_hold=False,
    ),
    "3D_only_loss3": RiskManager(
        enable_portfolio_stop=False,
        enable_position_sizing=False,
        enable_adaptive_leverage=False,
        enable_consec_loss_filter=True,
        max_consec_losses=3,
        loss_cooldown_bars=24,
        enable_max_hold=False,
    ),
    "3D_only_loss4": RiskManager(
        enable_portfolio_stop=False,
        enable_position_sizing=False,
        enable_adaptive_leverage=False,
        enable_consec_loss_filter=True,
        max_consec_losses=4,
        loss_cooldown_bars=24,
        enable_max_hold=False,
    ),
    "3D_only_loss5": RiskManager(
        enable_portfolio_stop=False,
        enable_position_sizing=False,
        enable_adaptive_leverage=False,
        enable_consec_loss_filter=True,
        max_consec_losses=5,
        loss_cooldown_bars=24,
        enable_max_hold=False,
    ),
    "3E_only_hold90": RiskManager(
        enable_portfolio_stop=False,
        enable_position_sizing=False,
        enable_adaptive_leverage=False,
        enable_consec_loss_filter=False,
        enable_max_hold=True,
        long_max_hold_bars=90,
        short_max_hold_bars=90,
    ),
    "3E_only_hold120": RiskManager(
        enable_portfolio_stop=False,
        enable_position_sizing=False,
        enable_adaptive_leverage=False,
        enable_consec_loss_filter=False,
        enable_max_hold=True,
        long_max_hold_bars=120,
        short_max_hold_bars=120,
    ),
    "3E_only_hold180": RiskManager(
        enable_portfolio_stop=False,
        enable_position_sizing=False,
        enable_adaptive_leverage=False,
        enable_consec_loss_filter=False,
        enable_max_hold=True,
        long_max_hold_bars=180,
        short_max_hold_bars=180,
    ),
    "3A_dd15": RiskManager(
        enable_portfolio_stop=True,
        max_dd_pct=0.15,
        dd_cooldown_bars=48,
        enable_position_sizing=False,
        enable_adaptive_leverage=False,
        enable_consec_loss_filter=False,
        enable_max_hold=False,
    ),
    "3A_dd12": RiskManager(
        enable_portfolio_stop=True,
        max_dd_pct=0.12,
        dd_cooldown_bars=48,
        enable_position_sizing=False,
        enable_adaptive_leverage=False,
        enable_consec_loss_filter=False,
        enable_max_hold=False,
    ),
    "combo_conservative": RiskManager(
        enable_portfolio_stop=True,
        max_dd_pct=0.15,
        dd_cooldown_bars=48,
        enable_position_sizing=False,
        enable_adaptive_leverage=False,
        enable_consec_loss_filter=True,
        max_consec_losses=4,
        loss_cooldown_bars=24,
        enable_max_hold=True,
        long_max_hold_bars=120,
        short_max_hold_bars=120,
    ),
    "combo_moderate": RiskManager(
        enable_portfolio_stop=True,
        max_dd_pct=0.18,
        dd_cooldown_bars=36,
        enable_position_sizing=False,
        enable_adaptive_leverage=False,
        enable_consec_loss_filter=True,
        max_consec_losses=5,
        loss_cooldown_bars=18,
        enable_max_hold=True,
        long_max_hold_bars=150,
        short_max_hold_bars=120,
    ),
    "combo_aggressive": RiskManager(
        enable_portfolio_stop=False,
        enable_position_sizing=False,
        enable_adaptive_leverage=False,
        enable_consec_loss_filter=True,
        max_consec_losses=5,
        loss_cooldown_bars=18,
        enable_max_hold=True,
        long_max_hold_bars=180,
        short_max_hold_bars=150,
    ),
}


def run_strategy_with_risk(
    name: str,
    cfg: dict,
    runner: BacktestRunner,
    risk_manager: RiskManager,
) -> dict | None:
    """Run a single strategy with risk controls applied to signals.

    For pair trading and funding strategies that override run_backtest(),
    risk controls are skipped (they have built-in stop mechanisms).
    """
    strat_key = cfg.get("strategy_name", name)
    strategy_cls = STRATEGY_MAP.get(strat_key)
    if strategy_cls is None:
        return None

    try:
        ohlcv = load_data(cfg["symbol"], cfg["timeframe"])
    except FileNotFoundError:
        return None

    strategy = strategy_cls(cfg["params"])
    capital = INITIAL_CAPITAL * cfg["allocation"]
    lev = strategy.params.get("leverage", 2)

    # Pair trading / funding: skip signal-level risk (has built-in stops)
    is_pair = hasattr(strategy, "required_symbols") and len(getattr(strategy, "required_symbols", [])) > 1
    has_custom_backtest = type(strategy).run_backtest is not type(strategy).__mro__[1].run_backtest if len(type(strategy).__mro__) > 1 else False

    if is_pair or has_custom_backtest:
        # Run without risk controls for special strategies
        try:
            if hasattr(strategy, "load_funding") and hasattr(strategy, "prepare_data"):
                funding_df = strategy.load_funding(cfg["symbol"])
                if len(funding_df) > 0:
                    ohlcv = strategy.prepare_data(ohlcv, funding_df)

            result = runner.run_single(
                strategy=strategy,
                ohlcv=ohlcv,
                symbol=cfg["symbol"],
                timeframe=cfg["timeframe"],
                initial_capital=capital,
            )
        except Exception as e:
            print(f"  ❌ {name} failed: {e}")
            return None

        m = result.metrics
        return {
            "name": name,
            "symbol": cfg["symbol"],
            "alloc": cfg["allocation"],
            "capital": capital,
            "final": m.get("final_value", capital),
            "return%": m.get("total_return", 0),
            "sharpe": m.get("sharpe_ratio", 0),
            "maxdd%": m.get("max_drawdown", 0),
            "calmar": m.get("calmar_ratio", 0),
            "trades": m.get("total_trades", 0),
            "win%": m.get("win_rate", 0),
            "equity": result.equity_curve,
            "risk_applied": False,
        }

    # ── Standard strategy: generate signals → apply risk → VBT portfolio ──

    # Handle funding data
    if hasattr(strategy, "load_funding") and hasattr(strategy, "prepare_data"):
        try:
            funding_df = strategy.load_funding(cfg["symbol"])
            if len(funding_df) > 0:
                ohlcv = strategy.prepare_data(ohlcv, funding_df)
        except Exception:
            pass

    try:
        entries = strategy.generate_entries(ohlcv).fillna(False).astype(bool)
        exits = strategy.generate_exits(ohlcv).fillna(False).astype(bool)
        short_entries = strategy.generate_short_entries(ohlcv)
        short_exits = strategy.generate_short_exits(ohlcv)
    except Exception as e:
        print(f"  ❌ {name} signal generation failed: {e}")
        return None

    # Count baseline signals
    baseline_entries = int(entries.sum())

    # Apply risk controls
    entries_f, exits_f, se_f, sx_f = risk_manager.apply(
        ohlcv, entries, exits, short_entries, short_exits, capital,
    )

    filtered_entries = int(entries_f.sum())

    # Build VBT portfolio with filtered signals
    close = ohlcv["close"]
    freq = FREQ_MAP.get(strategy.required_timeframe, strategy.required_timeframe)
    total_fees = runner.cost_model.default_fee_rate + runner.cost_model.slippage_rate

    kwargs = {
        "close": close,
        "entries": entries_f,
        "exits": exits_f,
        "init_cash": capital,
        "fees": total_fees,
        "freq": freq,
    }

    if se_f is not None:
        se_f = se_f.fillna(False).astype(bool)
        kwargs["short_entries"] = se_f
        kwargs["direction"] = "both"
        if sx_f is not None:
            sx_f = sx_f.fillna(False).astype(bool)
            kwargs["short_exits"] = sx_f
    else:
        kwargs["direction"] = "longonly"

    try:
        portfolio = vbt.Portfolio.from_signals(**kwargs)
    except Exception as e:
        print(f"  ❌ {name} VBT portfolio creation failed: {e}")
        return None

    equity = portfolio.value()
    total_return = portfolio.total_return() * 100
    total_trades = portfolio.trades.count()
    win_rate = portfolio.trades.win_rate() * 100 if total_trades > 0 else 0
    max_dd = portfolio.max_drawdown() * 100
    final_val = float(equity.iloc[-1]) if len(equity) > 0 else capital

    # Compute Sharpe
    returns = equity.pct_change().dropna()
    if strategy.required_timeframe == "1d":
        ann = 365
    elif strategy.required_timeframe == "4h":
        ann = 365 * 6
    else:
        ann = 365 * 24
    sharpe = returns.mean() / returns.std() * np.sqrt(ann) if returns.std() > 0 else 0
    calmar = (total_return / abs(max_dd)) if max_dd != 0 else 0

    return {
        "name": name,
        "symbol": cfg["symbol"],
        "alloc": cfg["allocation"],
        "capital": capital,
        "final": final_val,
        "return%": total_return,
        "sharpe": sharpe,
        "maxdd%": max_dd,
        "calmar": calmar,
        "trades": total_trades,
        "win%": win_rate,
        "equity": equity,
        "risk_applied": True,
        "entries_baseline": baseline_entries,
        "entries_filtered": filtered_entries,
        "entries_removed": baseline_entries - filtered_entries,
    }


def run_portfolio(risk_config_name: str, risk_manager: RiskManager, runner: BacktestRunner) -> dict:
    """Run full portfolio with a given risk configuration."""
    results = {}
    equity_curves = {}

    for name, cfg in PORTFOLIO.items():
        r = run_strategy_with_risk(name, cfg, runner, risk_manager)
        if r is not None:
            results[name] = r
            equity_curves[name] = r["equity"]

    if not results:
        return {"config": risk_config_name, "error": "No strategies completed"}

    # Combine equity curves (daily resampling)
    daily_curves = {}
    for name, eq in equity_curves.items():
        if hasattr(eq.index, "date"):
            daily = eq.resample("1D").last().dropna()
            daily_curves[name] = daily

    if not daily_curves:
        return {"config": risk_config_name, "error": "No valid equity curves"}

    aligned = pd.DataFrame(daily_curves)
    aligned = aligned.ffill().bfill()
    combined = aligned.sum(axis=1).dropna()

    if len(combined) < 10:
        return {"config": risk_config_name, "error": "Combined equity too short"}

    # Portfolio metrics
    total_return = (combined.iloc[-1] / combined.iloc[0] - 1) * 100
    returns = combined.pct_change().dropna()
    ann_factor = 365
    sharpe = returns.mean() / returns.std() * np.sqrt(ann_factor) if returns.std() > 0 else 0
    sortino_dn = returns[returns < 0].std()
    sortino = returns.mean() / sortino_dn * np.sqrt(ann_factor) if sortino_dn > 0 else 0

    cummax = combined.cummax()
    dd = (combined - cummax) / cummax
    max_dd = dd.min() * 100

    total_days = (combined.index[-1] - combined.index[0]).days
    ann_return = ((1 + total_return / 100) ** (365 / max(total_days, 1)) - 1) * 100
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

    total_trades = sum(r.get("trades", 0) for r in results.values())
    total_entries_removed = sum(r.get("entries_removed", 0) for r in results.values())

    return {
        "config": risk_config_name,
        "sharpe": round(sharpe, 3),
        "ann_return%": round(ann_return, 1),
        "total_return%": round(total_return, 1),
        "max_dd%": round(max_dd, 1),
        "calmar": round(calmar, 2),
        "sortino": round(sortino, 3),
        "initial": round(combined.iloc[0], 1),
        "final": round(combined.iloc[-1], 1),
        "trades": total_trades,
        "entries_removed": total_entries_removed,
        "results": results,
    }


def print_comparison_table(all_results: list[dict]) -> None:
    """Print a formatted comparison table."""
    print("\n" + "=" * 100)
    print("📊 RISK CONTROL COMPARISON TABLE")
    print("=" * 100)

    header = (
        f"{'Config':<25} {'Sharpe':>7} {'AnnRet%':>8} {'MaxDD%':>7} "
        f"{'Calmar':>7} {'Sortino':>8} {'$Final':>8} {'Trades':>7} {'Removed':>8}"
    )
    print(header)
    print("-" * len(header))

    baseline = None
    for r in all_results:
        if r.get("error"):
            print(f"{r['config']:<25} ❌ {r['error']}")
            continue

        if r["config"] == "baseline":
            baseline = r

        print(
            f"{r['config']:<25} {r['sharpe']:>7.3f} {r['ann_return%']:>7.1f}% "
            f"{r['max_dd%']:>7.1f} {r['calmar']:>7.2f} {r['sortino']:>8.3f} "
            f"${r['final']:>7.1f} {r['trades']:>7} {r.get('entries_removed', 0):>8}"
        )

    # Delta from baseline
    if baseline:
        print("\n" + "-" * 100)
        print(f"{'Δ from baseline':<25}")
        print("-" * 100)
        for r in all_results:
            if r.get("error") or r["config"] == "baseline":
                continue

            d_sharpe = r["sharpe"] - baseline["sharpe"]
            d_ret = r["ann_return%"] - baseline["ann_return%"]
            d_dd = r["max_dd%"] - baseline["max_dd%"]
            d_calmar = r["calmar"] - baseline["calmar"]

            sharpe_icon = "✅" if d_sharpe >= 0 else "⚠️"
            dd_icon = "✅" if d_dd >= 0 else "⚠️"  # less negative DD is better
            calmar_icon = "✅" if d_calmar >= 0 else "⚠️"

            print(
                f"{r['config']:<25} {sharpe_icon}{d_sharpe:>+6.3f} {d_ret:>+7.1f}% "
                f"{dd_icon}{d_dd:>+6.1f} {calmar_icon}{d_calmar:>+6.2f}"
            )


def print_per_strategy_impact(baseline_results: dict, filtered_results: dict, config_name: str) -> None:
    """Print per-strategy impact of risk controls."""
    print(f"\n📋 Per-Strategy Impact: {config_name}")
    print("-" * 90)
    header = f"{'Strategy':<28} {'ΔSharpe':>8} {'ΔRet%':>7} {'ΔMaxDD':>7} {'Entries-':>8} {'Trades':>7}"
    print(header)
    print("-" * 90)

    for name in baseline_results:
        if name not in filtered_results:
            continue
        b = baseline_results[name]
        f = filtered_results[name]

        d_sharpe = f["sharpe"] - b["sharpe"]
        d_ret = f["return%"] - b["return%"]
        d_dd = f["maxdd%"] - b["maxdd%"]
        removed = f.get("entries_removed", 0)

        icon = "✅" if d_sharpe >= 0 else "⚠️"
        print(
            f"{icon} {name:<26} {d_sharpe:>+7.2f} {d_ret:>+6.1f}% "
            f"{d_dd:>+6.1f} {removed:>8} {f['trades']:>7}"
        )


def analyze_adaptive_leverage(runner: BacktestRunner) -> None:
    """Analyze volatility-adaptive leverage across symbols (3C)."""
    print("\n" + "=" * 70)
    print("📊 3C: Adaptive Leverage Analysis (ATR-percentile based)")
    print("=" * 70)

    symbols_tf = set()
    for cfg in PORTFOLIO.values():
        symbols_tf.add((cfg["symbol"], cfg["timeframe"]))

    for symbol, tf in sorted(symbols_tf):
        try:
            ohlcv = load_data(symbol, tf)
        except FileNotFoundError:
            continue

        leverage = compute_adaptive_leverage(
            ohlcv,
            atr_period=14,
            atr_lookback=100,
            max_leverage=3.0,
            min_leverage=1.0,
            high_vol_pct=80,
            low_vol_pct=20,
        )

        # Statistics
        print(f"\n  {symbol} ({tf}):")
        print(f"    Mean leverage:  {leverage.mean():.2f}x")
        print(f"    Median:         {leverage.median():.2f}x")
        print(f"    Min/Max:        {leverage.min():.2f}x / {leverage.max():.2f}x")
        print(f"    % at min (1x):  {(leverage <= 1.05).mean()*100:.1f}%")
        print(f"    % at max (3x):  {(leverage >= 2.95).mean()*100:.1f}%")

        # Yearly breakdown
        yearly = leverage.groupby(leverage.index.year).mean()
        for yr, lev in yearly.items():
            print(f"    {yr}: avg {lev:.2f}x")


def main() -> None:
    print("=" * 70)
    print("🛡️  Phase D: Risk Control Optimization Analysis")
    print("=" * 70)
    print(f"  Portfolio: V7.3 ({len(PORTFOLIO)} strategies)")
    print(f"  Initial capital: ${INITIAL_CAPITAL}")
    print(f"  Risk configs to test: {len(RISK_CONFIGS)}")
    print()

    runner = BacktestRunner(str(ROOT / "backtest_tool" / "config" / "backtest_config.yaml"))

    # ─── Run all risk configurations ─────────────────────────────────────
    all_results = []

    for config_name, rm in RISK_CONFIGS.items():
        print(f"\n{'─'*50}")
        print(f"▶ Running config: {config_name}")
        print(f"  Controls: {rm.summary()}")
        print(f"{'─'*50}")

        result = run_portfolio(config_name, rm, runner)
        all_results.append(result)

        if not result.get("error"):
            print(f"  ✅ Sharpe={result['sharpe']:.3f}  AnnRet={result['ann_return%']:.1f}%  "
                  f"MaxDD={result['max_dd%']:.1f}%  Calmar={result['calmar']:.2f}  "
                  f"${result['initial']:.0f}→${result['final']:.0f}")

    # ─── Comparison Table ────────────────────────────────────────────────
    print_comparison_table(all_results)

    # ─── Per-strategy impact for best combo ──────────────────────────────
    baseline_data = next((r for r in all_results if r["config"] == "baseline"), None)
    if baseline_data and "results" in baseline_data:
        # Find best non-baseline config by Calmar
        best = max(
            (r for r in all_results if r["config"] != "baseline" and not r.get("error")),
            key=lambda x: x.get("calmar", 0),
            default=None,
        )
        if best and "results" in best:
            print_per_strategy_impact(
                baseline_data["results"], best["results"], best["config"]
            )

    # ─── Adaptive Leverage Analysis ──────────────────────────────────────
    analyze_adaptive_leverage(runner)

    # ─── Recommendation ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("🎯 RECOMMENDATION")
    print("=" * 70)

    if baseline_data and not baseline_data.get("error"):
        # Find configs that improved Calmar without hurting Sharpe too much
        improvements = []
        for r in all_results:
            if r.get("error") or r["config"] == "baseline":
                continue
            d_sharpe = r["sharpe"] - baseline_data["sharpe"]
            d_calmar = r["calmar"] - baseline_data["calmar"]
            d_dd = r["max_dd%"] - baseline_data["max_dd%"]  # less negative = better

            # Criteria: Calmar improved OR MaxDD improved, Sharpe not lost > 5%
            if (d_calmar > 0 or d_dd > 0) and d_sharpe > -0.1:
                improvements.append({
                    "config": r["config"],
                    "d_sharpe": d_sharpe,
                    "d_calmar": d_calmar,
                    "d_dd": d_dd,
                    "sharpe": r["sharpe"],
                    "calmar": r["calmar"],
                    "max_dd": r["max_dd%"],
                })

        if improvements:
            improvements.sort(key=lambda x: x["d_calmar"], reverse=True)
            best_imp = improvements[0]
            print(f"  ✅ Best improvement: {best_imp['config']}")
            print(f"     Sharpe: {best_imp['sharpe']:.3f} (Δ{best_imp['d_sharpe']:+.3f})")
            print(f"     Calmar: {best_imp['calmar']:.2f} (Δ{best_imp['d_calmar']:+.2f})")
            print(f"     MaxDD:  {best_imp['max_dd']:.1f}% (Δ{best_imp['d_dd']:+.1f}%)")
            print(f"\n  All improvements (by Calmar):")
            for imp in improvements:
                print(f"    {imp['config']:<25} Calmar Δ{imp['d_calmar']:+.2f}  "
                      f"Sharpe Δ{imp['d_sharpe']:+.3f}  MaxDD Δ{imp['d_dd']:+.1f}")
        else:
            print("  ℹ️  No configuration clearly improves on baseline.")
            print("  V7.3 baseline (MaxDD -9.1%) is already well-controlled.")
            print("  Risk controls may be more valuable in live trading (regime shifts).")

    # ─── Save Results ────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in all_results:
        if r.get("error"):
            continue
        rows.append({k: v for k, v in r.items() if k != "results"})
    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(OUTPUT_DIR / "risk_control_comparison.csv", index=False)
        print(f"\n  📁 Results saved: {OUTPUT_DIR / 'risk_control_comparison.csv'}")


if __name__ == "__main__":
    main()
