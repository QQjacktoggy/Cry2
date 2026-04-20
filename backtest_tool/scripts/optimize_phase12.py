#!/usr/bin/env python3
"""Phase 1+2: Comprehensive parameter optimization for core strategies.

Phase 1 — Long-term core strategies (4):
  1A: momentum_ranking full scan (BTC/ETH/SOL × 1d)
  1B: trend_donchian optimized params verification (ETH/SOL × 4h)
  1C: trend_donchian_mtf multi-timeframe scan (BTC × 4h)
  1D: trend_donchian_adx_slope param scan (BTC × 4h)

Phase 2 — Short-term gap-filling strategies (4):
  2A: grid_trend_bias param scan (BTC × 4h)
  2B: breakout_squeeze optimization (BTC × 4h)
  2C: mean_reversion_bb stop-loss fix (BTC/ETH × 1h)
  2D: grid_funding_aware optimization (BTC × 4h)

Usage:
    python -m backtest_tool.scripts.optimize_phase12
    python -m backtest_tool.scripts.optimize_phase12 --phase 1 --task 1a
    python -m backtest_tool.scripts.optimize_phase12 --symbol BTCUSDT --save-csv results.csv
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import DataStore
from backtest_tool.engine import BacktestRunner, ParamScanner
from backtest_tool.strategies import STRATEGY_MAP

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "reports" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================================
# Parameter Spaces
# =============================================================================

PHASE1_TASKS: dict[str, dict[str, Any]] = {
    "1a": {
        "name": "momentum_ranking",
        "desc": "momentum_ranking 全幣種參數掃描",
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "timeframe": "1d",
        "param_space": {
            "roc_period": [10, 20, 30, 40, 60],
            "lookback": [60, 90, 120, 180],
            "upper_pct": [60, 65, 70, 75, 80],
            "lower_pct": [20, 25, 30, 35, 40],
            "leverage": [1],
        },
        "target": "sharpe_ratio",
        "accept_criteria": {"sharpe_ratio": 1.0, "max_drawdown": -25},
    },
    "1b": {
        "name": "trend_donchian",
        "desc": "trend_donchian 最佳化參數跨幣種驗證",
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "timeframe": "4h",
        "param_space": {
            "entry_period": [25, 30, 35],
            "exit_period": [5, 7, 10],
            "adx_threshold": [25, 30, 35],
            "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
            "leverage": [1, 2],
        },
        "target": "sharpe_ratio",
        "accept_criteria": {"sharpe_ratio": 0.8, "max_drawdown": -20},
    },
    "1c": {
        "name": "trend_donchian_mtf",
        "desc": "trend_donchian_mtf 多時框參數掃描",
        "symbols": ["BTCUSDT"],
        "timeframe": "4h",
        "param_space": {
            "entry_period": [15, 20, 25, 30],
            "exit_period": [5, 10, 15],
            "adx_threshold": [20, 25, 30],
            "htf_period": [100, 150, 200],
            "leverage": [1, 2],
        },
        "target": "sharpe_ratio",
        "accept_criteria": {"sharpe_ratio": 0.5, "max_drawdown": -25},
    },
    "1d": {
        "name": "trend_donchian_adx_slope",
        "desc": "trend_donchian_adx_slope 參數掃描",
        "symbols": ["BTCUSDT"],
        "timeframe": "4h",
        "param_space": {
            "entry_period": [15, 20, 25, 30],
            "exit_period": [5, 10, 15],
            "adx_threshold": [20, 25, 30],
            "adx_slope_bars": [3, 5, 7, 10],
            "leverage": [1, 2],
        },
        "target": "sharpe_ratio",
        "accept_criteria": {"sharpe_ratio": 0.5, "max_drawdown": -20},
    },
}

PHASE2_TASKS: dict[str, dict[str, Any]] = {
    "2a": {
        "name": "grid_trend_bias",
        "desc": "grid_trend_bias 參數掃描",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "timeframe": "4h",
        "param_space": {
            "bb_period": [15, 20, 25, 30],
            "bb_std": [1.5, 2.0, 2.5, 3.0],
            "ema_period": [50, 100, 150, 200],
            "leverage": [1, 2],
        },
        "target": "sharpe_ratio",
        "accept_criteria": {"sharpe_ratio": 0.7, "max_drawdown": -20},
    },
    "2b": {
        "name": "breakout_squeeze",
        "desc": "breakout_squeeze 參數優化",
        "symbols": ["BTCUSDT"],
        "timeframe": "4h",
        "param_space": {
            "bb_period": [15, 20, 25, 30],
            "bb_std": [1.5, 2.0, 2.5],
            "kc_ema": [15, 20, 25],
            "kc_atr": [7, 10, 14],
            "kc_mult": [1.0, 1.5, 2.0],
            "leverage": [1],
        },
        "target": "sharpe_ratio",
        "accept_criteria": {"sharpe_ratio": 0.5, "max_drawdown": -20},
    },
    "2c": {
        "name": "mean_reversion_bb",
        "desc": "mean_reversion_bb 止損修復 (ATR trailing)",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "timeframe": "1h",
        "param_space": {
            "bb_period": [12, 15, 20, 25],
            "bb_std": [1.5, 2.0, 2.5, 3.0],
            "rsi_oversold": [20, 25, 30, 35],
            "rsi_overbought": [65, 70, 75, 80],
            "leverage": [1],
        },
        "target": "sharpe_ratio",
        "accept_criteria": {"sharpe_ratio": 0.5, "max_drawdown": -30},
    },
    "2d": {
        "name": "grid_funding_aware",
        "desc": "grid_funding_aware 優化",
        "symbols": ["BTCUSDT"],
        "timeframe": "4h",
        "param_space": {
            "bb_period": [15, 20, 25, 30],
            "bb_std": [1.5, 2.0, 2.5, 3.0],
            "funding_threshold": [0.0001, 0.0003, 0.0005, 0.001],
            "leverage": [1, 2],
        },
        "target": "sharpe_ratio",
        "accept_criteria": {"sharpe_ratio": 0.4, "max_drawdown": -25},
    },
}


# =============================================================================
# Synthetic data fallback
# =============================================================================

def _generate_synthetic(
    timeframe: str, start: str, end: str, seed: int = 42,
) -> pd.DataFrame:
    freq_map = {"1h": "1h", "4h": "4h", "8h": "8h", "1d": "1D"}
    freq = freq_map.get(timeframe, "4h")
    index = pd.date_range(start=start, end=end, freq=freq)
    n = len(index)
    rng = np.random.default_rng(seed)
    s0, annual_drift, annual_vol = 45000.0, 0.40, 0.65
    bars_per_year = {"1h": 8760, "4h": 2190, "8h": 1095, "1D": 365}.get(freq, 2190)
    dt = 1.0 / bars_per_year
    mu, sigma = annual_drift * dt, annual_vol * np.sqrt(dt)
    rets = rng.normal(mu - 0.5 * sigma**2, sigma, n)
    closes = s0 * np.exp(np.cumsum(rets))
    cycle = 0.15 * np.sin(2 * np.pi * np.arange(n) / (bars_per_year * 1.5))
    closes *= 1 + cycle
    bar_vol = annual_vol / np.sqrt(bars_per_year)
    highs = closes * (1 + rng.uniform(0, bar_vol * 1.5, n))
    lows = closes * (1 - rng.uniform(0, bar_vol * 1.5, n))
    opens = np.roll(closes, 1); opens[0] = s0
    volumes = rng.lognormal(13, 0.8, n)
    return pd.DataFrame({
        "timestamp": (index.astype(np.int64) // 10**6).astype(int),
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": volumes, "quote_volume": volumes * closes,
        "trade_count": (volumes / 0.01).astype(int),
        "taker_buy_volume": volumes * 0.52,
        "taker_buy_quote_volume": volumes * closes * 0.52,
    }, index=index)


def load_data(store: DataStore, symbol: str, tf: str, start: str, end: str) -> pd.DataFrame:
    df = store.load_klines(symbol, tf, start=start, end=end)
    if not df.empty and len(df) > 100:
        return df
    try:
        from backtest_tool.data_manager import DataImporter
        importer = DataImporter(store)
        importer.download_from_binance(symbols=[symbol], timeframes=[tf], start=start, end=end)
        df = store.load_klines(symbol, tf, start=start, end=end)
        if not df.empty and len(df) > 100:
            return df
    except Exception:
        pass
    print(f"    ⚠️  Using synthetic data for {symbol} {tf}")
    return _generate_synthetic(tf, start, end)


# =============================================================================
# Display
# =============================================================================

METRIC_COLS = [
    ("sharpe_ratio",  "Sharpe",    "{:>7.3f}"),
    ("total_return",  "Return%",   "{:>+8.2f}"),
    ("annual_return", "Annual%",   "{:>+8.2f}"),
    ("max_drawdown",  "MaxDD%",    "{:>8.2f}"),
    ("calmar_ratio",  "Calmar",    "{:>7.3f}"),
    ("win_rate",      "WinR%",     "{:>7.2f}"),
    ("profit_factor", "PF",        "{:>7.3f}"),
    ("total_trades",  "Trades",    "{:>6.0f}"),
    ("sortino_ratio", "Sortino",   "{:>7.3f}"),
]


def print_results(task_id: str, task: dict, symbol: str, df: pd.DataFrame, top_n: int = 10) -> None:
    if df.empty:
        print(f"  ⚠️  [{task_id}] {task['name']} @ {symbol}: No results")
        return

    sep = "=" * 110
    print(f"\n{sep}")
    print(f"  [{task_id.upper()}] {task['desc']}  │  {symbol}  │  Top {min(top_n, len(df))}")
    print(sep)

    # Determine param columns
    metric_keys = {m[0] for m in METRIC_COLS} | {
        "annualized_return", "volatility_ann", "max_dd_duration_bars",
        "avg_trade_pnl", "avg_win", "avg_loss", "best_trade", "worst_trade",
        "payoff_ratio", "net_profit", "total_fees", "initial_capital",
        "leverage", "final_value", "total_pnl", "maker_fee", "taker_fee", "slippage_bps",
    }
    param_cols = [c for c in df.columns if c not in metric_keys]

    # Header
    hdr = [f"{'#':>3}"]
    for pc in param_cols[:6]:
        hdr.append(f"{pc:>12}")
    for _, label, _ in METRIC_COLS:
        hdr.append(f"{label:>9}")
    print("  " + " ".join(hdr))
    print("  " + "-" * 106)

    for rank, row in df.head(top_n).iterrows():
        parts = [f"{rank:>3}"]
        for pc in param_cols[:6]:
            v = row.get(pc, "")
            parts.append(f"{str(v):>12}")
        for key, _, fmt in METRIC_COLS:
            v = row.get(key, 0)
            try:
                parts.append(fmt.format(v).rjust(9))
            except Exception:
                parts.append(f"{'N/A':>9}")
        print("  " + " ".join(parts))

    # Best summary
    best = df.iloc[0]
    criteria = task.get("accept_criteria", {})
    passed = True
    for k, threshold in criteria.items():
        actual = best.get(k, 0)
        if k == "max_drawdown":
            if actual < threshold:
                passed = False
        elif actual < threshold:
            passed = False

    status = "✅ PASS" if passed else "⚠️  BELOW TARGET"
    best_params = {pc: best.get(pc) for pc in param_cols[:6]}
    print(f"\n  🏆 Best: {best_params}")
    print(f"     Sharpe={best.get('sharpe_ratio', 0):.4f} │ "
          f"Return={best.get('total_return', 0):+.2f}% │ "
          f"MaxDD={best.get('max_drawdown', 0):.2f}% │ "
          f"Calmar={best.get('calmar_ratio', 0):.4f} │ {status}")
    print(sep)


def print_summary(all_results: list[dict]) -> None:
    sep = "=" * 130
    print(f"\n{sep}")
    print("  📊 Phase 1+2 Optimization Summary")
    print(sep)

    hdr = f"  {'Task':>4} {'Strategy':<26} {'Symbol':<10} {'Sharpe':>8} {'Return%':>9} {'MaxDD%':>8} {'Calmar':>8} {'Trades':>7} {'Status':>10}"
    print(hdr)
    print("  " + "-" * 124)

    for r in all_results:
        task_id = r["task_id"]
        strat = r["strategy"]
        symbol = r["symbol"]
        best = r.get("best", {})
        status = r.get("status", "N/A")
        print(f"  {task_id:>4} {strat:<26} {symbol:<10} "
              f"{best.get('sharpe_ratio', 0):>8.4f} "
              f"{best.get('total_return', 0):>+9.2f} "
              f"{best.get('max_drawdown', 0):>8.2f} "
              f"{best.get('calmar_ratio', 0):>8.4f} "
              f"{best.get('total_trades', 0):>7.0f} "
              f"{'✅' if status == 'PASS' else '⚠️':>10}")

    print(sep)


# =============================================================================
# Main
# =============================================================================

def run_task(
    task_id: str,
    task: dict,
    store: DataStore,
    runner: BacktestRunner,
    start: str,
    end: str,
    top_n: int,
) -> list[dict]:
    """Run a single optimization task across all specified symbols."""
    results = []
    strategy_cls = STRATEGY_MAP[task["name"]]
    tf = task["timeframe"]
    param_space = task["param_space"]
    n_combos = len(list(itertools.product(*param_space.values())))

    for symbol in task["symbols"]:
        print(f"\n  📂 Loading {symbol} {tf}...")
        df = load_data(store, symbol, tf, start, end)
        if df.empty or len(df) < 50:
            print(f"  ❌ Insufficient data for {symbol}, skipping")
            results.append({"task_id": task_id, "strategy": task["name"],
                          "symbol": symbol, "best": {}, "status": "NO_DATA", "df": pd.DataFrame()})
            continue

        print(f"  ✅ {len(df)} bars │ Scanning {n_combos} combos...")
        scanner = ParamScanner(runner)

        t0 = time.time()
        scan_df = scanner.scan(strategy_cls, param_space, df,
                               sort_by=task["target"], top_n=top_n)
        elapsed = time.time() - t0

        print_results(task_id, task, symbol, scan_df, top_n)

        if not scan_df.empty:
            best = scan_df.iloc[0].to_dict()
            criteria = task.get("accept_criteria", {})
            passed = all(
                (best.get(k, 0) >= v) if k != "max_drawdown" else (best.get(k, 0) >= v)
                for k, v in criteria.items()
            )
            status = "PASS" if passed else "BELOW"
        else:
            best = {}
            status = "EMPTY"

        results.append({
            "task_id": task_id,
            "strategy": task["name"],
            "symbol": symbol,
            "best": best,
            "status": status,
            "df": scan_df,
            "elapsed": elapsed,
        })

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1+2 parameter optimization")
    parser.add_argument("--phase", type=int, default=None, choices=[1, 2],
                        help="Run only Phase 1 or 2")
    parser.add_argument("--task", type=str, default=None,
                        help="Run specific task (e.g., 1a, 2c)")
    parser.add_argument("--start", type=str, default="2024-01-01")
    parser.add_argument("--end", type=str, default="2026-04-17")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--save-csv", type=str, default=None)
    args = parser.parse_args()

    # Select tasks
    all_tasks: dict[str, dict] = {}
    if args.task:
        task_id = args.task.lower()
        if task_id in PHASE1_TASKS:
            all_tasks[task_id] = PHASE1_TASKS[task_id]
        elif task_id in PHASE2_TASKS:
            all_tasks[task_id] = PHASE2_TASKS[task_id]
        else:
            print(f"❌ Unknown task: {task_id}")
            sys.exit(1)
    elif args.phase == 1:
        all_tasks = PHASE1_TASKS
    elif args.phase == 2:
        all_tasks = PHASE2_TASKS
    else:
        all_tasks = {**PHASE1_TASKS, **PHASE2_TASKS}

    # Count total combos
    total_combos = 0
    for tid, t in all_tasks.items():
        nc = len(list(itertools.product(*t["param_space"].values())))
        ns = len(t["symbols"])
        total_combos += nc * ns

    banner = "=" * 110
    print(banner)
    print(f"  🚀 Phase 1+2 Parameter Optimization")
    print(f"  期間: {args.start} → {args.end} │ 初始資金: ${args.capital:,.0f}")
    print(f"  任務: {len(all_tasks)} │ 總參數組合: ~{total_combos:,}")
    print(banner)

    store = DataStore()
    runner = BacktestRunner(config_path=str(CONFIG_PATH))
    runner.initial_capital = args.capital

    all_results: list[dict] = []
    all_csv_parts: list[pd.DataFrame] = []

    for task_id, task in all_tasks.items():
        phase = "Phase 1 (長線)" if task_id.startswith("1") else "Phase 2 (短線)"
        print(f"\n{'─' * 110}")
        print(f"  [{task_id.upper()}] {phase}: {task['desc']}")
        print(f"{'─' * 110}")

        task_results = run_task(task_id, task, store, runner, args.start, args.end, args.top)
        all_results.extend(task_results)

        # Collect CSV data
        for r in task_results:
            if not r["df"].empty:
                csv_df = r["df"].copy()
                csv_df.insert(0, "task_id", task_id)
                csv_df.insert(1, "strategy", r["strategy"])
                csv_df.insert(2, "symbol", r["symbol"])
                all_csv_parts.append(csv_df)

    # Summary
    print_summary(all_results)

    # Save CSV
    csv_path = args.save_csv or str(OUTPUT_DIR / "phase12_optimization_results.csv")
    if all_csv_parts:
        combined = pd.concat(all_csv_parts, ignore_index=True)
        combined.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"\n  💾 Results saved: {csv_path}")

    # Generate optimized params summary
    print("\n" + "=" * 110)
    print("  📋 Recommended Optimized Parameters")
    print("=" * 110)
    for r in all_results:
        if r["status"] == "PASS" and r["best"]:
            b = r["best"]
            metric_keys = {m[0] for m in METRIC_COLS} | {
                "annualized_return", "volatility_ann", "max_dd_duration_bars",
                "avg_trade_pnl", "avg_win", "avg_loss", "best_trade", "worst_trade",
                "payoff_ratio", "net_profit", "total_fees", "initial_capital",
                "leverage", "final_value", "total_pnl", "maker_fee", "taker_fee", "slippage_bps",
            }
            params = {k: v for k, v in b.items() if k not in metric_keys}
            print(f"  {r['strategy']:>26} @ {r['symbol']:<10}: {params}")

    print(f"\n  ✅ Phase 1+2 optimization complete!\n")


if __name__ == "__main__":
    main()
