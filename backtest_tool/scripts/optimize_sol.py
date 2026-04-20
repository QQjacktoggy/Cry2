"""SOL-focused parameter optimization for V6 portfolio expansion.

Tests all V5 strategies on SOLUSDT with parameter grid search,
plus SOL-specific adjustments (lower leverage, tighter params).

Usage:
    cd C:\\Users\\jack_shih\\Desktop\\cry2
    $env:PYTHONPATH="src"; python backtest_tool/scripts/optimize_sol.py
"""
import sys
from pathlib import Path
from itertools import product

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from backtest_tool.strategies import STRATEGY_MAP

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "klines"


def load_data(symbol: str, timeframe: str) -> pd.DataFrame:
    data_path = DATA_DIR / symbol / timeframe
    if not data_path.exists():
        raise FileNotFoundError(f"No data at {data_path}")
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
        result = strat.run_backtest(ohlcv, initial_capital=150)
        stats = result.stats()
        return {
            "strategy": strategy_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "params": str(params),
            "return_pct": stats.get("Total Return [%]", 0),
            "sharpe": stats.get("Sharpe Ratio", 0),
            "max_dd": stats.get("Max Drawdown [%]", 0),
            "trades": stats.get("Total Trades", 0),
            "win_rate": stats.get("Win Rate [%]", 0),
            "calmar": stats.get("Calmar Ratio", 0),
        }
    except Exception as e:
        return {
            "strategy": strategy_name, "symbol": symbol, "timeframe": timeframe,
            "params": str(params), "return_pct": 0, "sharpe": 0,
            "max_dd": 0, "trades": 0, "win_rate": 0, "calmar": 0,
            "error": str(e)[:80],
        }


# SOL-specific param spaces — lower leverage due to high volatility
TASKS = [
    # momentum_ranking — SOL 1d
    {
        "name": "momentum_ranking",
        "symbol": "SOLUSDT",
        "timeframe": "1d",
        "space": {
            "roc_period": [30, 60, 90],
            "lookback": [90, 120, 180],
            "upper_threshold": [70, 80],
            "lower_threshold": [30, 40],
            "leverage": [1, 1.5],
        },
    },
    # trend_donchian_mtf — SOL 4h
    {
        "name": "trend_donchian_mtf",
        "symbol": "SOLUSDT",
        "timeframe": "4h",
        "space": {
            "entry_period": [10, 15, 20],
            "exit_period": [5, 10],
            "adx_threshold": [20, 25, 30],
            "htf_period": [100, 200],
            "leverage": [1, 1.5],
        },
    },
    # trend_donchian_adx_slope — SOL 4h
    {
        "name": "trend_donchian_adx_slope",
        "symbol": "SOLUSDT",
        "timeframe": "4h",
        "space": {
            "entry_period": [15, 20, 30],
            "exit_period": [5, 10],
            "adx_slope_bars": [3, 5, 7],
            "adx_slope_min": [0.2, 0.3, 0.5],
            "leverage": [1, 1.5],
        },
    },
    # grid_trend_bias — SOL 4h
    {
        "name": "grid_trend_bias",
        "symbol": "SOLUSDT",
        "timeframe": "4h",
        "space": {
            "bb_period": [15, 20, 30],
            "bb_std": [1.5, 2.0, 2.5],
            "ema_period": [30, 50, 100],
            "leverage": [1, 1.5],
        },
    },
    # breakout_squeeze — SOL 4h
    {
        "name": "breakout_squeeze",
        "symbol": "SOLUSDT",
        "timeframe": "4h",
        "space": {
            "bb_period": [20, 30],
            "bb_std": [2.0, 2.5],
            "kc_ema_period": [10, 15, 20],
            "kc_atr_period": [7, 10, 14],
            "kc_mult": [1.5, 2.0],
            "leverage": [1, 1.5],
        },
    },
    # tail_risk_hedge — SOL 1d
    {
        "name": "tail_risk_hedge",
        "symbol": "SOLUSDT",
        "timeframe": "1d",
        "space": {
            "consec_up_threshold": [5, 7, 10, 14],
            "consec_down_threshold": [3, 5, 7],
            "exit_bars": [3, 5, 7, 10],
            "leverage": [1],
        },
    },
    # dual_channel_breakout — SOL 4h
    {
        "name": "dual_channel_breakout",
        "symbol": "SOLUSDT",
        "timeframe": "4h",
        "space": {
            "dc_period": [15, 20, 30],
            "kc_ema": [15, 20, 30],
            "kc_atr": [7, 10, 14],
            "kc_mult": [1.5, 2.0],
            "adx_threshold": [20, 25, 30],
            "leverage": [1, 1.5],
        },
    },
]


