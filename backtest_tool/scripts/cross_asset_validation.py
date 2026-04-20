"""Cross-asset robustness validation for V4 portfolio strategies.

Tests each V4 strategy on SOL, BNB, XRP (in addition to original BTC/ETH)
to verify the strategies are not over-fitted to specific assets.

Usage:
    python -m backtest_tool.scripts.cross_asset_validation
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from backtest_tool.engine.runner import BacktestRunner
from backtest_tool.strategies import STRATEGY_MAP

DATA_DIR = ROOT / "backtest_tool" / "data" / "klines"

# V4 strategies with their optimized params and timeframes
V4_STRATEGIES = {
    "momentum_ranking": {
        "timeframe": "1d",
        "params": {"roc_period": 60, "lookback": 180, "upper_threshold": 80, "lower_threshold": 40, "leverage": 1.5},
    },
    "trend_donchian_mtf": {
        "timeframe": "4h",
        "params": {"entry_period": 15, "exit_period": 10, "adx_threshold": 20, "htf_period": 200, "leverage": 2},
    },
    "trend_donchian_adx_slope": {
        "timeframe": "4h",
        "params": {"entry_period": 20, "exit_period": 10, "adx_slope_bars": 5, "adx_slope_min": 0.3, "leverage": 2},
    },
    "grid_trend_bias": {
        "timeframe": "4h",
        "params": {"bb_period": 20, "bb_std": 2.0, "ema_period": 50, "leverage": 2},
    },
    "breakout_squeeze": {
        "timeframe": "4h",
        "params": {"bb_period": 30, "bb_std": 2.5, "kc_ema_period": 15, "kc_atr_period": 7, "kc_mult": 2.0, "leverage": 2},
    },
    "tail_risk_hedge": {
        "timeframe": "1d",
        "params": {"consec_up_threshold": 14, "consec_down_threshold": 5, "exit_bars": 10, "leverage": 1},
    },
    "dual_channel_breakout": {
        "timeframe": "4h",
        "params": {"dc_period": 30, "kc_ema": 15, "kc_atr": 14, "kc_mult": 2.0, "adx_period": 14, "adx_threshold": 20, "leverage": 2},
    },
}

# Original symbols used in V4 portfolio
ORIGINAL_SYMBOLS = {"BTCUSDT", "ETHUSDT"}
# New symbols to validate
TEST_SYMBOLS = ["SOLUSDT", "BNBUSDT", "XRPUSDT"]
ALL_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


def load_data(symbol: str, timeframe: str) -> pd.DataFrame:
    data_path = DATA_DIR / symbol / timeframe
    if not data_path.exists():
        raise FileNotFoundError(f"No data at {data_path}")

    dfs = []
    for f in sorted(data_path.glob("*.parquet")):
        dfs.append(pd.read_parquet(f))

    combined = pd.concat(dfs, ignore_index=True)
    if "timestamp" in combined.columns:
        combined["timestamp"] = pd.to_datetime(combined["timestamp"], unit="ms")
        combined = combined.set_index("timestamp")
    elif not isinstance(combined.index, pd.DatetimeIndex):
        combined.index = pd.to_datetime(combined.index)

    combined = combined.sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]

    col_map = {}
    for col in combined.columns:
        lower = col.lower()
        if lower in ("open", "high", "low", "close", "volume"):
            col_map[col] = lower
    if col_map:
        combined = combined.rename(columns=col_map)

    return combined


def main() -> None:
    print("=" * 80)
    print("🔬 Cross-Asset Robustness Validation — V4 Strategies on SOL/BNB/XRP")
    print("=" * 80)

    runner = BacktestRunner(str(ROOT / "backtest_tool" / "config" / "backtest_config.yaml"))
    results = []

    for strat_name, cfg in V4_STRATEGIES.items():
        strategy_cls = STRATEGY_MAP.get(strat_name)
        if strategy_cls is None:
            continue

        for symbol in ALL_SYMBOLS:
            try:
                ohlcv = load_data(symbol, cfg["timeframe"])
            except FileNotFoundError:
                continue

            strategy = strategy_cls(cfg["params"])
            try:
                result = runner.run_single(
                    strategy=strategy,
                    ohlcv=ohlcv,
                    symbol=symbol,
                    timeframe=cfg["timeframe"],
                    initial_capital=10000,
                )
            except Exception as e:
                print(f"  ❌ {strat_name} {symbol}: {e}")
                continue

            m = result.metrics
            results.append({
                "strategy": strat_name,
                "symbol": symbol,
                "is_original": symbol in ORIGINAL_SYMBOLS,
                "return_pct": m.get("total_return", 0),
                "sharpe": m.get("sharpe_ratio", 0),
                "max_dd": m.get("max_drawdown", 0),
                "trades": m.get("total_trades", 0),
                "win_rate": m.get("win_rate", 0),
                "calmar": m.get("calmar_ratio", 0),
            })

    df = pd.DataFrame(results)

    # Print results grouped by strategy
    print("\n" + "=" * 80)
    print("📊 Results by Strategy × Symbol")
    print("=" * 80)

    for strat in V4_STRATEGIES:
        sub = df[df["strategy"] == strat].sort_values("sharpe", ascending=False)
        if len(sub) == 0:
            continue

        print(f"\n  {'─'*70}")
        print(f"  {strat}")
        print(f"  {'─'*70}")
        print(f"  {'Symbol':<12} {'Original':>8} {'Return%':>8} {'Sharpe':>7} {'MaxDD%':>7} {'Calmar':>7} {'Trades':>6} {'Win%':>6}")

        for _, r in sub.iterrows():
            tag = "✅ OG" if r["is_original"] else "🆕 NEW"
            print(
                f"  {r['symbol']:<12} {tag:>8} "
                f"{r['return_pct']:>7.1f} {r['sharpe']:>7.2f} "
                f"{r['max_dd']:>7.1f} {r['calmar']:>7.2f} "
                f"{int(r['trades']):>6d} {r['win_rate']:>5.1f}"
            )

    # Summary statistics
    print("\n" + "=" * 80)
    print("📈 Cross-Asset Robustness Summary")
    print("=" * 80)

    for strat in V4_STRATEGIES:
        sub = df[df["strategy"] == strat]
        orig = sub[sub["is_original"]]
        new = sub[~sub["is_original"]]

        if len(orig) == 0 or len(new) == 0:
            continue

        orig_sharpe = orig["sharpe"].mean()
        new_sharpe = new["sharpe"].mean()
        pct_positive_new = (new["sharpe"] > 0).sum() / len(new) * 100

        robustness = "🟢 ROBUST" if pct_positive_new >= 67 else ("🟡 PARTIAL" if pct_positive_new >= 33 else "🔴 FRAGILE")

        print(f"  {strat:<30s}  Orig Sharpe={orig_sharpe:5.2f}  New Sharpe={new_sharpe:5.2f}  Positive={pct_positive_new:.0f}%  {robustness}")

    # Overall
    orig_all = df[df["is_original"]]
    new_all = df[~df["is_original"]]
    print(f"\n  {'Overall':<30s}  Orig Sharpe={orig_all['sharpe'].mean():5.2f}  New Sharpe={new_all['sharpe'].mean():5.2f}")
    print(f"  Strategies profitable on new symbols: {(new_all['sharpe'] > 0).sum()}/{len(new_all)} ({(new_all['sharpe'] > 0).sum()/len(new_all)*100:.0f}%)")

    # Save results
    out_path = ROOT / "backtest_tool" / "reports" / "output" / "cross_asset_validation.csv"
    df.to_csv(out_path, index=False)
    print(f"\n✅ Results saved to {out_path}")


if __name__ == "__main__":
    main()
