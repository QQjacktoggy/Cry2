# ruff: noqa: E402
"""Full portfolio backtest + Walk-Forward validation with real Binance data.

Runs all 17 strategies from the V7.4 optimized allocation, applies the
explicit leverage model chosen in leverage_analysis.py, combines equity
curves, and performs robustness checks (walk-forward, Monte Carlo, stress
test).

Supports optional risk controls (Phase D):
    --risk   Enable Phase D risk controls (3D consecutive loss + 3E max hold)

Usage:
    python -m backtest_tool.scripts.full_portfolio_backtest
    python -m backtest_tool.scripts.full_portfolio_backtest --risk
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add project root to path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from backtest_tool.engine.analytics import (
    compute_correlation_matrix,
    compute_correlation_summary,
    compute_strategy_health_report,
    find_low_correlation_pairs,
)
from backtest_tool.engine.leverage import (
    annualization_factor_for_timeframe,
    apply_leverage_to_equity,
    summarize_equity_curve,
)
from backtest_tool.engine.regime import MarketRegimeDetector, RegimeConfig
from backtest_tool.engine.risk_manager import RiskManager
from backtest_tool.engine.robustness import (
    block_bootstrap_simulation,
    fee_sensitivity_analysis,
    monte_carlo_simulation,
    walk_forward_analysis,
    walk_forward_nested_analysis,
)
from backtest_tool.engine.runner import BacktestRunner
from backtest_tool.strategies import STRATEGY_MAP
from backtest_tool.strategies.base_vbt import FREQ_MAP
from bot.strategy.bridge import V74_CONFIG

DATA_DIR = ROOT / "backtest_tool" / "data" / "klines"
OUTPUT_DIR = ROOT / "backtest_tool" / "reports" / "output"

INITIAL_CAPITAL = 150  # USDT

# ─── Phase D: Optimal Risk Configuration ────────────────────────────────────
# Calibrated via risk_control_analysis.py sweep of 15 configurations.
# Winner: 3D (consecutive loss=4, cooldown=24) + 3E (max hold=180 bars)
# Impact: Sharpe 2.437→2.476, MaxDD -9.1%→-8.9%, Calmar 4.87→5.07
RISK_MANAGER = RiskManager(
    enable_portfolio_stop=False,       # DD stop hurts returns (close ≠ equity)
    enable_position_sizing=False,      # Uniform allocation preferred
    enable_adaptive_leverage=False,    # Strategy-level leverage is already tuned
    enable_consec_loss_filter=True,    # 3D: pause after 4 consecutive losses
    max_consec_losses=4,
    loss_cooldown_bars=24,             # ~4 days at 4h
    enable_max_hold=True,              # 3E: forced exit after 180 bars
    long_max_hold_bars=180,            # ~30 days at 4h
    short_max_hold_bars=150,           # ~25 days at 4h
)

# ─── Strategy + Allocation Config ───────────────────────────────────────────

def _build_live_aligned_v74_portfolio() -> dict[str, dict]:
    """Build the backtest V7.4 portfolio from the live bridge source of truth."""
    portfolio: dict[str, dict] = {}
    for strategy_name, symbol, timeframe, allocation, params in V74_CONFIG:
        key = f"{strategy_name}_{symbol.replace('USDT', '').lower()}"
        portfolio[key] = {
            "strategy_name": strategy_name,
            "allocation": allocation,
            "symbol": symbol,
            "timeframe": timeframe,
            "params": dict(params),
        }
    return portfolio


PORTFOLIO = _build_live_aligned_v74_portfolio()


def load_data(symbol: str, timeframe: str) -> pd.DataFrame:
    """Load and concatenate all parquet files for a symbol/timeframe."""
    data_path = DATA_DIR / symbol / timeframe
    if not data_path.exists():
        raise FileNotFoundError(f"No data at {data_path}")

    dfs = []
    for f in sorted(data_path.glob("*.parquet")):
        df = pd.read_parquet(f)
        dfs.append(df)

    if not dfs:
        raise FileNotFoundError(f"No parquet files in {data_path}")

    combined = pd.concat(dfs, ignore_index=True)

    # Convert epoch ms timestamp to DatetimeIndex
    if "timestamp" in combined.columns:
        combined["timestamp"] = pd.to_datetime(combined["timestamp"], unit="ms")
        combined = combined.set_index("timestamp")
    elif not isinstance(combined.index, pd.DatetimeIndex):
        combined.index = pd.to_datetime(combined.index)

    combined = combined.sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]

    # Ensure standard column names
    col_map = {}
    for col in combined.columns:
        lower = col.lower()
        if lower in ("open", "high", "low", "close", "volume"):
            col_map[col] = lower
    if col_map:
        combined = combined.rename(columns=col_map)

    return combined


def run_single_strategy(
    name: str,
    cfg: dict,
    runner: BacktestRunner,
    risk_manager: RiskManager | None = None,
    apply_leverage_model: bool = False,
) -> dict | None:
    """Run a single strategy and return result dict.

    Args:
        name: Strategy position name.
        cfg: Strategy config dict (from PORTFOLIO).
        runner: BacktestRunner instance.
        risk_manager: Optional Phase D risk manager. When provided,
            signal-level controls (3D, 3E) are applied before VBT
            portfolio creation.  Pair trading and funding strategies
            are skipped (they have built-in stop mechanisms).
        apply_leverage_model: When True, apply the explicit return-amplification
            leverage model to the resulting equity curve.
    """
    # Support strategy_name override for multi-symbol entries
    strat_key = cfg.get("strategy_name", name)
    strategy_cls = STRATEGY_MAP.get(strat_key)
    if strategy_cls is None:
        print(f"  ⚠️  Strategy '{strat_key}' not in STRATEGY_MAP, skipping")
        return None

    try:
        ohlcv = load_data(cfg["symbol"], cfg["timeframe"])
    except FileNotFoundError as e:
        print(f"  ⚠️  {e}")
        return None

    strategy = strategy_cls(cfg["params"])
    capital = INITIAL_CAPITAL * cfg["allocation"]

    # Auto-merge funding data for funding-aware strategies
    if hasattr(strategy, "load_funding") and hasattr(strategy, "prepare_data"):
        funding_df = strategy.load_funding(cfg["symbol"])
        if len(funding_df) > 0:
            ohlcv = strategy.prepare_data(ohlcv, funding_df)

    # Pair trading strategies load data internally via run_backtest
    is_pair = hasattr(strategy, "required_symbols") and len(getattr(strategy, "required_symbols", [])) > 1
    has_custom_bt = type(strategy).run_backtest is not type(strategy).__mro__[1].run_backtest if len(type(strategy).__mro__) > 1 else False

    # ── Risk-controlled path (standard strategies only) ──────────────
    if risk_manager is not None and not is_pair and not has_custom_bt:
        try:
            entries = strategy.generate_entries(ohlcv).fillna(False).astype(bool)
            exits = strategy.generate_exits(ohlcv).fillna(False).astype(bool)
            short_entries = strategy.generate_short_entries(ohlcv)
            short_exits = strategy.generate_short_exits(ohlcv)

            entries, exits, short_entries, short_exits = risk_manager.apply(
                ohlcv, entries, exits, short_entries, short_exits, capital,
            )

            close = ohlcv["close"]
            freq = FREQ_MAP.get(strategy.required_timeframe, strategy.required_timeframe)
            total_fees = runner.cost_model.default_fee_rate + runner.cost_model.slippage_rate

            leverage = cfg["params"].get("leverage", 1.0)
            kwargs: dict = {
                "close": close,
                "entries": entries,
                "exits": exits,
                "init_cash": capital,
                "fees": total_fees,
                "freq": freq,
                "size": leverage,
                "size_type": "percent",
                "upon_opposite_entry": "close",
            }
            if short_entries is not None:
                kwargs["short_entries"] = short_entries.fillna(False).astype(bool)
                if short_exits is not None:
                    kwargs["short_exits"] = short_exits.fillna(False).astype(bool)
                kwargs["direction"] = "both"
            else:
                kwargs["direction"] = "longonly"

            import vectorbt as vbt
            portfolio = vbt.Portfolio.from_signals(**kwargs)
            equity = portfolio.value()
            leverage = cfg["params"].get("leverage", 1.0)
            if apply_leverage_model:
                equity = apply_leverage_to_equity(equity, leverage, initial_capital=capital)

            total_trades = portfolio.trades.count()
            win_rate = portfolio.trades.win_rate() * 100 if total_trades > 0 else 0
            summary = summarize_equity_curve(
                equity,
                annualization_factor=annualization_factor_for_timeframe(strategy.required_timeframe),
            )

            return {
                "name": name,
                "symbol": cfg["symbol"],
                "tf": cfg["timeframe"],
                "alloc": cfg["allocation"],
                "capital": capital,
                "final": summary["final_value"],
                "return%": summary["total_return"],
                "sharpe": summary["sharpe_ratio"],
                "sortino": summary["sortino_ratio"],
                "maxdd%": summary["max_drawdown"],
                "calmar": summary["calmar_ratio"],
                "trades": total_trades,
                "win%": win_rate,
                "equity": equity,
            }
        except Exception as e:
            print(f"  ⚠️  Risk-controlled run failed for {name}: {e}, falling back")

    # ── Standard path (no risk controls, or pair/funding strategies) ──
    try:
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

    equity = result.equity_curve
    leverage = cfg["params"].get("leverage", 1.0)
    if apply_leverage_model:
        equity = apply_leverage_to_equity(equity, leverage, initial_capital=capital)
        summary = summarize_equity_curve(
            equity,
            annualization_factor=annualization_factor_for_timeframe(cfg["timeframe"]),
        )
        final_value = summary["final_value"]
        total_return = summary["total_return"]
        sharpe = summary["sharpe_ratio"]
        sortino = summary["sortino_ratio"]
        max_drawdown = summary["max_drawdown"]
        calmar = summary["calmar_ratio"]
    else:
        m = result.metrics
        final_value = m.get("final_value", capital)
        total_return = m.get("total_return", 0)
        sharpe = m.get("sharpe_ratio", 0)
        sortino = m.get("sortino_ratio", 0)
        max_drawdown = m.get("max_drawdown", 0)
        calmar = m.get("calmar_ratio", 0)

    m = result.metrics
    return {
        "name": name,
        "symbol": cfg["symbol"],
        "tf": cfg["timeframe"],
        "alloc": cfg["allocation"],
        "capital": capital,
        "final": final_value,
        "return%": total_return,
        "sharpe": sharpe,
        "sortino": sortino,
        "maxdd%": max_drawdown,
        "calmar": calmar,
        "trades": m.get("total_trades", 0),
        "win%": m.get("win_rate", 0),
        "equity": equity,
    }


def main() -> None:
    """Run the full portfolio backtest."""
    use_risk = "--risk" in sys.argv

    print("=" * 70)
    print("🚀 Cry2 Full Portfolio Backtest + Walk-Forward Validation")
    if use_risk:
        print("🛡️  Phase D Risk Controls ENABLED (3D consec_loss=4 + 3E max_hold=180)")
    print("=" * 70)

    runner = BacktestRunner(str(ROOT / "backtest_tool" / "config" / "backtest_config.yaml"))
    rm = RISK_MANAGER if use_risk else None

    # ─── Run each strategy ──────────────────────────────────────────────
    print("\n📊 Running individual strategies...")
    results = {}
    equity_curves = {}

    for name, cfg in PORTFOLIO.items():
        print(f"  ▶ {name} ({cfg['symbol']} {cfg['timeframe']} alloc={cfg['allocation']:.0%})")
        r = run_single_strategy(
            name,
            cfg,
            runner,
            risk_manager=rm,
            apply_leverage_model=True,
        )
        if r is not None:
            results[name] = r
            equity_curves[name] = r["equity"]

    if not results:
        print("❌ No strategies completed. Check data availability.")
        return

    # ─── Print individual results ───────────────────────────────────────
    print("\n" + "=" * 70)
    print("📈 Individual Strategy Results")
    print("=" * 70)
    header = f"{'Strategy':<28} {'Symbol':<10} {'Alloc':>5} {'Return%':>8} {'Sharpe':>7} {'MaxDD%':>7} {'Calmar':>7} {'Trades':>6} {'Win%':>6}"
    print(header)
    print("-" * len(header))

    for name, r in results.items():
        print(
            f"{r['name']:<28} {r['symbol']:<10} {r['alloc']:>4.0%} "
            f"{r['return%']:>7.1f} {r['sharpe']:>7.2f} {r['maxdd%']:>7.1f} "
            f"{r['calmar']:>7.2f} {r['trades']:>6.0f} {r['win%']:>5.1f}"
        )

    # ─── Combine equity curves ──────────────────────────────────────────
    print("\n📊 Combining portfolio equity...")

    # Resample all equity curves to daily frequency for fair combination
    daily_curves = {}
    for name, eq in equity_curves.items():
        if hasattr(eq.index, 'date'):
            daily = eq.resample("1D").last().dropna()
            daily_curves[name] = daily

    if not daily_curves:
        print("❌ No equity curves with DatetimeIndex. Cannot combine.")
        return

    aligned = pd.DataFrame(daily_curves)
    aligned = aligned.ffill().bfill()
    combined_equity = aligned.sum(axis=1)
    combined_equity = combined_equity.dropna()

    if len(combined_equity) < 10:
        print("❌ Combined equity too short for analysis.")
        return

    total_return = (combined_equity.iloc[-1] / combined_equity.iloc[0] - 1) * 100
    returns = combined_equity.pct_change().dropna()

    # Daily frequency → annualization factor = 365 (crypto)
    ann_factor = 365

    sharpe = returns.mean() / returns.std() * np.sqrt(ann_factor) if returns.std() > 0 else 0
    sortino_dn = returns[returns < 0].std()
    sortino = returns.mean() / sortino_dn * np.sqrt(ann_factor) if sortino_dn > 0 else 0

    cummax = combined_equity.cummax()
    dd = (combined_equity - cummax) / cummax
    max_dd = dd.min() * 100

    total_days = (combined_equity.index[-1] - combined_equity.index[0]).days
    ann_return = ((1 + total_return / 100) ** (365 / max(total_days, 1)) - 1) * 100
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

    print("\n" + "=" * 70)
    print("💰 PORTFOLIO SUMMARY")
    print("=" * 70)
    print(f"  Initial Capital:    ${combined_equity.iloc[0]:,.0f}")
    print(f"  Final Value:        ${combined_equity.iloc[-1]:,.0f}")
    print(f"  Total Return:       {total_return:+.1f}%")
    print(f"  Annualized Return:  {ann_return:+.1f}%")
    print(f"  Sharpe Ratio:       {sharpe:.3f}")
    print(f"  Sortino Ratio:      {sortino:.3f}")
    print(f"  Max Drawdown:       {max_dd:.1f}%")
    print(f"  Calmar Ratio:       {calmar:.2f}")
    print(f"  Period:             {combined_equity.index[0].date()} → {combined_equity.index[-1].date()}")
    print(f"  Bars:               {len(combined_equity)}")

    # ─── Regime Analysis ────────────────────────────────────────────────
    print("\n📊 Market Regime Analysis...")
    try:
        btc_4h = load_data("BTCUSDT", "4h")
        detector = MarketRegimeDetector(RegimeConfig())
        regimes = detector.detect(btc_4h)
        regime_summary = detector.regime_summary(regimes)
        print("  BTC 4h Regime Distribution:")
        for k, v in regime_summary.items():
            print(f"    {k}: {v['pct']:.1f}% ({v['count']} bars, longest streak {v['longest_streak']})")
    except Exception as e:
        print(f"  ⚠️  Regime analysis failed: {e}")

    # ─── Correlation Analysis ───────────────────────────────────────────
    print("\n📊 Strategy Correlation Analysis...")
    # Use daily-resampled curves to ensure consistent frequency across 4h/1d strategies
    corr = compute_correlation_matrix(daily_curves if daily_curves else equity_curves)
    summary = compute_correlation_summary(corr)
    print(f"  Mean correlation: {summary.get('mean_correlation', 'N/A')}")
    low_pairs = find_low_correlation_pairs(corr, threshold=0.3)
    print(f"  Low-corr pairs (< 0.3): {len(low_pairs)}")
    if low_pairs:
        for p in low_pairs[:5]:
            print(f"    {p['strategy_a']} ↔ {p['strategy_b']}: {p['correlation']:.3f}")

    # ─── Strategy Health ────────────────────────────────────────────────
    print("\n📊 Strategy Health Report...")
    # Use daily-resampled curves so window_short/long are actual days, not bars
    health = compute_strategy_health_report(
        daily_curves if daily_curves else equity_curves,
        window_short=30,
        window_long=90,
        annualization_factor=365.0,
    )
    for name, h in health.items():
        decay_flag = " ⚠️DECAY" if h.get("is_decaying") else ""
        print(f"  {name}: Sharpe30d={h.get('sharpe_30d', '?'):.2f} Sharpe90d={h.get('sharpe_90d', '?'):.2f} DD={h.get('current_drawdown', 0):.1%}{decay_flag}")

    # ─── Walk-Forward Analysis ──────────────────────────────────────────
    # Use ~6-month train + ~1.5-month test windows (in hourly bars for combined)
    print("\n" + "=" * 70)
    print("🔍 Walk-Forward Analysis (combined portfolio)")
    print("=" * 70)

    # Walk-forward on daily combined equity: ~365d train, ~90d test
    wf_train = min(365, len(combined_equity) // 3)
    wf_test = min(90, wf_train // 4)
    wf_step = wf_test

    wf = walk_forward_analysis(
        combined_equity,
        train_bars=wf_train,
        test_bars=wf_test,
        step_bars=wf_step,
        annualization_factor=ann_factor,
    )

    if wf.get("windows"):
        print(f"  Windows: {len(wf['windows'])}")
        print(f"  {'Window':<8} {'IS Sharpe':>10} {'OOS Sharpe':>11} {'OOS Ret%':>9} {'OOS DD%':>8}")
        print(f"  {'-'*47}")
        for w in wf["windows"]:
            print(f"  W{w['id']:<7} {w['IS_sharpe']:>10.3f} {w['OOS_sharpe']:>11.3f} {w['OOS_return%']:>8.1f} {w['OOS_maxDD%']:>8.1f}")

        s = wf["summary"]
        print(f"\n  Avg OOS Sharpe:     {s['avg_oos_sharpe']:.3f}")
        print(f"  Min OOS Sharpe:     {s['min_oos_sharpe']:.3f}")
        print(f"  Efficiency (OOS/IS): {s['efficiency_ratio']:.3f}")
        print(f"  % Positive OOS:     {s['pct_positive_oos']:.0f}%")
        print(f"  PASSED:             {'✅ YES' if wf['passed'] else '❌ NO'}")
    else:
        print("  ⚠️  Insufficient data for walk-forward analysis")

    # ─── Nested Walk-Forward (per-window re-weight) ────────────────────
    print("\n" + "=" * 70)
    print("🔍 Nested Walk-Forward (per-window IS-Sharpe reweighting)")
    print("=" * 70)
    if daily_curves:
        for scheme in ("equal", "sharpe_pos"):
            nwf = walk_forward_nested_analysis(
                daily_curves,
                train_bars=min(365, len(combined_equity) // 3),
                test_bars=min(90, len(combined_equity) // 12),
                step_bars=min(90, len(combined_equity) // 12),
                annualization_factor=ann_factor,
                weighting=scheme,
            )
            s = nwf.get("summary", {})
            if "error" not in s:
                print(f"  [{scheme:>10}] avg_oos={s.get('avg_oos_sharpe','?'):.3f}  "
                      f"min={s.get('min_oos_sharpe','?'):.3f}  "
                      f"pos%={s.get('pct_positive_oos','?'):.1f}  "
                      f"n_win={s.get('n_windows','?')}")

    # ─── Monte Carlo ────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("🎲 Monte Carlo Simulation (1000 runs each; i.i.d. + block bootstrap)")
    print("=" * 70)
    mc = monte_carlo_simulation(combined_equity, n_simulations=1000, annualization_factor=ann_factor, method="bootstrap")
    if "original" in mc:
        o = mc["original"]
        s = mc["simulation"]
        print(f"  Original:  Final={o['final_value']:.4f}  Sharpe={o['sharpe']:.3f}  MaxDD={o['max_drawdown']:.1f}%")
        print(f"  Sim Mean:  Final={s['final_value']['mean']:.4f}  Sharpe={s['sharpe']['mean']:.3f}  MaxDD={s['max_drawdown']['mean']:.1f}%")
        print(f"  Sim P5:    Final={s['final_value']['p5']:.4f}  Sharpe={s['sharpe']['p5']:.3f}  MaxDD(P95)={s['max_drawdown']['p95']:.1f}%")
        print(f"  Sim P95:   Final={s['final_value']['p95']:.4f}  Sharpe={s['sharpe']['p95']:.3f}")
        pr = mc["percentile_rank"]
        print(f"  Percentile Rank: Final={pr['final_value']:.0f}%  MaxDD={pr['max_drawdown']:.0f}%")

    bb = block_bootstrap_simulation(combined_equity, n_simulations=1000, block_size=5, annualization_factor=ann_factor)
    if "simulation" in bb:
        s = bb["simulation"]
        print(f"  [block=5d] Sim P5/P95 Sharpe: {s['sharpe']['p5']:.3f} / {s['sharpe']['p95']:.3f}  "
              f"Final P5/P95: {s['final_value']['p5']:.3f}x / {s['final_value']['p95']:.3f}x  "
              f"MaxDD P5: {s['max_drawdown']['p5']:.2f}%")

    # ─── Fee Sensitivity ────────────────────────────────────────────────
    print("\n📊 Fee Sensitivity Analysis...")
    total_trades = sum(r.get("trades", 0) for r in results.values())
    fs = fee_sensitivity_analysis(combined_equity, n_trades=max(int(total_trades), 1), base_fee_bps=6.0)
    print(f"  Total trades: {total_trades}")
    print(f"  Breakeven fee: {fs.get('breakeven_fee_bps', 'N/A')} bps")
    print(f"  Safety margin: {fs.get('safety_margin', 'N/A')}x")
    print(f"  Assessment: {fs.get('assessment', 'N/A')}")

    # ─── Save Results ───────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Individual results CSV
    rows = []
    for name, r in results.items():
        rows.append({
            "strategy": r["name"],
            "symbol": r["symbol"],
            "timeframe": r["tf"],
            "allocation": r["alloc"],
            "capital": r["capital"],
            "final_value": r["final"],
            "return_pct": r["return%"],
            "sharpe": r["sharpe"],
            "sortino": r["sortino"],
            "max_dd_pct": r["maxdd%"],
            "calmar": r["calmar"],
            "trades": r["trades"],
            "win_rate": r["win%"],
        })
    pd.DataFrame(rows).to_csv(OUTPUT_DIR / "portfolio_backtest_results.csv", index=False)

    # Combined equity
    combined_equity.to_csv(OUTPUT_DIR / "portfolio_combined_equity.csv", header=["equity"])

    print("\n" + "=" * 70)
    print("✅ Results saved to backtest_tool/reports/output/")
    print("  • portfolio_backtest_results.csv")
    print("  • portfolio_combined_equity.csv")
    print("=" * 70)

    # ─── Final Verdict ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("🏁 FINAL VERDICT")
    print("=" * 70)
    checks = {
        "Sharpe > 0.8": sharpe > 0.8,
        "Ann Return > 25%": ann_return > 25,
        "MaxDD < 20%": max_dd > -20,
        "WF Passed": wf.get("passed", False),
        "Fee Safe": fs.get("assessment") in ("SAFE", "OK"),
    }
    for check, passed in checks.items():
        icon = "✅" if passed else "❌"
        print(f"  {icon} {check}")
    pass_count = sum(checks.values())
    total_checks = len(checks)
    print(f"\n  Result: {pass_count}/{total_checks} checks passed")
    if pass_count == total_checks:
        print("  🎉 DEPLOYABLE — all checks passed!")
    elif pass_count >= 3:
        print("  ⚡ CONDITIONAL — most checks passed, review failures")
    else:
        print("  ⚠️  NEEDS WORK — significant issues to address")


if __name__ == "__main__":
    main()
