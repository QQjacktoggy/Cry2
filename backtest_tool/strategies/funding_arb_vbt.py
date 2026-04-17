"""Strategy A: Funding Rate Arbitrage (VectorBT).

Entry: Annualized funding rate > threshold → Short futures
Exit:  Annualized funding rate < exit_threshold OR hold > max_hold_days
v1: Only futures side (spot side assumed manual).

Reference: src/bot/strategy/funding_arb.py (event-driven version)
Note: Requires merged kline + funding rate data. The ohlcv DataFrame must
include a 'funding_rate' column aligned to kline timestamps.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import vectorbt as vbt

from backtest_tool.strategies.base_vbt import FREQ_MAP, BaseVBTStrategy


class FundingArbVBT(BaseVBTStrategy):
    """Funding rate arbitrage strategy for VectorBT."""

    name = "funding_arb"
    default_params: dict[str, Any] = {
        "funding_rate_threshold_annual": 15.0,
        "funding_rate_exit_annual": 5.0,
        "max_hold_days": 7,
        "leverage": 1,
        "delta_neutral": False,
    }
    required_timeframe = "8h"

    @staticmethod
    def prepare_data(
        klines: pd.DataFrame,
        funding: pd.DataFrame,
        spot_klines: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Merge kline, funding rate, and optional spot data.

        Funding rates are recorded every 8h. This method forward-fills
        the funding rate onto kline bars for signal generation.

        Args:
            klines: DataFrame with futures OHLCV data and DatetimeIndex.
            funding: DataFrame with funding_rate column and DatetimeIndex.
            spot_klines: Optional spot market OHLCV (for delta-neutral simulation).

        Returns:
            Merged DataFrame with 'funding_rate', 'annual_rate', and optionally
            'spot_return' and 'spot_cumulative' columns.
        """
        df = klines.copy()

        if "funding_rate" in funding.columns:
            # Reindex funding to kline index and forward-fill
            fr = funding["funding_rate"].reindex(df.index, method="ffill")
            df["funding_rate"] = fr.fillna(0.0)
        else:
            df["funding_rate"] = 0.0

        # Annualized rate: funding_rate × 3 (per day) × 365 × 100 (percentage)
        df["annual_rate"] = df["funding_rate"] * 3 * 365 * 100

        # Delta-neutral: add spot return (long spot to hedge short futures)
        if spot_klines is not None and not spot_klines.empty:
            spot_close = spot_klines["close"].reindex(df.index, method="ffill")
            spot_ret = spot_close.pct_change()
            df["spot_return"] = spot_ret.fillna(0.0)
            df["spot_cumulative"] = (1 + df["spot_return"]).cumprod() - 1
        else:
            df["spot_return"] = 0.0
            df["spot_cumulative"] = 0.0

        return df

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate long entry signals (not used — this strategy only shorts).

        Args:
            ohlcv: DataFrame with OHLCV data (must include 'annual_rate' column).

        Returns:
            Boolean Series (all False for funding arb — longs not applicable).
        """
        return pd.Series(False, index=ohlcv.index)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate long exit signals (not used).

        Args:
            ohlcv: DataFrame with OHLCV data.

        Returns:
            Boolean Series (all False).
        """
        return pd.Series(False, index=ohlcv.index)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate short entry signals: annual funding rate > threshold.

        When funding rate is high, short futures to collect funding payments.

        Args:
            ohlcv: DataFrame with OHLCV data and 'annual_rate' column.

        Returns:
            Boolean Series of short entry signals.
        """
        if "annual_rate" not in ohlcv.columns:
            return pd.Series(False, index=ohlcv.index)

        short_entries = ohlcv["annual_rate"] > self.params["funding_rate_threshold_annual"]
        return short_entries.fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate short exit signals: annual rate < exit threshold OR max hold exceeded.

        Args:
            ohlcv: DataFrame with OHLCV data and 'annual_rate' column.

        Returns:
            Boolean Series of short exit signals.
        """
        if "annual_rate" not in ohlcv.columns:
            return pd.Series(True, index=ohlcv.index)

        exit_threshold = self.params["funding_rate_exit_annual"]
        max_hold_bars = self._max_hold_bars()

        # Condition 1: Funding rate normalized
        rate_exit = ohlcv["annual_rate"].abs() < exit_threshold

        # Condition 2: Max hold time exceeded (approximate using bar count)
        # Create a rolling counter of consecutive short positions
        # Since we can't track exact position state in vectorized mode,
        # we use a simpler approach: exit after N bars since last entry signal
        short_entry_signal = ohlcv["annual_rate"] > self.params["funding_rate_threshold_annual"]
        bars_since_entry = self._bars_since_true(short_entry_signal)
        hold_exit = bars_since_entry >= max_hold_bars

        short_exits = rate_exit | hold_exit
        return short_exits.fillna(False)

    def _max_hold_bars(self) -> int:
        """Calculate max hold bars based on timeframe and max_hold_days.

        Returns:
            Number of bars corresponding to max_hold_days.
        """
        max_days = self.params["max_hold_days"]
        # 8h timeframe = 3 bars per day
        return max_days * 3

    @staticmethod
    def _bars_since_true(signal: pd.Series) -> pd.Series:
        """Count bars since last True signal.

        Args:
            signal: Boolean Series.

        Returns:
            Integer Series counting bars since last True value.
        """
        groups = signal.cumsum()
        result = groups.groupby(groups).cumcount()
        return result

    def run_backtest(
        self,
        ohlcv: pd.DataFrame,
        initial_capital: float = 10000.0,
        fees: float = 0.0004,
        slippage: float = 0.0002,
        leverage: float = 1.0,
    ) -> vbt.Portfolio:
        """Execute backtest (short-only strategy).

        Args:
            ohlcv: DataFrame with OHLCV data and 'annual_rate' column.
            initial_capital: Starting capital.
            fees: Trading fee rate.
            slippage: Slippage rate.
            leverage: Leverage multiplier.

        Returns:
            VBT Portfolio object.
        """
        ohlcv = ohlcv.copy()
        close = ohlcv["close"]

        entries = self.generate_entries(ohlcv).fillna(False).astype(bool)
        exits = self.generate_exits(ohlcv).fillna(False).astype(bool)
        short_entries = self.generate_short_entries(ohlcv).fillna(False).astype(bool)
        short_exits = self.generate_short_exits(ohlcv).fillna(False).astype(bool)

        freq = FREQ_MAP.get(self.required_timeframe, self.required_timeframe)
        total_fees = fees + slippage

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
