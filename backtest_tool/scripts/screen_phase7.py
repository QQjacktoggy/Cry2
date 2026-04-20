"""Screen all Phase 7 strategies on BTC/ETH to find replacements for weak strategies."""
import sys
from pathlib import Path

import pandas as pd

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


CONFIGS = [
    ("momentum_rotation", "ETHUSDT", "1d", {"leverage": 1.5}),
    ("momentum_rotation", "BTCUSDT", "1d", {"leverage": 1.5}),
    ("trend_strength_sizing", "BTCUSDT", "4h", {"leverage": 2}),
    ("trend_strength_sizing", "ETHUSDT", "4h", {"leverage": 2}),
    ("dual_channel_breakout", "BTCUSDT", "4h", {"leverage": 2}),
    ("dual_channel_breakout", "ETHUSDT", "4h", {"leverage": 2}),
    ("vol_mean_reversion", "BTCUSDT", "4h", {"leverage": 2}),
    ("vol_mean_reversion", "ETHUSDT", "4h", {"leverage": 2}),
    ("gamma_scalping", "BTCUSDT", "4h", {"leverage": 1}),
    ("gamma_scalping", "ETHUSDT", "4h", {"leverage": 1}),
    ("pv_divergence", "BTCUSDT", "4h", {"leverage": 2}),
    ("pv_divergence", "ETHUSDT", "4h", {"leverage": 2}),
    ("tail_risk_hedge", "BTCUSDT", "1d", {"leverage": 1}),
    ("tail_risk_hedge", "ETHUSDT", "1d", {"leverage": 1}),
    ("time_of_day_filter", "BTCUSDT", "1h", {"leverage": 1}),
    ("time_of_day_filter", "ETHUSDT", "1h", {"leverage": 1}),
    ("pairs_spread_mr", "BTCUSDT", "4h", {"leverage": 1}),
    ("funding_contrarian", "BTCUSDT", "8h", {"leverage": 1}),
]


def main():
    header = "{:<28} {:<10} {:>8} {:>7} {:>7} {:>6} {:>6}".format(
        "Strategy", "Symbol", "Return%", "Sharpe", "MaxDD%", "Trades", "Win%"
    )
    print(header)
    print("-" * 80)

    for name, sym, tf, params in CONFIGS:
        try:
            cls = STRATEGY_MAP[name]
            merged = {**cls.default_params, **params}
            strat = cls(merged)
            ohlcv = load_data(sym, tf)
            result = strat.run_backtest(ohlcv, initial_capital=1000)
            stats = result.stats()
            ret = stats.get("Total Return [%]", 0)
            sharpe = stats.get("Sharpe Ratio", 0)
            maxdd = stats.get("Max Drawdown [%]", 0)
            trades = stats.get("Total Trades", 0)
            winr = stats.get("Win Rate [%]", 0)
            print(
                "{:<28} {:<10} {:>8.1f} {:>7.2f} {:>7.1f} {:>6} {:>6.1f}".format(
                    name, sym, ret, sharpe, maxdd, trades, winr
                )
            )
        except Exception as e:
            print("{:<28} {:<10}  ERROR: {}".format(name, sym, str(e)[:50]))


if __name__ == "__main__":
    main()
