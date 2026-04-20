"""Full portfolio backtest + Walk-Forward validation with real Binance data.

Runs all 10 strategies from the optimized allocation, combines equity curves,
and performs robustness checks (walk-forward, Monte Carlo, stress test).

Usage:
    python -m backtest_tool.scripts.full_portfolio_backtest
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
from backtest_tool.engine.regime import MarketRegimeDetector, RegimeConfig
from backtest_tool.engine.robustness import (
    block_bootstrap_simulation,
    fee_sensitivity_analysis,
    generate_robustness_report,
    monte_carlo_simulation,
    walk_forward_analysis,
    walk_forward_nested_analysis,
)
from backtest_tool.engine.runner import BacktestRunner
from backtest_tool.strategies import STRATEGY_MAP

DATA_DIR = ROOT / "backtest_tool" / "data" / "klines"
OUTPUT_DIR = ROOT / "backtest_tool" / "reports" / "output"

INITIAL_CAPITAL = 150  # USDT

# ─── Strategy + Allocation Config ───────────────────────────────────────────

PORTFOLIO = {
    # ═══════════════════════════════════════════════════════════
    # V7.3 ALLOCATION — Phase G Integrated (G4 + G5 new strategies)
    # Philosophy: Parameter stability > Raw Sharpe + Market-neutral diversification
    # Changes from V7.2:
    #   - 🆕 pair_btc_eth 5% (G5 market-neutral, Sharpe 1.334, corr≈0.025)
    #   - 🆕 funding_reversal_eth 3% (G4 gap filler, Sharpe 0.934)
    #   - ❌ REMOVED breakout_squeeze BTC (fragile, low conviction)
    #   - ↓ grid_trend_bias ETH 9→7%, momentum_ranking ETH 12→10%
    #   - ↓ grid_trend_bias BTC 4→3%
    # Tier structure:
    #   - ⭐ Robust tier (40%)  — trend_donchian family
    #   - 🔵 Moderate tier (45%) — momentum / tail_risk / mid-grid
    #   - ⚠️ Fragile tier (7%) — grid_trend_bias ETH (high Sharpe but sensitive)
    #   - 🟣 Market Neutral + Gap (8%) — pair trading + funding reversal
    # Result: 17 strategy positions, 5 coins, 4 strategy families + 2 new
    # ═══════════════════════════════════════════════════════════

    # ── ⭐ ROBUST TIER — 40% (trend_donchian family; mixed viable%) ──────
    "trend_donchian_mtf_btc": {
        "strategy_name": "trend_donchian_mtf",
        "allocation": 0.16,
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "params": {"entry_period": 10, "exit_period": 10, "adx_threshold": 15, "htf_period": 150, "leverage": 2},
    },
    "trend_donchian_adx_slope_eth": {
        "strategy_name": "trend_donchian_adx_slope",
        "allocation": 0.10,
        "symbol": "ETHUSDT",
        "timeframe": "4h",
        "params": {"entry_period": 20, "exit_period": 5, "adx_slope_bars": 5, "adx_slope_min": 0.2, "leverage": 2},
    },
    "trend_donchian_adx_slope_btc": {
        "strategy_name": "trend_donchian_adx_slope",
        "allocation": 0.08,
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "params": {"entry_period": 30, "exit_period": 7, "adx_slope_bars": 3, "adx_slope_min": 0.2, "leverage": 2},
    },
    "trend_donchian_mtf_xrp": {
        "strategy_name": "trend_donchian_mtf",
        "allocation": 0.04,
        "symbol": "XRPUSDT",
        "timeframe": "4h",
        "params": {"entry_period": 10, "exit_period": 5, "adx_threshold": 15, "htf_period": 100, "leverage": 2},
    },
    "trend_donchian_mtf_bnb": {
        "strategy_name": "trend_donchian_mtf",
        "allocation": 0.02,
        "symbol": "BNBUSDT",
        "timeframe": "4h",
        "params": {"entry_period": 10, "exit_period": 7, "adx_threshold": 15, "htf_period": 150, "leverage": 2},
    },

    # ── 🔵 MODERATE TIER — 45% (momentum/tail_risk/mid-grid; viable% 5.6-25%, capped ≤10%) ───────
    "momentum_ranking_eth": {
        "strategy_name": "momentum_ranking",
        "allocation": 0.10,
        "symbol": "ETHUSDT",
        "timeframe": "1d",
        "params": {"roc_period": 60, "lookback": 240, "upper_threshold": 70, "lower_threshold": 30, "leverage": 1.5},
    },
    "momentum_ranking_bnb": {
        "strategy_name": "momentum_ranking",
        "allocation": 0.08,
        "symbol": "BNBUSDT",
        "timeframe": "1d",
        "params": {"roc_period": 90, "lookback": 120, "upper_threshold": 70, "lower_threshold": 30, "leverage": 1.5},
    },
    "momentum_ranking_sol": {
        "strategy_name": "momentum_ranking",
        "allocation": 0.03,
        "symbol": "SOLUSDT",
        "timeframe": "1d",
        "params": {"roc_period": 20, "lookback": 240, "upper_threshold": 70, "lower_threshold": 30, "leverage": 1.5},
    },
    "grid_trend_bias_xrp": {
        "strategy_name": "grid_trend_bias",
        "allocation": 0.06,
        "symbol": "XRPUSDT",
        "timeframe": "4h",
        "params": {"bb_period": 15, "bb_std": 2.0, "ema_period": 50, "leverage": 2},
    },
    "tail_risk_hedge_bnb": {
        "strategy_name": "tail_risk_hedge",
        "allocation": 0.05,
        "symbol": "BNBUSDT",
        "timeframe": "1d",
        "params": {"consec_up_threshold": 10, "consec_down_threshold": 5, "exit_bars": 15, "leverage": 1},
    },
    "tail_risk_hedge_sol": {
        "strategy_name": "tail_risk_hedge",
        "allocation": 0.04,
        "symbol": "SOLUSDT",
        "timeframe": "1d",
        "params": {"consec_up_threshold": 10, "consec_down_threshold": 3, "exit_bars": 10, "leverage": 1},
    },
    "grid_trend_bias_btc": {
        "strategy_name": "grid_trend_bias",
        "allocation": 0.03,
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "params": {"bb_period": 30, "bb_std": 3.0, "ema_period": 200, "leverage": 1},
    },
    "grid_trend_bias_sol": {
        "strategy_name": "grid_trend_bias",
        "allocation": 0.03,
        "symbol": "SOLUSDT",
        "timeframe": "4h",
        "params": {"bb_period": 15, "bb_std": 2.0, "ema_period": 100, "leverage": 1},
    },
    "tail_risk_hedge_btc": {
        "strategy_name": "tail_risk_hedge",
        "allocation": 0.03,
        "symbol": "BTCUSDT",
        "timeframe": "1d",
        "params": {"consec_up_threshold": 10, "consec_down_threshold": 5, "exit_bars": 10, "leverage": 1},
    },

    # ── ⚠️ FRAGILE TIER — 7% (very low viable% OR weak stat confidence; capped ≤7%) ─
    "grid_trend_bias_eth": {
        "strategy_name": "grid_trend_bias",
        "allocation": 0.07,
        "symbol": "ETHUSDT",
        "timeframe": "4h",
        "params": {"bb_period": 20, "bb_std": 2.0, "ema_period": 100, "leverage": 2},
    },

    # ── 🟣 MARKET NEUTRAL + GAP FILLER — 8% (new G4+G5; near-zero corr) ─
    "pair_btc_eth": {
        "strategy_name": "pair_btc_eth",
        "allocation": 0.05,
        "symbol": "BTCUSDT",  # Primary symbol (loads ETH internally)
        "timeframe": "4h",
        "params": {"ols_window": 480, "zscore_period": 90, "entry_z": 2.5, "exit_z": 0.0, "stop_z": 4.0, "leverage": 1},
    },
    "funding_reversal_eth": {
        "strategy_name": "funding_reversal",
        "allocation": 0.03,
        "symbol": "ETHUSDT",
        "timeframe": "4h",
        "params": {"entry_rate_long": -4, "hold_bars": 12, "entry_rate_short": 999, "leverage": 1},
    },
}


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


def run_single_strategy(name: str, cfg: dict, runner: BacktestRunner) -> dict | None:
    """Run a single strategy and return result dict."""
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

    try:
        if is_pair:
            result = runner.run_single(
                strategy=strategy,
                ohlcv=ohlcv,
                symbol=cfg["symbol"],
                timeframe=cfg["timeframe"],
                initial_capital=capital,
            )
        else:
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
        "tf": cfg["timeframe"],
        "alloc": cfg["allocation"],
        "capital": capital,
        "final": m.get("final_value", capital),
        "return%": m.get("total_return", 0),
        "sharpe": m.get("sharpe_ratio", 0),
        "sortino": m.get("sortino_ratio", 0),
        "maxdd%": m.get("max_drawdown", 0),
        "calmar": m.get("calmar_ratio", 0),
        "trades": m.get("total_trades", 0),
        "win%": m.get("win_rate", 0),
        "equity": result.equity_curve,
    }


def main() -> None:
    """Run the full portfolio backtest."""
    print("=" * 70)
    print("🚀 Cry2 Full Portfolio Backtest + Walk-Forward Validation")
    print("=" * 70)

    runner = BacktestRunner(str(ROOT / "backtest_tool" / "config" / "backtest_config.yaml"))

    # ─── Run each strategy ──────────────────────────────────────────────
    print("\n📊 Running individual strategies...")
    results = {}
    equity_curves = {}

    for name, cfg in PORTFOLIO.items():
        print(f"  ▶ {name} ({cfg['symbol']} {cfg['timeframe']} alloc={cfg['allocation']:.0%})")
        r = run_single_strategy(name, cfg, runner)
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
