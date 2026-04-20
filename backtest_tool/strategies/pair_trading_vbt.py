"""Strategy G5: BTC-ETH Pair Trading (VectorBT).

Market-neutral strategy trading the BTC/ETH price ratio via z-score
mean reversion. When the ratio deviates from its rolling mean by
a threshold z-score, trade the reversion.

Implementation approach:
- Compute ratio = BTC_close / ETH_close (synthetic price)
- Apply z-score of log(ratio) for stationarity
- Long ratio (= long BTC + short ETH) when z < -entry_z
- Short ratio (= short BTC + long ETH) when z > entry_z
- Exit when z reverts toward zero

Using the ratio as VBT's 'close' price is standard practice for
pairs trading backtests — the PnL from trading the ratio equals
the combined PnL from the two-leg futures position.

Fees are doubled (2 legs = 2 fee events per trade).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import vectorbt as vbt

from backtest_tool.strategies.base_vbt import FREQ_MAP, BaseVBTStrategy

# Default kline data directory
_KLINES_DIR = Path(__file__).resolve().parent.parent / "data" / "klines"


class PairTradingBTCETH(BaseVBTStrategy):
    """BTC-ETH pair trading via ratio z-score mean reversion.

    Market-neutral: net exposure ≈ 0 when both legs active.
    Profits from the BTC/ETH ratio reverting to its rolling mean.
    """

    name = "pair_btc_eth"
    required_timeframe = "4h"
    # Symbols required for this strategy
    required_symbols: list[str] = ["BTCUSDT", "ETHUSDT"]

    default_params: dict[str, Any] = {
        # Z-score calculation
        "zscore_period": 60,        # Rolling window for mean/std (60 bars = 10 days at 4h)
        "entry_z": 1.8,             # Enter when |z| > this
        "exit_z": 0.3,              # Exit when |z| < this (near mean)
        # Risk management
        "stop_z": 3.5,              # Stop loss at extreme divergence
        "max_hold_bars": 120,       # ~20 days max hold
        # Hedge ratio
        "hedge_ratio": 1.0,         # 1:1 notional (symmetric)
        "leverage": 1,
    }

    @staticmethod
    def load_pair_data(
        symbol_a: str = "BTCUSDT",
        symbol_b: str = "ETHUSDT",
        timeframe: str = "4h",
        data_dir: Path | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Load OHLCV data for both symbols.

        Args:
            symbol_a: First symbol (e.g., BTCUSDT).
            symbol_b: Second symbol (e.g., ETHUSDT).
            timeframe: Candle timeframe.
            data_dir: Override data directory.

        Returns:
            Tuple of (df_a, df_b) DataFrames with DatetimeIndex.
        """
        kdir = data_dir or _KLINES_DIR

        dfs = {}
        for sym in (symbol_a, symbol_b):
            path = kdir / sym / timeframe
            if not path.exists():
                raise FileNotFoundError(f"No data at {path}")

            parts = []
            for f in sorted(path.glob("*.parquet")):
                parts.append(pd.read_parquet(f))
            if not parts:
                raise FileNotFoundError(f"No parquet files in {path}")

            combined = pd.concat(parts, ignore_index=True)
            if "timestamp" in combined.columns:
                combined["timestamp"] = pd.to_datetime(combined["timestamp"], unit="ms")
                combined = combined.set_index("timestamp")
            elif not isinstance(combined.index, pd.DatetimeIndex):
                combined.index = pd.to_datetime(combined.index)
            combined = combined.sort_index()
            combined = combined[~combined.index.duplicated(keep="first")]
            # Normalize column names
            col_map = {}
            for col in combined.columns:
                lower = col.lower()
                if lower in ("open", "high", "low", "close", "volume"):
                    col_map[col] = lower
            if col_map:
                combined = combined.rename(columns=col_map)
            dfs[sym] = combined

        return dfs[symbol_a], dfs[symbol_b]

    @staticmethod
    def compute_ratio_ohlcv(
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        hedge_ratio: float = 1.0,
    ) -> pd.DataFrame:
        """Compute ratio OHLCV from two symbol DataFrames.

        The ratio = A_close / (B_close * hedge_ratio).
        This synthetic price represents the pair spread.

        Args:
            df_a: OHLCV for symbol A (numerator, e.g., BTC).
            df_b: OHLCV for symbol B (denominator, e.g., ETH).
            hedge_ratio: Multiplier for symbol B.

        Returns:
            DataFrame with ratio-based OHLCV and DatetimeIndex.
        """
        # Align indices (inner join — only bars present in both)
        common = df_a.index.intersection(df_b.index)
        a = df_a.loc[common]
        b = df_b.loc[common]

        ratio_close = a["close"] / (b["close"] * hedge_ratio)
        ratio_open = a["open"] / (b["open"] * hedge_ratio)
        ratio_high = a["high"] / (b["low"] * hedge_ratio)   # max ratio
        ratio_low = a["low"] / (b["high"] * hedge_ratio)    # min ratio
        avg_volume = (a["volume"] + b["volume"]) / 2

        return pd.DataFrame(
            {
                "open": ratio_open,
                "high": ratio_high,
                "low": ratio_low,
                "close": ratio_close,
                "volume": avg_volume,
            },
            index=common,
        )

    def _zscore(self, close: pd.Series) -> pd.Series:
        """Rolling z-score of log(ratio)."""
        log_ratio = np.log(close)
        period = self.params["zscore_period"]
        mean = log_ratio.rolling(period).mean()
        std = log_ratio.rolling(period).std(ddof=0)
        return (log_ratio - mean) / std.replace(0, pd.NA)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Long ratio entry: z-score below -entry_z.

        Interpretation: BTC underperforming ETH → expect reversion (BTC catches up).
        Action: Long BTC, Short ETH.
        """
        z = self._zscore(ohlcv["close"])
        return (z < -self.params["entry_z"]).fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Long exit: z reverts toward zero OR stop loss hit."""
        z = self._zscore(ohlcv["close"])
        # Mean reversion exit
        revert = z > -self.params["exit_z"]
        # Stop loss: spread diverges further
        stop = z < -self.params["stop_z"]
        return (revert | stop).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Short ratio entry: z-score above +entry_z.

        Interpretation: BTC outperforming ETH → expect reversion (ETH catches up).
        Action: Short BTC, Long ETH.
        """
        z = self._zscore(ohlcv["close"])
        return (z > self.params["entry_z"]).fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Short exit: z reverts toward zero OR stop loss hit."""
        z = self._zscore(ohlcv["close"])
        revert = z < self.params["exit_z"]
        stop = z > self.params["stop_z"]
        return (revert | stop).fillna(False)

    def run_backtest(
        self,
        ohlcv: pd.DataFrame,
        initial_capital: float = 10000.0,
        fees: float = 0.0004,
        slippage: float = 0.0002,
        leverage: float = 1.0,
        data_dir: Path | None = None,
    ) -> vbt.Portfolio:
        """Execute pair trading backtest.

        If ohlcv already contains ratio data (has 'close' column with
        reasonable values), uses it directly. Otherwise loads both symbols
        and computes the ratio internally.

        Fees are doubled since pair trading involves two legs.

        Args:
            ohlcv: OHLCV DataFrame. Can be pre-computed ratio or single symbol.
            initial_capital: Starting capital.
            fees: Per-leg trading fee rate.
            slippage: Per-leg slippage rate.
            leverage: Leverage multiplier.
            data_dir: Override kline data directory.

        Returns:
            VBT Portfolio object.
        """
        # Check if this is already ratio data (close values typically 20-40 for BTC/ETH)
        # or if we need to load and compute it
        mean_close = ohlcv["close"].mean() if "close" in ohlcv.columns else 0
        is_ratio = 5 < mean_close < 100  # BTC/ETH ratio is ~25-40

        if not is_ratio:
            try:
                df_a, df_b = self.load_pair_data(
                    self.required_symbols[0],
                    self.required_symbols[1],
                    self.required_timeframe,
                    data_dir,
                )
                ohlcv = self.compute_ratio_ohlcv(df_a, df_b, self.params["hedge_ratio"])
            except (FileNotFoundError, IndexError):
                pass  # Fall through with whatever ohlcv was provided

        close = ohlcv["close"]

        entries = self.generate_entries(ohlcv).fillna(False).astype(bool)
        exits = self.generate_exits(ohlcv).fillna(False).astype(bool)
        short_entries = self.generate_short_entries(ohlcv).fillna(False).astype(bool)
        short_exits = self.generate_short_exits(ohlcv).fillna(False).astype(bool)

        freq = FREQ_MAP.get(self.required_timeframe, self.required_timeframe)
        # Double fees: pair trading has 2 legs, each with its own fee + slippage
        total_fees = (fees + slippage) * 2

        portfolio = vbt.Portfolio.from_signals(
            close=close,
            entries=entries,
            exits=exits,
            short_entries=short_entries,
            short_exits=short_exits,
            init_cash=initial_capital,
            fees=total_fees,
            freq=freq,
            direction="both",
        )

        return portfolio
