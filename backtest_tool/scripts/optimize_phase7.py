"""Param optimization for promising Phase 7 strategies.

Focus: trend_strength_sizing (Sharpe 0.61), dual_channel_breakout (0.51),
       tail_risk_hedge (0.95 — can we push higher?), pv_divergence (0.49)
"""
import sys
from pathlib import Path
from itertools import product

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backtest_tool.strategies import STRATEGY_MAP

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "klines"


def load_data(symbol: str, timeframe: str) -> pd.DataFrame:
    data_path = DATA_DIR / symbol / timeframe
    dfs = [pd.read_parquet(f) for f in sorted(data_path.glob("*.parquet"))]
    combined = pd.concat(dfs, ignore_index=True)
    combined["timestamp"] = pd.to_datetime(combined["timestamp"], unit="ms")
    combined = combined.set_index("timestamp").sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    return combined


def run_combo(strategy_name, symbol, timeframe, params):
    try:
        cls = STRATEGY_MAP[strategy_name]
        strat = cls(params)
        ohlcv = load_data(symbol, timeframe)
        result = strat.run_backtest(ohlcv, initial_capital=1000)
        stats = result.stats()
        return {
            "strategy": strategy_name,
            "symbol": symbol,
            "params": str(params),
            "return_pct": stats.get("Total Return [%]", 0),
            "sharpe": stats.get("Sharpe Ratio", 0),
            "max_dd": stats.get("Max Drawdown [%]", 0),
            "trades": stats.get("Total Trades", 0),
            "win_rate": stats.get("Win Rate [%]", 0),
        }
    except Exception as e:
        return {"strategy": strategy_name, "symbol": symbol, "params": str(params),
                "return_pct": 0, "sharpe": 0, "max_dd": 0, "trades": 0, "win_rate": 0,
                "error": str(e)[:50]}


# ── Param spaces ──────────────────────────────────────────────
TASKS = [
    # trend_strength_sizing: tune adx_min and entry/exit periods
    {
        "name": "trend_strength_sizing",
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "space": {
            "entry_period": [15, 20, 30],
            "exit_period": [5, 10],
            "adx_min": [20, 25, 30],
            "adx_strong": [35, 40],
            "leverage": [2],
        },
    },
    {
        "name": "trend_strength_sizing",
        "symbol": "ETHUSDT",
        "timeframe": "4h",
        "space": {
            "entry_period": [15, 20, 30],
            "exit_period": [5, 10],
            "adx_min": [20, 25, 30],
            "adx_strong": [35, 40],
            "leverage": [2],
        },
    },
    # dual_channel_breakout: tune dc_period, kc params, adx threshold
    {
        "name": "dual_channel_breakout",
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "space": {
            "dc_period": [15, 20, 30],
            "kc_ema": [15, 20, 30],
            "kc_atr": [7, 10, 14],
            "kc_mult": [1.5, 2.0],
            "adx_threshold": [20, 25, 30],
            "leverage": [2],
        },
    },
    {
        "name": "dual_channel_breakout",
        "symbol": "ETHUSDT",
        "timeframe": "4h",
        "space": {
            "dc_period": [15, 20, 30],
            "kc_ema": [15, 20, 30],
            "kc_atr": [7, 10, 14],
            "kc_mult": [1.5, 2.0],
            "adx_threshold": [20, 25, 30],
            "leverage": [2],
        },
    },
    # tail_risk_hedge: tune thresholds
    {
        "name": "tail_risk_hedge",
        "symbol": "BTCUSDT",
        "timeframe": "1d",
        "space": {
            "consec_up_threshold": [7, 10, 14],
            "consec_down_threshold": [3, 5, 7],
            "exit_bars": [3, 5, 7, 10],
            "leverage": [1],
        },
    },
    {
        "name": "tail_risk_hedge",
        "symbol": "ETHUSDT",
        "timeframe": "1d",
        "space": {
            "consec_up_threshold": [7, 10, 14],
            "consec_down_threshold": [3, 5, 7],
            "exit_bars": [3, 5, 7, 10],
            "leverage": [1],
        },
    },
    # pv_divergence: tune slope_period, mfi thresholds
    {
        "name": "pv_divergence",
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "space": {
            "slope_period": [10, 20, 30],
            "mfi_period": [10, 14, 20],
            "mfi_oversold": [15, 20, 25],
            "mfi_overbought": [75, 80, 85],
            "leverage": [2],
        },
    },
    {
        "name": "pv_divergence",
        "symbol": "ETHUSDT",
        "timeframe": "4h",
        "space": {
            "slope_period": [10, 20, 30],
            "mfi_period": [10, 14, 20],
            "mfi_oversold": [15, 20, 25],
            "mfi_overbought": [75, 80, 85],
            "leverage": [2],
        },
    },
]


def main():
    all_results = []

    for task in TASKS:
        name = task["name"]
        sym = task["symbol"]
        tf = task["timeframe"]
        space = task["space"]

        keys = list(space.keys())
        combos = list(product(*[space[k] for k in keys]))
        print(f"\n{'='*60}")
        print(f"  {name} | {sym} | {len(combos)} combos")
        print(f"{'='*60}")

        best_sharpe = -999
        best_result = None

        for combo in combos:
            params = dict(zip(keys, combo))
            # Merge with default params
            cls = STRATEGY_MAP[name]
            merged = {**cls.default_params, **params}
            result = run_combo(name, sym, tf, merged)
            all_results.append(result)

            sharpe = result["sharpe"]
            if isinstance(sharpe, (int, float)) and not np.isnan(sharpe) and sharpe > best_sharpe:
                best_sharpe = sharpe
                best_result = result

        if best_result:
            print(f"  BEST: Sharpe={best_result['sharpe']:.2f}  "
                  f"Return={best_result['return_pct']:.1f}%  "
                  f"DD={best_result['max_dd']:.1f}%  "
                  f"Trades={best_result['trades']}  "
                  f"Win={best_result['win_rate']:.1f}%")
            print(f"  Params: {best_result['params']}")

    # Save results
    df = pd.DataFrame(all_results)
    out = Path(__file__).resolve().parents[1] / "reports" / "output" / "phase7_optimization_results.csv"
    df.to_csv(out, index=False)
    print(f"\n✅ All results saved to {out}")

    # Summary: top 5 by Sharpe
    df_valid = df[df["sharpe"] > 0].sort_values("sharpe", ascending=False)
    print(f"\n{'='*70}")
    print("  TOP 10 RESULTS (by Sharpe)")
    print(f"{'='*70}")
    for _, row in df_valid.head(10).iterrows():
        print(f"  {row['strategy']:<25} {row['symbol']:<10} "
              f"Sharpe={row['sharpe']:>6.2f}  Ret={row['return_pct']:>7.1f}%  "
              f"DD={row['max_dd']:>7.1f}%  W={row['win_rate']:>5.1f}%")


if __name__ == "__main__":
    main()
