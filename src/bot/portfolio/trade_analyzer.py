"""Trade Analyzer — bridge between TradeJournal (live/paper SQLite)
and backtest_tool analytics/robustness functions.

Reads fills and trades from a TradeJournal instance, reconstructs
equity curves, and feeds them into the backtest_tool analytics engine
for correlation analysis, rolling Sharpe, strategy health reports,
and Monte Carlo robustness checks.

Usage::

    from bot.portfolio.trade_journal import TradeJournal
    from bot.portfolio.trade_analyzer import TradeAnalyzer

    journal = TradeJournal("./data/trades.db")
    analyzer = TradeAnalyzer(journal)

    curves = analyzer.build_equity_curves()
    report = analyzer.health_report()
    corr   = analyzer.correlation_matrix()
    sharpe = analyzer.rolling_sharpe("my_strategy")
    summary = analyzer.trade_summary()
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Ensure both repo root and src/ are importable so that
# ``from backtest_tool.engine...`` and ``from bot...`` both resolve.
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent          # src/bot/portfolio
_SRC_DIR = _THIS_DIR.parent.parent                   # src/
_REPO_ROOT = _SRC_DIR.parent                         # repo root

for _p in (_SRC_DIR, _REPO_ROOT):
    _ps = str(_p)
    if _ps not in sys.path:
        sys.path.insert(0, _ps)

from backtest_tool.engine.analytics import (
    compute_correlation_matrix,
    compute_rolling_sharpe,
    compute_strategy_health_report,
)
from backtest_tool.engine.robustness import monte_carlo_simulation
from bot.portfolio.trade_journal import TradeJournal

DEFAULT_INITIAL_CAPITAL = 10_000.0


class TradeAnalyzer:
    """Bridge that feeds TradeJournal data into backtest_tool analytics.

    Args:
        journal: An initialised :class:`TradeJournal` instance.
        initial_capital: Starting equity used when reconstructing curves.
    """

    def __init__(
        self,
        journal: TradeJournal,
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    ) -> None:
        self.journal = journal
        self.initial_capital = initial_capital

    # ------------------------------------------------------------------
    # Equity curve construction
    # ------------------------------------------------------------------

    def build_equity_curves(
        self,
        strategy: str | None = None,
        symbol: str | None = None,
    ) -> dict[str, pd.Series]:
        """Reconstruct per-strategy equity curves from recorded fills.

        Each curve starts at *initial_capital* and applies every fill's
        ``realized_pnl`` in chronological order.  When *strategy* is
        ``None`` one curve per unique strategy name is returned.

        Args:
            strategy: Limit to a single strategy (or ``None`` for all).
            symbol: Limit to a single symbol (or ``None`` for all).

        Returns:
            Dict mapping strategy name → equity ``pd.Series`` indexed by
            timestamp.
        """
        fills = self.journal.export_fills(strategy=strategy, symbol=symbol)

        if fills.empty:
            return {}

        curves: dict[str, pd.Series] = {}
        for strat_name, group in fills.groupby("strategy"):
            group = group.sort_values("timestamp")
            cumulative_pnl = group["realized_pnl"].cumsum()
            equity = self.initial_capital + cumulative_pnl
            equity.index = group["timestamp"]
            equity.name = str(strat_name)
            curves[str(strat_name)] = equity

        return curves

    # ------------------------------------------------------------------
    # Analytics wrappers
    # ------------------------------------------------------------------

    def health_report(
        self,
        strategy: str | None = None,
        symbol: str | None = None,
        **kwargs: Any,
    ) -> dict[str, dict[str, Any]]:
        """Strategy health report (rolling Sharpe + decay detection).

        Keyword arguments are forwarded to
        :func:`compute_strategy_health_report`.

        Returns:
            Dict mapping strategy name → health metrics dict.
        """
        curves = self.build_equity_curves(strategy=strategy, symbol=symbol)
        if not curves:
            return {}
        return compute_strategy_health_report(curves, **kwargs)

    def correlation_matrix(
        self,
        strategy: str | None = None,
        symbol: str | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """Cross-strategy return correlation matrix.

        Keyword arguments are forwarded to
        :func:`compute_correlation_matrix`.

        Returns:
            N×N correlation DataFrame.
        """
        curves = self.build_equity_curves(strategy=strategy, symbol=symbol)
        if not curves:
            return pd.DataFrame()
        return compute_correlation_matrix(curves, **kwargs)

    def rolling_sharpe(
        self,
        strategy: str,
        symbol: str | None = None,
        **kwargs: Any,
    ) -> pd.Series:
        """Rolling Sharpe ratio for a single strategy.

        Keyword arguments are forwarded to
        :func:`compute_rolling_sharpe`.

        Args:
            strategy: Strategy name (required).
            symbol: Optional symbol filter.

        Returns:
            Series of rolling Sharpe values.
        """
        curves = self.build_equity_curves(strategy=strategy, symbol=symbol)
        if strategy not in curves:
            return pd.Series(dtype=float)
        return compute_rolling_sharpe(curves[strategy], **kwargs)

    def monte_carlo(
        self,
        strategy: str,
        symbol: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run Monte Carlo simulation on a strategy's equity curve.

        Keyword arguments are forwarded to
        :func:`monte_carlo_simulation`.

        Args:
            strategy: Strategy name (required).
            symbol: Optional symbol filter.

        Returns:
            Dict with percentile statistics and distribution info.
        """
        curves = self.build_equity_curves(strategy=strategy, symbol=symbol)
        if strategy not in curves:
            return {"error": "No equity curve available for strategy"}
        return monte_carlo_simulation(curves[strategy], **kwargs)

    # ------------------------------------------------------------------
    # Enhanced summary
    # ------------------------------------------------------------------

    def trade_summary(
        self,
        strategy: str | None = None,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """Enhanced trade summary with per-strategy breakdown.

        Augments :meth:`TradeJournal.summary` with a breakdown for every
        strategy present in the journal.

        Returns:
            Dict with ``overall`` summary and ``per_strategy`` dict.
        """
        overall = self.journal.summary(strategy=strategy, symbol=symbol)

        # Per-strategy breakdown
        fills = self.journal.export_fills(strategy=strategy, symbol=symbol)
        if fills.empty:
            return {"overall": overall, "per_strategy": {}}

        strategies = fills["strategy"].unique().tolist()
        per_strategy: dict[str, dict[str, Any]] = {}
        for strat in strategies:
            per_strategy[strat] = self.journal.summary(
                strategy=strat, symbol=symbol
            )

        return {"overall": overall, "per_strategy": per_strategy}
