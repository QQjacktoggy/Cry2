#!/usr/bin/env python3
"""Phase 1-2: Full parameter re-scan for long-term + short-term strategies.

Scans all V6 strategies across all 5 coins with parameter grid search.
Identifies optimal parameters and flags overfitting via neighbor stability.

Usage:
    PYTHONPATH=src;. python backtest_tool/scripts/rescan_params.py
    PYTHONPATH=src;. python backtest_tool/scripts/rescan_params.py --strategy momentum_ranking
    PYTHONPATH=src;. python backtest_tool/scripts/rescan_params.py --coin BTCUSDT
"""

from __future__ import annotations

import argparse
import csv
import itertools
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from backtest_tool.engine.runner import BacktestRunner
from backtest_tool.strategies import STRATEGY_MAP

warnings.filterwarnings("ignore")

DATA_DIR = ROOT / "backtest_tool" / "data" / "klines"
OUTPUT_DIR = ROOT / "backtest_tool" / "reports" / "output"

COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT"]

# ─── Parameter Grids ────────────────────────────────────────────────────────

PARAM_GRIDS = {
    # ═══ Phase 1: Long-term core ═══
    "momentum_ranking": {
        "timeframe": "1d",
        "grid": {
            "roc_period": [20, 30, 60, 90],
            "lookback": [90, 120, 180, 240],
            "upper_threshold": [70, 80, 90],
            "lower_threshold": [30, 40, 50],
            "leverage": [1, 1.5, 2],
        },
    },
    "trend_donchian_mtf": {
        "timeframe": "4h",
        "grid": {
            "entry_period": [10, 15, 20, 30],
            "exit_period": [5, 7, 10],
            "adx_threshold": [15, 20, 25, 30],
            "htf_period": [100, 150, 200],
            "leverage": [1, 2],
        },
    },
    "trend_donchian_adx_slope": {
        "timeframe": "4h",
        "grid": {
            "entry_period": [15, 20, 30],
            "exit_period": [5, 7, 10],
            "adx_slope_bars": [3, 5, 7],
            "adx_slope_min": [0.2, 0.3, 0.5],
            "leverage": [1, 2],
        },
    },
    # ═══ Phase 2: Short-term fill ═══
    "grid_trend_bias": {
        "timeframe": "4h",
        "grid": {
            "bb_period": [15, 20, 30],
            "bb_std": [1.5, 2.0, 2.5, 3.0],
            "ema_period": [50, 100, 200],
            "leverage": [1, 2],
        },
    },
    "breakout_squeeze": {
        "timeframe": "4h",
        "grid": {
            "bb_period": [20, 30],
            "bb_std": [2.0, 2.5, 3.0],
            "kc_ema_period": [10, 15, 20],
            "kc_atr_period": [7, 10, 14],
            "kc_mult": [1.5, 2.0],
            "leverage": [1, 2],
        },
    },
    "tail_risk_hedge": {
        "timeframe": "1d",
        "grid": {
            "consec_up_threshold": [10, 12, 14],
            "consec_down_threshold": [3, 5, 7],
            "exit_bars": [5, 10, 15],
            "leverage": [1],
        },
    },
    "dual_channel_breakout": {
        "timeframe": "4h",
        "grid": {
            "dc_period": [20, 30, 40],
            "kc_ema": [10, 15, 20],
            "kc_atr": [10, 14],
            "kc_mult": [1.5, 2.0, 2.5],
            "adx_period": [14],
            "adx_threshold": [15, 20, 25],
            "leverage": [1, 2],
        },
    },
}


def load_data(symbol: str, timeframe: str) -> pd.DataFrame:
    """Load and concatenate all parquet files for a symbol/timeframe."""
    data_path = DATA_DIR / symbol / timeframe
    if not data_path.exists():
        return pd.DataFrame()

    dfs = []
    for f in sorted(data_path.glob("*.parquet")):
        dfs.append(pd.read_parquet(f))

    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    if "timestamp" in combined.columns:
        combined["timestamp"] = pd.to_datetime(combined["timestamp"], unit="ms")
        combined = combined.set_index("timestamp")

    combined = combined.sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]

    for col in ["open", "high", "low", "close", "volume"]:
        if col in combined.columns:
            combined[col] = combined[col].astype(float)

    return combined


def run_single_backtest(
    strategy_name: str,
    symbol: str,
    timeframe: str,
    params: dict,
    data: pd.DataFrame,
    capital: float = 150.0,
) -> dict | None:
    """Run a single backtest and return metrics."""
    if strategy_name not in STRATEGY_MAP:
        return None

    strategy_cls = STRATEGY_MAP[strategy_name]
    try:
        strategy = strategy_cls(params)
        runner = BacktestRunner()
        result = runner.run_single(
            strategy=strategy,
            ohlcv=data,
            symbol=symbol,
            timeframe=timeframe,
            initial_capital=capital,
            leverage=params.get("leverage", 2),
        )
        if result is None:
            return None

        m = result.metrics
        return {
            "strategy": strategy_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "params": str(params),
            "return_pct": m.get("total_return", 0),
            "sharpe": m.get("sharpe_ratio", 0),
            "sortino": m.get("sortino_ratio", 0),
            "max_dd_pct": m.get("max_drawdown", 0),
            "calmar": m.get("calmar_ratio", 0),
            "trades": m.get("total_trades", 0),
            "win_rate": m.get("win_rate", 0),
        }
    except Exception:
        return None


