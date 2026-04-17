"""BaseVBTStrategy: Abstract base class for all VectorBT strategies.

Provides a unified interface for signal generation and backtest execution.
All strategies inherit this class and implement generate_entries/exits.
"""

from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from typing import Any

import numpy as np
import pandas as pd
import structlog
import vectorbt as vbt

logger = structlog.get_logger(__name__)

# Map timeframe string to pandas freq alias for VBT
FREQ_MAP: dict[str, str] = {
    "1m": "1min",
    "3m": "3min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1D",
}


class BaseVBTStrategy(ABC):
    """Abstract base class for VectorBT-based strategies."""

    name: str = "base"
    default_params: dict[str, Any] = {}
    required_timeframe: str = "4h"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        """Initialize strategy with merged parameters.

        Args:
            params: User-provided parameters (override defaults).
        """
        self.params = {**self.default_params, **(params or {})}

    @abstractmethod
    def generate_entries(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate long entry signals.

        Args:
            ohlcv: DataFrame with DatetimeIndex and open/high/low/close/volume columns.

        Returns:
            Boolean Series (True = enter long).
        """

    @abstractmethod
    def generate_exits(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Generate long exit signals.

        Args:
            ohlcv: DataFrame with DatetimeIndex and OHLCV columns.

        Returns:
            Boolean Series (True = exit long).
        """

    def generate_short_entries(self, ohlcv: pd.DataFrame) -> pd.Series | None:
        """Generate short entry signals (optional).

        Args:
            ohlcv: DataFrame with DatetimeIndex and OHLCV columns.

        Returns:
            Boolean Series or None (no shorting).
        """
        return None

    def generate_short_exits(self, ohlcv: pd.DataFrame) -> pd.Series | None:
        """Generate short exit signals (optional).

        Args:
            ohlcv: DataFrame with DatetimeIndex and OHLCV columns.

        Returns:
            Boolean Series or None.
        """
        return None

    def run_backtest(
        self,
        ohlcv: pd.DataFrame,
        initial_capital: float = 10000.0,
        fees: float = 0.0004,
        slippage: float = 0.0002,
        leverage: float = 1.0,
    ) -> vbt.Portfolio:
        """Execute backtest using VBT Portfolio.from_signals().

        Args:
            ohlcv: DataFrame with DatetimeIndex and OHLCV columns.
            initial_capital: Starting capital in USDT.
            fees: Trading fee rate (fraction, e.g., 0.0004 = 0.04%).
            slippage: Slippage rate (fraction, e.g., 0.0002 = 0.02%).
            leverage: Leverage multiplier.

        Returns:
            VBT Portfolio object with backtest results.
        """
        ohlcv = ohlcv.copy()
        close = ohlcv["close"]

        entries = self.generate_entries(ohlcv)
        exits = self.generate_exits(ohlcv)
        short_entries = self.generate_short_entries(ohlcv)
        short_exits = self.generate_short_exits(ohlcv)

        # Ensure boolean Series with no NaN
        entries = entries.fillna(False).astype(bool)
        exits = exits.fillna(False).astype(bool)

        freq = FREQ_MAP.get(self.required_timeframe, self.required_timeframe)

        # Effective fees = trading fee + slippage
        total_fees = fees + slippage

        kwargs: dict[str, Any] = {
            "close": close,
            "entries": entries,
            "exits": exits,
            "init_cash": initial_capital,
            "fees": total_fees,
            "freq": freq,
            "direction": "both" if short_entries is not None else "longonly",
        }

        if short_entries is not None:
            short_entries = short_entries.fillna(False).astype(bool)
            kwargs["short_entries"] = short_entries
        if short_exits is not None:
            short_exits = short_exits.fillna(False).astype(bool)
            kwargs["short_exits"] = short_exits

        portfolio = vbt.Portfolio.from_signals(**kwargs)

        logger.info(
            "Backtest complete",
            strategy=self.name,
            total_return=f"{portfolio.total_return():.2%}",
            total_trades=portfolio.trades.count(),
        )

        return portfolio

    def get_param_combinations(self, param_space: dict[str, list]) -> list[dict]:
        """Generate all parameter combinations from a parameter space.

        Args:
            param_space: Dict mapping param names to lists of values.

        Returns:
            List of parameter dicts (Cartesian product).
        """
        if not param_space:
            return [{}]

        keys = list(param_space.keys())
        values = list(param_space.values())
        combinations = []

        for combo in itertools.product(*values):
            combinations.append(dict(zip(keys, combo)))

        return combinations
