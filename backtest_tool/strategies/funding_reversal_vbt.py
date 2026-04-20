"""Strategy G4: Funding Rate Reversal (VectorBT).

Long-only contrarian strategy using actual Binance funding rate data.
When crowd is panic-shorting (extremely negative funding), buy the dip.
Uses momentum confirmation (ROC) to avoid catching falling knives.

Data analysis shows short-side (fading high positive funding) is
consistently unprofitable across all coins — high funding correlates
with strong bullish trends that persist. Long-side (buying panic dips)
generates positive alpha on XRP, BTC, ETH, and SOL.

Key differences from existing strategies:
- FundingArbVBT: Carry trade (collect funding). This: Fade panic dips.
- FundingContrarian: Uses ROC proxy. This: Uses actual funding_rate column.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import vectorbt as vbt

from backtest_tool.strategies.base_vbt import FREQ_MAP, BaseVBTStrategy

# Default path for funding rate parquet files
_FUNDING_DIR = Path(__file__).resolve().parent.parent / "data" / "funding"


class FundingReversalVBT(BaseVBTStrategy):
    """Funding rate reversal — long-only, buy extreme negative funding dips.

    Short side disabled by default: data shows fading high positive funding
    is consistently unprofitable (strong trends persist despite high funding).
    """

    name = "funding_reversal"
    required_timeframe = "8h"
    default_params: dict[str, Any] = {
        # Funding rate thresholds (annualized %)
        "entry_rate_long": -8.0,        # Go long when funding < this (crowd panic short)
        "entry_rate_short": 999.0,      # Disabled by default (short side unprofitable)
        "exit_rate_threshold": 3.0,     # Exit when |funding| drops below this
        # Momentum confirmation (ROC)
        "roc_period": 9,                # 9 bars x 8h = 3 days
        "roc_confirm_long": -2.0,       # Price must be falling to confirm long reversal
        "roc_confirm_short": 3.0,       # (unused when shorts disabled)
        # Holding limits
        "max_hold_days": 2,             # Short fade window
        # Confirmation
        "confirm_bars": 1,              # Require N consecutive extreme bars
        "leverage": 1,
    }

    @staticmethod
    def load_funding(symbol: str, funding_dir: Path | None = None) -> pd.DataFrame:
        """Load funding rate data for a symbol.

        Args:
            symbol: e.g. 'BTCUSDT'.
            funding_dir: Override directory. Defaults to backtest_tool/data/funding/.

        Returns:
            DataFrame with DatetimeIndex and 'funding_rate' column.
        """
        fdir = funding_dir or _FUNDING_DIR
        path = fdir / f"{symbol}.parquet"
        if not path.exists():
            return pd.DataFrame(columns=["funding_rate"])

        df = pd.read_parquet(path)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp")
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="first")]
        # Keep only funding_rate column
        if "funding_rate" in df.columns:
            return df[["funding_rate"]]
        return pd.DataFrame(columns=["funding_rate"])

    @staticmethod
    def prepare_data(
        klines: pd.DataFrame,
        funding: pd.DataFrame,
    ) -> pd.DataFrame:
        """Merge kline and funding rate data.

        Forward-fills funding rate onto kline bars and computes annualized rate.

        Args:
            klines: OHLCV DataFrame with DatetimeIndex.
            funding: DataFrame with 'funding_rate' column and DatetimeIndex.

        Returns:
            Merged DataFrame with 'funding_rate', 'annual_rate' columns.
        """
        df = klines.copy()

        if "funding_rate" in funding.columns and len(funding) > 0:
            fr = funding["funding_rate"].reindex(df.index, method="ffill")
            df["funding_rate"] = fr.fillna(0.0)
        else:
            df["funding_rate"] = 0.0

        # Annualized: funding_rate × 3 (per day at 8h) × 365 × 100
        df["annual_rate"] = df["funding_rate"] * 3 * 365 * 100
        return df

    def _compute_roc(self, close: pd.Series) -> pd.Series:
        """Rate of change over roc_period bars."""
        period = self.params["roc_period"]
        return close.pct_change(period) * 100

    def _consecutive_count(self, condition: pd.Series) -> pd.Series:
        """Count consecutive True bars. Resets to 0 on False."""
        groups = (~condition).cumsum()
        return condition.groupby(groups).cumcount() + condition.astype(int)

    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Long entry: extreme negative funding + price falling (reversal setup).

        Logic: crowd is panic-shorting (funding < -15%) AND price is dropping
        (ROC < -3%) for N consecutive bars → buy the dip.
        """
        if "annual_rate" not in ohlcv.columns:
            return pd.Series(False, index=ohlcv.index)

        annual = ohlcv["annual_rate"]
        roc = self._compute_roc(ohlcv["close"])

        # Extreme negative funding = crowd is short
        extreme_neg = annual < self.params["entry_rate_long"]
        # Price falling confirms panic
        price_falling = roc < self.params["roc_confirm_long"]

        combined = extreme_neg & price_falling
        # Require consecutive confirmation
        consec = self._consecutive_count(combined)
        entries = consec >= self.params["confirm_bars"]

        return entries.fillna(False)

    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Long exit: funding normalizes OR max hold exceeded."""
        if "annual_rate" not in ohlcv.columns:
            return pd.Series(True, index=ohlcv.index)

        annual = ohlcv["annual_rate"]
        exit_threshold = self.params["exit_rate_threshold"]

        # Funding returned to normal range
        rate_normalized = annual.abs() < exit_threshold

        # Max hold
        entry_signal = self.generate_entries(ohlcv)
        bars_since = self._bars_since_true(entry_signal)
        max_bars = self.params["max_hold_days"] * 3  # 8h = 3 bars/day
        hold_exit = bars_since >= max_bars

        return (rate_normalized | hold_exit).fillna(False)

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Short entry: extreme positive funding + price rising (overbought).

        Logic: crowd is over-leveraged long (funding > 25%) AND price is rising
        (ROC > 3%) for N consecutive bars → short the top.
        """
        if "annual_rate" not in ohlcv.columns:
            return pd.Series(False, index=ohlcv.index)

        annual = ohlcv["annual_rate"]
        roc = self._compute_roc(ohlcv["close"])

        extreme_pos = annual > self.params["entry_rate_short"]
        price_rising = roc > self.params["roc_confirm_short"]

        combined = extreme_pos & price_rising
        consec = self._consecutive_count(combined)
        entries = consec >= self.params["confirm_bars"]

        return entries.fillna(False)

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Short exit: funding normalizes OR max hold exceeded."""
        if "annual_rate" not in ohlcv.columns:
            return pd.Series(True, index=ohlcv.index)

        annual = ohlcv["annual_rate"]
        exit_threshold = self.params["exit_rate_threshold"]

        rate_normalized = annual.abs() < exit_threshold

        short_entry = self.generate_short_entries(ohlcv)
        bars_since = self._bars_since_true(short_entry)
        max_bars = self.params["max_hold_days"] * 3
        hold_exit = bars_since >= max_bars

        return (rate_normalized | hold_exit).fillna(False)

    @staticmethod
    def _bars_since_true(signal: pd.Series) -> pd.Series:
        """Count bars since last True signal."""
        groups = signal.cumsum()
        return groups.groupby(groups).cumcount()

    def run_backtest(
        self,
        ohlcv: pd.DataFrame,
        initial_capital: float = 10000.0,
        fees: float = 0.0004,
        slippage: float = 0.0002,
        leverage: float = 1.0,
        symbol: str = "",
        funding_dir: Path | None = None,
    ) -> vbt.Portfolio:
        """Execute backtest, auto-loading funding data if needed.

        If ohlcv already has 'annual_rate' column, uses it directly.
        Otherwise tries to load funding data from parquet files.

        Args:
            ohlcv: OHLCV DataFrame with DatetimeIndex.
            initial_capital: Starting capital.
            fees: Trading fee rate.
            slippage: Slippage rate.
            leverage: Leverage multiplier.
            symbol: Trading symbol (for loading funding data).
            funding_dir: Override funding data directory.

        Returns:
            VBT Portfolio object.
        """
        ohlcv = ohlcv.copy()

        # Auto-merge funding data if not already present
        if "annual_rate" not in ohlcv.columns:
            if symbol:
                funding_df = self.load_funding(symbol, funding_dir)
                if len(funding_df) > 0:
                    ohlcv = self.prepare_data(ohlcv, funding_df)
                else:
                    ohlcv["funding_rate"] = 0.0
                    ohlcv["annual_rate"] = 0.0
            else:
                ohlcv["funding_rate"] = 0.0
                ohlcv["annual_rate"] = 0.0

        close = ohlcv["close"]
        entries = self.generate_entries(ohlcv).fillna(False).astype(bool)
        exits = self.generate_exits(ohlcv).fillna(False).astype(bool)
        short_entries = self.generate_short_entries(ohlcv).fillna(False).astype(bool)
        short_exits = self.generate_short_exits(ohlcv).fillna(False).astype(bool)

        freq = FREQ_MAP.get(self.required_timeframe, self.required_timeframe)
        total_fees = fees + slippage

        # Only use "both" direction if shorts are actually generated
        has_shorts = short_entries.any()
        kwargs: dict[str, Any] = {
            "close": close,
            "entries": entries,
            "exits": exits,
            "init_cash": initial_capital,
            "fees": total_fees,
            "freq": freq,
            "direction": "both" if has_shorts else "longonly",
        }
        if has_shorts:
            kwargs["short_entries"] = short_entries
            kwargs["short_exits"] = short_exits

        portfolio = vbt.Portfolio.from_signals(**kwargs)

        return portfolio