def scan_strategy(
    strategy_name: str,
    coins: list[str],
    grid: dict,
    timeframe: str,
) -> list[dict]:
    """Scan all parameter combinations for a strategy across coins."""
    param_names = list(grid.keys())
    param_values = list(grid.values())
    combos = list(itertools.product(*param_values))
    total_combos = len(combos) * len(coins)

    print(f"\n{'='*60}")
    print(f"  {strategy_name}: {len(combos)} combos x {len(coins)} coins = {total_combos} runs")
    print(f"{'='*60}")

    results = []
    done = 0

    for symbol in coins:
        data = load_data(symbol, timeframe)
        if data.empty:
            print(f"  ⚠️  No data for {symbol}/{timeframe}, skipping")
            continue

        print(f"\n  📊 {symbol} ({len(data)} bars, {timeframe})")

        for combo in combos:
            params = dict(zip(param_names, combo))
            result = run_single_backtest(
                strategy_name, symbol, timeframe, params, data
            )
            done += 1

            if result is not None:
                results.append(result)
                sharpe = result["sharpe"]
                dd = result["max_dd_pct"]
                ret = result["return_pct"]
                marker = " 🌟" if sharpe > 1.0 and dd > -25 else ""
                if done % 50 == 0 or marker:
                    print(f"    [{done}/{total_combos}] Sharpe={sharpe:.2f} DD={dd:.1f}% Ret={ret:.1f}%{marker}")

    return results


def find_best(results: list[dict]) -> pd.DataFrame:
    """Find the best parameters per strategy-coin pair."""
    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    # Filter: Sharpe > 0, trades > 3
    df = df[(df["sharpe"] > 0) & (df["trades"] > 3)]

    if df.empty:
        return pd.DataFrame()

    # Best by Sharpe per strategy-coin
    idx = df.groupby(["strategy", "symbol"])["sharpe"].idxmax()
    best = df.loc[idx].sort_values("sharpe", ascending=False)
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1-2 parameter re-scan")
    parser.add_argument("--strategy", type=str, help="Scan single strategy")
    parser.add_argument("--coin", type=str, help="Scan single coin")
    args = parser.parse_args()

    coins = [args.coin] if args.coin else COINS
    strategies = {args.strategy: PARAM_GRIDS[args.strategy]} if args.strategy else PARAM_GRIDS

    print("🔬 Phase 1-2: Full Parameter Re-Scan")
    print(f"   Strategies: {list(strategies.keys())}")
    print(f"   Coins: {coins}")

    total_combos = 0
    for name, cfg in strategies.items():
        grid = cfg["grid"]
        n = 1
        for v in grid.values():
            n *= len(v)
        total_combos += n * len(coins)
    print(f"   Total combinations: {total_combos}")

    all_results = []

    for strategy_name, cfg in strategies.items():
        results = scan_strategy(
            strategy_name=strategy_name,
            coins=coins,
            grid=cfg["grid"],
            timeframe=cfg["timeframe"],
        )
        all_results.extend(results)

    # Save all results
    output_file = OUTPUT_DIR / "phase12_rescan_results.csv"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if all_results:
        df = pd.DataFrame(all_results)
        df.to_csv(output_file, index=False)
        print(f"\n📄 All results saved to {output_file} ({len(df)} rows)")

        # Best per strategy-coin
        best = find_best(all_results)
        if not best.empty:
            best_file = OUTPUT_DIR / "phase12_rescan_best.csv"
            best.to_csv(best_file, index=False)

            print(f"\n{'='*70}")
            print("🏆 BEST PARAMETERS PER STRATEGY × COIN")
            print(f"{'='*70}")
            for _, row in best.iterrows():
                emoji = "🌟" if row["sharpe"] > 1.0 else "✅" if row["sharpe"] > 0.5 else "⚡"
                print(f"  {emoji} {row['strategy']} {row['symbol']}")
                print(f"     Sharpe={row['sharpe']:.3f}  Return={row['return_pct']:.1f}%  "
                      f"MaxDD={row['max_dd_pct']:.1f}%  Calmar={row['calmar']:.2f}  "
                      f"Trades={int(row['trades'])}  WinRate={row['win_rate']:.1f}%")
                print(f"     Params: {row['params']}")
    else:
        print("\n⚠️  No valid results found")

    print(f"\n✅ Phase 1-2 re-scan complete: {len(all_results)} total results")


if __name__ == "__main__":
    main()
