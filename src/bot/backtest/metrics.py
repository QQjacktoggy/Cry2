"""Performance metrics calculation.

Computes: Sharpe, Sortino, MaxDD, Calmar, win rate, profit factor,
annualized return, and more.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import structlog

from bot.utils.math_utils import safe_divide

logger = structlog.get_logger(__name__)


class MetricsCalculator:
    """Calculates comprehensive backtest performance metrics."""

    def __init__(
        self,
        equity_curve: list[tuple[int, float]],
        fills: list[Any],
        initial_capital: float = 10000.0,
        risk_free_rate: float = 0.0,
        periods_per_year: int = 365,
    ) -> None:
        self.initial_capital = initial_capital
        self.risk_free_rate = risk_free_rate
        self.periods_per_year = periods_per_year

        # Build equity series
        if equity_curve:
            timestamps, values = zip(*equity_curve)
            self.equity_series = pd.Series(values, index=pd.to_datetime(timestamps, unit="ms"))
        else:
            self.equity_series = pd.Series(dtype=float)

        # Build returns
        self.returns = self.equity_series.pct_change().dropna() if len(self.equity_series) > 1 else pd.Series(dtype=float)

        # Parse fills
        self._fills = fills
        self._trades = self._extract_trades()

    def _extract_trades(self) -> list[dict[str, Any]]:
        """Extract round-trip trades from fills."""
        trades: list[dict[str, Any]] = []
        for fill in self._fills:
            pnl = getattr(fill, "realized_pnl", 0.0)
            if pnl != 0:
                trades.append({
                    "symbol": getattr(fill, "symbol", ""),
                    "side": getattr(fill, "side", ""),
                    "quantity": getattr(fill, "quantity", 0),
                    "price": getattr(fill, "price", 0),
                    "pnl": pnl,
                    "commission": getattr(fill, "commission", 0),
                })
        return trades

    def calculate_all(self) -> dict[str, Any]:
        """Calculate all metrics."""
        return {
            "total_return": self.total_return(),
            "annualized_return": self.annualized_return(),
            "sharpe_ratio": self.sharpe_ratio(),
            "sortino_ratio": self.sortino_ratio(),
            "max_drawdown": self.max_drawdown(),
            "max_drawdown_pct": self.max_drawdown_pct(),
            "calmar_ratio": self.calmar_ratio(),
            "win_rate": self.win_rate(),
            "profit_factor": self.profit_factor(),
            "total_trades": self.total_trades(),
            "winning_trades": self.winning_trades(),
            "losing_trades": self.losing_trades(),
            "avg_win": self.avg_win(),
            "avg_loss": self.avg_loss(),
            "payoff_ratio": self.payoff_ratio(),
            "total_fees": self.total_fees(),
            "final_equity": self.final_equity(),
            "monthly_returns": self.monthly_returns(),
        }

    def total_return(self) -> float:
        """Total return as decimal."""
        if self.equity_series.empty:
            return 0.0
        return (self.equity_series.iloc[-1] - self.initial_capital) / self.initial_capital

    def annualized_return(self) -> float:
        """Annualized return."""
        total = self.total_return()
        if self.equity_series.empty or len(self.equity_series) < 2:
            return 0.0

        days = (self.equity_series.index[-1] - self.equity_series.index[0]).days
        if days <= 0:
            return 0.0

        if total <= -1.0:
            return -1.0
        return (1.0 + total) ** (365.0 / days) - 1.0

    def sharpe_ratio(self) -> float:
        """Annualized Sharpe ratio."""
        if self.returns.empty or self.returns.std() == 0:
            return 0.0
        excess_returns = self.returns - self.risk_free_rate / self.periods_per_year
        return float(
            excess_returns.mean() / excess_returns.std() * math.sqrt(self.periods_per_year)
        )

    def sortino_ratio(self) -> float:
        """Annualized Sortino ratio (using downside deviation)."""
        if self.returns.empty:
            return 0.0
        downside = self.returns[self.returns < 0]
        if downside.empty or downside.std() == 0:
            return float("inf") if self.returns.mean() > 0 else 0.0
        return float(
            self.returns.mean() / downside.std() * math.sqrt(self.periods_per_year)
        )

    def max_drawdown(self) -> float:
        """Maximum drawdown in absolute terms."""
        if self.equity_series.empty:
            return 0.0
        peak = self.equity_series.cummax()
        drawdown = self.equity_series - peak
        return float(drawdown.min())

    def max_drawdown_pct(self) -> float:
        """Maximum drawdown as percentage."""
        if self.equity_series.empty:
            return 0.0
        peak = self.equity_series.cummax()
        dd_pct = (self.equity_series - peak) / peak
        return float(dd_pct.min())

    def calmar_ratio(self) -> float:
        """Calmar ratio (annualized return / max drawdown)."""
        mdd = abs(self.max_drawdown_pct())
        if mdd == 0:
            return 0.0
        return safe_divide(self.annualized_return(), mdd)

    def win_rate(self) -> float:
        """Win rate (fraction of profitable trades)."""
        if not self._trades:
            return 0.0
        wins = sum(1 for t in self._trades if t["pnl"] > 0)
        return wins / len(self._trades)

    def profit_factor(self) -> float:
        """Profit factor (gross profit / gross loss)."""
        gross_profit = sum(t["pnl"] for t in self._trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in self._trades if t["pnl"] < 0))
        return safe_divide(gross_profit, gross_loss)

    def total_trades(self) -> int:
        """Total number of round-trip trades."""
        return len(self._trades)

    def winning_trades(self) -> int:
        """Number of winning trades."""
        return sum(1 for t in self._trades if t["pnl"] > 0)

    def losing_trades(self) -> int:
        """Number of losing trades."""
        return sum(1 for t in self._trades if t["pnl"] < 0)

    def avg_win(self) -> float:
        """Average winning trade PnL."""
        wins = [t["pnl"] for t in self._trades if t["pnl"] > 0]
        return safe_divide(sum(wins), len(wins)) if wins else 0.0

    def avg_loss(self) -> float:
        """Average losing trade PnL (negative)."""
        losses = [t["pnl"] for t in self._trades if t["pnl"] < 0]
        return safe_divide(sum(losses), len(losses)) if losses else 0.0

    def payoff_ratio(self) -> float:
        """Average win / average loss ratio."""
        avg_w = self.avg_win()
        avg_l = abs(self.avg_loss())
        return safe_divide(avg_w, avg_l)

    def monthly_returns(self) -> list[dict[str, Any]]:
        """Calculate per-month profit breakdown.

        Returns:
            List of dicts with year, month, start_equity, end_equity,
            profit, return_pct for each month in the equity curve.
        """
        if self.equity_series.empty or len(self.equity_series) < 2:
            return []

        # Resample equity to month-end values
        monthly_equity = self.equity_series.resample("ME").last().dropna()
        if monthly_equity.empty:
            return []

        results: list[dict[str, Any]] = []
        prev_equity = self.initial_capital

        for ts, end_eq in monthly_equity.items():
            profit = float(end_eq) - prev_equity
            ret_pct = profit / prev_equity if prev_equity != 0 else 0.0
            results.append({
                "year": ts.year,
                "month": ts.month,
                "start_equity": round(prev_equity, 2),
                "end_equity": round(float(end_eq), 2),
                "profit": round(profit, 2),
                "return_pct": round(ret_pct, 4),
            })
            prev_equity = float(end_eq)

        return results

    def total_fees(self) -> float:
        """Total commission paid."""
        return sum(t.get("commission", 0) for t in self._trades)

    def final_equity(self) -> float:
        """Final equity value."""
        if self.equity_series.empty:
            return self.initial_capital
        return float(self.equity_series.iloc[-1])
