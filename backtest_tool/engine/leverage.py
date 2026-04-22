"""Helpers for explicit leverage modeling on top of equity curves."""

from __future__ import annotations

import math

import pandas as pd


def apply_leverage_to_equity(
    equity: pd.Series,
    leverage: float,
    *,
    initial_capital: float | None = None,
) -> pd.Series:
    """Apply leverage by amplifying bar returns and re-compounding.

    This models a rebalanced leveraged strategy from an unleveraged equity curve.
    It is used because ``vectorbt`` percent sizing clips position size at 100%
    rather than borrowing cash for true margin exposure.
    """
    if equity.empty or leverage == 1:
        return equity

    base_capital = float(initial_capital if initial_capital is not None else equity.iloc[0])
    returns = equity.pct_change().fillna(0.0)
    leveraged = (1 + returns * leverage).cumprod() * base_capital
    leveraged.iloc[0] = base_capital
    leveraged.name = equity.name
    return leveraged


def annualization_factor_for_timeframe(timeframe: str) -> float:
    """Return the annualization factor used by the backtest scripts."""
    return {
        "4h": 365 * 6,
        "1d": 365,
        "1h": 365 * 24,
    }.get(timeframe, 365.0)


def summarize_equity_curve(
    equity: pd.Series,
    *,
    annualization_factor: float,
) -> dict[str, float]:
    """Compute summary metrics directly from an equity curve."""
    if equity.empty:
        return {
            "final_value": 0.0,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "max_drawdown": 0.0,
            "calmar_ratio": 0.0,
            "volatility_ann": 0.0,
        }

    start_value = float(equity.iloc[0])
    final_value = float(equity.iloc[-1])
    total_return = (final_value / start_value - 1) * 100 if start_value else 0.0

    returns = equity.pct_change().dropna()
    if len(returns) == 0:
        sharpe = 0.0
        sortino = 0.0
        volatility_ann = 0.0
    else:
        std = float(returns.std())
        sharpe = float(returns.mean() / std * math.sqrt(annualization_factor)) if std > 0 else 0.0
        downside = returns[returns < 0]
        downside_std = float(downside.std()) if len(downside) > 0 else 0.0
        sortino = (
            float(returns.mean() / downside_std * math.sqrt(annualization_factor))
            if downside_std > 0
            else 0.0
        )
        volatility_ann = std * math.sqrt(annualization_factor) * 100

    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    max_drawdown = float(drawdown.min()) * 100 if len(drawdown) > 0 else 0.0

    total_days = max((equity.index[-1] - equity.index[0]).days, 1)
    annualized_return = ((final_value / start_value) ** (365 / total_days) - 1) * 100 if start_value else 0.0
    calmar = annualized_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

    return {
        "final_value": final_value,
        "total_return": total_return,
        "annualized_return": annualized_return,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown": max_drawdown,
        "calmar_ratio": calmar,
        "volatility_ann": volatility_ann,
    }