def main():
    all_results = []
    total_combos = sum(
        len(list(product(*[t["space"][k] for k in t["space"]])))
        for t in TASKS
    )
    print(f"{'='*70}")
    print(f"  🔎 SOL Parameter Optimization — {total_combos} total combos")
    print(f"  Initial capital: $150 USDT | Symbol: SOLUSDT")
    print(f"{'='*70}")

    for task in TASKS:
        name = task["name"]
        sym = task["symbol"]
        tf = task["timeframe"]
        space = task["space"]

        keys = list(space.keys())
        combos = list(product(*[space[k] for k in keys]))
        print(f"\n{'='*60}")
        print(f"  {name} | {sym} | {tf} | {len(combos)} combos")
        print(f"{'='*60}")

        best_sharpe = -999
        best_result = None
        done = 0

        for combo in combos:
            params = dict(zip(keys, combo))
            cls = STRATEGY_MAP[name]
            merged = {**cls.default_params, **params}
            result = run_combo(name, sym, tf, merged)
            all_results.append(result)

            sharpe = result["sharpe"]
            if isinstance(sharpe, (int, float)) and not np.isnan(sharpe) and sharpe > best_sharpe:
                best_sharpe = sharpe
                best_result = result

            done += 1
            if done % 20 == 0:
                print(f"    ... {done}/{len(combos)}")

        if best_result:
            print(f"  ★ BEST: Sharpe={best_result['sharpe']:.2f}  "
                  f"Return={best_result['return_pct']:.1f}%  "
                  f"DD={best_result['max_dd']:.1f}%  "
                  f"Trades={best_result['trades']}  "
                  f"Win={best_result['win_rate']:.1f}%")
            print(f"    Params: {best_result['params']}")
        else:
            print(f"  ⚠ No positive Sharpe found")

    # Save results
    df = pd.DataFrame(all_results)
    out = Path(__file__).resolve().parents[1] / "reports" / "output" / "sol_optimization_results.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\n✅ All results saved to {out}")

    # Summary: top results
    df_valid = df[(df["sharpe"] > 0) & (df["trades"] >= 3)].sort_values("sharpe", ascending=False)
    print(f"\n{'='*70}")
    print(f"  🏆 TOP 10 SOL RESULTS (Sharpe > 0, Trades ≥ 3)")
    print(f"{'='*70}")
    if df_valid.empty:
        print("  No strategies found with Sharpe > 0 and ≥ 3 trades on SOL")
    else:
        for _, row in df_valid.head(10).iterrows():
            print(f"  {row['strategy']:<28} "
                  f"Sharpe={row['sharpe']:>6.2f}  Ret={row['return_pct']:>7.1f}%  "
                  f"DD={row['max_dd']:>7.1f}%  Trades={int(row['trades']):>3}  "
                  f"Win={row['win_rate']:>5.1f}%")

    # SOL feasibility summary
    print(f"\n{'='*70}")
    print(f"  📊 SOL FEASIBILITY SUMMARY")
    print(f"{'='*70}")
    for strat_name in [t["name"] for t in TASKS]:
        strat_df = df_valid[df_valid["strategy"] == strat_name]
        if strat_df.empty:
            print(f"  {strat_name:<28} ❌ No viable params found")
        else:
            best = strat_df.iloc[0]
            verdict = "✅ VIABLE" if best["sharpe"] > 0.5 and best["max_dd"] > -40 else "⚠️ MARGINAL"
            print(f"  {strat_name:<28} {verdict}  Sharpe={best['sharpe']:.2f}  DD={best['max_dd']:.1f}%")


if __name__ == "__main__":
    main()
