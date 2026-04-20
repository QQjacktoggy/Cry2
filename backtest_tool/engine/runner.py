"""BacktestRunner: Execute single and multi-strategy backtests.

Orchestrates strategy execution, metric extraction, and result packaging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import inspect

import numpy as np
import pandas as pd
import structlog
import vectorbt as vbt
import yaml

from backtest_tool.engine.cost_model import CostModel
from backtest_tool.strategies.base_vbt import BaseVBTStrategy

logger = structlog.get_logger(__name__)


@dataclass
class BacktestResult:
    """Container for single-strategy backtest results."""

    strategy_name: str
    symbol: str
    timeframe: str
    portfolio: vbt.Portfolio
    metrics: dict[str, float]
    trades: pd.DataFrame
    equity_curve: pd.Series
    params: dict[str, Any]
    config: dict[str, Any]


@dataclass
class MultiBacktestResult:
    """Container for multi-strategy backtest results."""

    results: dict[str, BacktestResult]
    combined_equity: pd.Series
    combined_metrics: dict[str, float]
    allocations: dict[str, float]


class BacktestRunner:
    """Executes backtests for single or multiple strategies."""

    def __init__(self, config_path: str | None = None) -> None:
        """Initialize runner from config file.

        Args:
            config_path: Path to backtest_config.yaml. Uses defaults if None.
        """
        self.config: dict[str, Any] = {}
        if config_path and Path(config_path).exists():
            with open(config_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            self.config = raw.get("backtest", {})

        self.cost_model = CostModel.from_config(self.config) if self.config else CostModel()
        self.initial_capital = self.config.get("initial_capital", 10000.0)
        self.default_leverage = self.config.get("default_leverage", 2)

    def run_single(
        self,
        strategy: BaseVBTStrategy,
        ohlcv: pd.DataFrame,
        symbol: str = "",
        timeframe: str = "",
        initial_capital: float | None = None,
        leverage: float | None = None,
    ) -> BacktestResult:
        """Run a single strategy backtest.

        Args:
            strategy: VBT strategy instance.
            ohlcv: DataFrame with OHLCV data and DatetimeIndex.
            symbol: Trading symbol (e.g., 'BTCUSDT').
            timeframe: Candle timeframe (e.g., '4h').
            initial_capital: Override initial capital. Uses config default if None.
            leverage: Override leverage. Uses strategy param or config default.

        Returns:
            BacktestResult with all metrics and trade details.
        """
        capital = initial_capital or self.initial_capital
        lev = leverage or strategy.params.get("leverage", self.default_leverage)

        logger.info(
            "Running single backtest",
            strategy=strategy.name,
            capital=capital,
            leverage=lev,
        )

        # Build kwargs and call strategy.run_backtest with context when supported
        run_kwargs = {
            "ohlcv": ohlcv,
            "initial_capital": capital,
            "fees": self.cost_model.default_fee_rate,
            "slippage": self.cost_model.slippage_rate,
            "leverage": lev,
        }

        sig = inspect.signature(strategy.run_backtest)
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if "symbol" in sig.parameters or accepts_kwargs:
            run_kwargs["symbol"] = symbol
        if "timeframe" in sig.parameters or accepts_kwargs:
            run_kwargs["timeframe"] = timeframe or strategy.required_timeframe

        portfolio = strategy.run_backtest(**run_kwargs)

        metrics = self._extract_metrics(portfolio)
        trades = self._extract_trades(portfolio)
        equity = portfolio.value()

        # Add context metrics
        metrics["initial_capital"] = capital
        metrics["leverage"] = lev
        metrics["final_value"] = float(equity.iloc[-1]) if len(equity) > 0 else capital
        metrics["total_pnl"] = metrics["final_value"] - capital

        # Convert fractions to percentages for display
        for key in ("total_return", "annualized_return", "max_drawdown", "win_rate", "volatility_ann"):
            if key in metrics:
                metrics[key] = metrics[key] * 100
        # Alias
        metrics["annual_return"] = metrics.get("annualized_return", 0.0)

        # Cost model info
        metrics["maker_fee"] = self.cost_model.maker_rate * 100
        metrics["taker_fee"] = self.cost_model.taker_rate * 100
        metrics["slippage_bps"] = self.cost_model.slippage_bps

        config_summary = {
            **self.cost_model.summary(),
            "initial_capital": capital,
            "leverage": lev,
            "timeframe": strategy.required_timeframe,
        }

        return BacktestResult(
            strategy_name=strategy.name,
            symbol=symbol,
            timeframe=timeframe or strategy.required_timeframe,
            portfolio=portfolio,
            metrics=metrics,
            trades=trades,
            equity_curve=equity,
            params=strategy.params.copy(),
            config=config_summary,
        )

    def run_multi(
        self,
        strategies: list[BaseVBTStrategy],
        data: dict[str, pd.DataFrame],
        allocations: dict[str, float],
    ) -> MultiBacktestResult:
        """Run multi-strategy backtest with capital allocation.

        Each strategy runs independently with its allocated capital.
        Equity curves are combined with weighted sum.

        Args:
            strategies: List of VBT strategy instances.
            data: Dict mapping strategy name to OHLCV DataFrame.
            allocations: Dict mapping strategy name to allocation fraction (sum = 1.0).

        Returns:
            MultiBacktestResult with combined metrics.
        """
        total_alloc = sum(allocations.values())
        if abs(total_alloc - 1.0) > 0.01:
            logger.warning("Allocations sum to %.2f, normalizing to 1.0", total_alloc)
            allocations = {k: v / total_alloc for k, v in allocations.items()}

        results: dict[str, BacktestResult] = {}
        equity_curves: list[pd.Series] = []

        for strategy in strategies:
            name = strategy.name
            if name not in data:
                logger.warning("No data for strategy %s, skipping", name)
                continue

            alloc = allocations.get(name, 0.0)
            if alloc <= 0:
                continue

            capital = self.initial_capital * alloc
            result = self.run_single(strategy, data[name], symbol=name, initial_capital=capital)
            results[name] = result
            equity_curves.append(result.equity_curve)

        # Combine equity curves
        if equity_curves:
            combined = equity_curves[0].copy()
            for ec in equity_curves[1:]:
                # Align indices and add
                combined, ec_aligned = combined.align(ec, fill_value=0)
                combined = combined + ec_aligned
            combined_metrics = self._compute_combined_metrics(combined)
        else:
            combined = pd.Series(dtype=float)
            combined_metrics = {}

        return MultiBacktestResult(
            results=results,
            combined_equity=combined,
            combined_metrics=combined_metrics,
            allocations=allocations,
        )

    def _extract_metrics(self, portfolio: vbt.Portfolio) -> dict[str, float]:
        """Extract all performance metrics from VBT Portfolio.

        Args:
            portfolio: VBT Portfolio object.

        Returns:
            Dict of metric name → value.
        """
        stats = portfolio.stats()

        # Map VBT stats to our metric names
        metrics: dict[str, float] = {}

        try:
            metrics["total_return"] = float(portfolio.total_return())
        except Exception:
            metrics["total_return"] = 0.0

        try:
            # Annualized return
            total_days = (portfolio.wrapper.index[-1] - portfolio.wrapper.index[0]).days
            if total_days > 0:
                total_ret = 1 + metrics["total_return"]
                metrics["annualized_return"] = total_ret ** (365.0 / total_days) - 1
            else:
                metrics["annualized_return"] = 0.0
        except Exception:
            metrics["annualized_return"] = 0.0

        # Sharpe, Sortino, Calmar
        try:
            equity = portfolio.value()
            returns = equity.pct_change().dropna()
            if len(returns) > 1 and returns.std() > 0:
                metrics["sharpe_ratio"] = float(returns.mean() / returns.std() * np.sqrt(365 * 24 / self._hours_per_bar(portfolio)))
            else:
                metrics["sharpe_ratio"] = 0.0
        except Exception:
            metrics["sharpe_ratio"] = 0.0

        try:
            equity = portfolio.value()
            returns = equity.pct_change().dropna()
            downside = returns[returns < 0]
            if len(downside) > 1 and downside.std() > 0:
                metrics["sortino_ratio"] = float(returns.mean() / downside.std() * np.sqrt(365 * 24 / self._hours_per_bar(portfolio)))
            else:
                metrics["sortino_ratio"] = 0.0
        except Exception:
            metrics["sortino_ratio"] = 0.0

        # Max Drawdown
        try:
            metrics["max_drawdown"] = float(portfolio.max_drawdown())
        except Exception:
            metrics["max_drawdown"] = 0.0

        # Max Drawdown Duration
        try:
            dd = portfolio.drawdown()
            if len(dd) > 0:
                in_dd = dd < 0
                groups = (~in_dd).cumsum()
                dd_lengths = in_dd.groupby(groups).sum()
                metrics["max_dd_duration_bars"] = float(dd_lengths.max()) if len(dd_lengths) > 0 else 0.0
            else:
                metrics["max_dd_duration_bars"] = 0.0
        except Exception:
            metrics["max_dd_duration_bars"] = 0.0

        # Calmar ratio
        if metrics["max_drawdown"] != 0:
            metrics["calmar_ratio"] = metrics["annualized_return"] / abs(metrics["max_drawdown"])
        else:
            metrics["calmar_ratio"] = 0.0

        # Trade statistics
        try:
            trades = portfolio.trades
            trade_count = trades.count()
            metrics["total_trades"] = float(trade_count)

            if trade_count > 0:
                records = trades.records_readable
                pnl = records["PnL"] if "PnL" in records.columns else pd.Series(dtype=float)

                if len(pnl) > 0:
                    wins = pnl[pnl > 0]
                    losses = pnl[pnl < 0]

                    metrics["win_rate"] = len(wins) / len(pnl)
                    metrics["avg_trade_pnl"] = float(pnl.mean())
                    metrics["avg_win"] = float(wins.mean()) if len(wins) > 0 else 0.0
                    metrics["avg_loss"] = float(losses.mean()) if len(losses) > 0 else 0.0
                    metrics["best_trade"] = float(pnl.max())
                    metrics["worst_trade"] = float(pnl.min())
                    metrics["profit_factor"] = float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
                    metrics["payoff_ratio"] = abs(metrics["avg_win"] / metrics["avg_loss"]) if metrics["avg_loss"] != 0 else float("inf")
                    metrics["net_profit"] = float(pnl.sum())
                    metrics["total_fees"] = float(records["Fees"].sum()) if "Fees" in records.columns else 0.0
                else:
                    self._set_zero_trade_metrics(metrics)
            else:
                self._set_zero_trade_metrics(metrics)
        except Exception:
            self._set_zero_trade_metrics(metrics)

        # Volatility (annualized)
        try:
            equity = portfolio.value()
            returns = equity.pct_change().dropna()
            hours = self._hours_per_bar(portfolio)
            metrics["volatility_ann"] = float(returns.std() * np.sqrt(365 * 24 / hours))
        except Exception:
            metrics["volatility_ann"] = 0.0

        return metrics

    def _hours_per_bar(self, portfolio: vbt.Portfolio) -> float:
        """Estimate hours per bar from the portfolio's index.

        Args:
            portfolio: VBT Portfolio.

        Returns:
            Estimated hours per bar (default 4 if can't determine).
        """
        try:
            idx = portfolio.wrapper.index
            if len(idx) > 1:
                diff = (idx[1] - idx[0]).total_seconds() / 3600
                return max(diff, 0.0167)  # Min 1 minute
        except Exception:
            pass
        return 4.0

    @staticmethod
    def _set_zero_trade_metrics(metrics: dict) -> None:
        """Set all trade-related metrics to zero."""
        for key in [
            "win_rate", "avg_trade_pnl", "avg_win", "avg_loss",
            "best_trade", "worst_trade", "profit_factor", "payoff_ratio",
            "net_profit", "total_fees", "total_trades",
        ]:
            metrics.setdefault(key, 0.0)

    def _extract_trades(self, portfolio: vbt.Portfolio) -> pd.DataFrame:
        """Extract readable trade log from VBT Portfolio.

        Args:
            portfolio: VBT Portfolio object.

        Returns:
            DataFrame with trade details.
        """
        try:
            records = portfolio.trades.records_readable
            if records.empty:
                return pd.DataFrame()
            return records.copy()
        except Exception:
            return pd.DataFrame()

    def _compute_combined_metrics(self, equity: pd.Series) -> dict[str, float]:
        """Compute metrics for a combined equity curve.

        Args:
            equity: Combined portfolio equity Series.

        Returns:
            Dict of combined metrics.
        """
        metrics: dict[str, float] = {}

        if equity.empty or len(equity) < 2:
            return metrics

        initial = equity.iloc[0]
        final = equity.iloc[-1]

        metrics["total_return"] = (final - initial) / initial if initial > 0 else 0.0

        total_days = (equity.index[-1] - equity.index[0]).days
        if total_days > 0:
            metrics["annualized_return"] = (1 + metrics["total_return"]) ** (365.0 / total_days) - 1
        else:
            metrics["annualized_return"] = 0.0

        returns = equity.pct_change().dropna()
        if len(returns) > 1 and returns.std() > 0:
            hours_per_bar = max((equity.index[1] - equity.index[0]).total_seconds() / 3600, 0.0167)
            metrics["sharpe_ratio"] = float(returns.mean() / returns.std() * np.sqrt(365 * 24 / hours_per_bar))
        else:
            metrics["sharpe_ratio"] = 0.0

        # Max drawdown
        cummax = equity.cummax()
        drawdown = (equity - cummax) / cummax
        metrics["max_drawdown"] = float(drawdown.min())

        return metrics
