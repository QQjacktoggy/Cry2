"""Portfolio analytics: correlation analysis and rolling performance metrics.

Phase 4 implementation:
  4C: Cross-strategy correlation matrix
  4D: Rolling Sharpe ratio (30d / 90d windows)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


# =============================================================================
# 4C: Cross-Strategy Correlation Analysis
# =============================================================================


def compute_correlation_matrix(
    equity_curves: dict[str, pd.Series],
    method: str = "pearson",
) -> pd.DataFrame:
    """Compute return correlation matrix across strategies.

    Converts equity curves to daily returns, then computes pairwise correlation.

    Args:
        equity_curves: Dict mapping strategy name → equity curve Series.
        method: Correlation method ('pearson', 'spearman', 'kendall').

    Returns:
        DataFrame correlation matrix (N×N where N = number of strategies).
    """
    if len(equity_curves) < 2:
        names = list(equity_curves.keys())
        return pd.DataFrame(1.0, index=names, columns=names)

    returns_df = pd.DataFrame(
        {name: eq.pct_change().fillna(0.0) for name, eq in equity_curves.items()}
    )
    return returns_df.corr(method=method)


def find_low_correlation_pairs(
    corr_matrix: pd.DataFrame,
    threshold: float = 0.3,
) -> list[dict[str, Any]]:
    """Find strategy pairs with correlation below threshold.

    Low-correlation pairs are ideal for portfolio diversification.

    Args:
        corr_matrix: Correlation matrix from compute_correlation_matrix().
        threshold: Maximum correlation to qualify as "low".

    Returns:
        List of dicts with 'strategy_a', 'strategy_b', 'correlation'.
        Sorted by correlation ascending (most diversified first).
    """
    pairs: list[dict[str, Any]] = []
    names = corr_matrix.columns.tolist()

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            corr_val = corr_matrix.iloc[i, j]
            if abs(corr_val) < threshold:
                pairs.append(
                    {
                        "strategy_a": names[i],
                        "strategy_b": names[j],
                        "correlation": round(float(corr_val), 4),
                    }
                )

    return sorted(pairs, key=lambda x: abs(x["correlation"]))


def compute_correlation_summary(
    corr_matrix: pd.DataFrame,
) -> dict[str, Any]:
    """Summarize correlation matrix statistics.

    Returns:
        Dict with mean_correlation, max_pair, min_pair, and per-strategy averages.
    """
    names = corr_matrix.columns.tolist()
    n = len(names)
    if n < 2:
        return {"mean_correlation": 0.0, "n_strategies": n}

    # Extract upper triangle (excluding diagonal)
    mask = np.triu(np.ones_like(corr_matrix, dtype=bool), k=1)
    upper_vals = corr_matrix.where(mask).stack()

    # Per-strategy average correlation with others
    per_strategy = {}
    for name in names:
        others = corr_matrix.loc[name].drop(name)
        per_strategy[name] = round(float(others.mean()), 4)

    max_idx = upper_vals.idxmax()
    min_idx = upper_vals.idxmin()

    return {
        "n_strategies": n,
        "n_pairs": len(upper_vals),
        "mean_correlation": round(float(upper_vals.mean()), 4),
        "median_correlation": round(float(upper_vals.median()), 4),
        "max_pair": {
            "strategies": list(max_idx),
            "correlation": round(float(upper_vals[max_idx]), 4),
        },
        "min_pair": {
            "strategies": list(min_idx),
            "correlation": round(float(upper_vals[min_idx]), 4),
        },
        "per_strategy_avg": per_strategy,
    }


# =============================================================================
# 4D: Rolling Sharpe Ratio
# =============================================================================


def compute_rolling_sharpe(
    equity: pd.Series,
    window: int = 30,
    annualization_factor: float = 252.0,
    risk_free_rate: float = 0.0,
) -> pd.Series:
    """Compute rolling Sharpe ratio over a sliding window.

    Args:
        equity: Equity curve (cumulative value, not returns).
        window: Rolling window size in bars.
        annualization_factor: Bars per year (252 for daily, 365*6 for 4h).
        risk_free_rate: Annual risk-free rate (default 0).

    Returns:
        Series of rolling Sharpe values.
    """
    returns = equity.pct_change().fillna(0.0)
    rf_per_bar = risk_free_rate / annualization_factor

    excess = returns - rf_per_bar
    rolling_mean = excess.rolling(window=window, min_periods=max(window // 2, 2)).mean()
    rolling_std = returns.rolling(window=window, min_periods=max(window // 2, 2)).std()

    sharpe = (rolling_mean / rolling_std.replace(0, np.nan)) * np.sqrt(
        annualization_factor
    )
    return sharpe


def compute_multi_window_sharpe(
    equity: pd.Series,
    windows: list[int] | None = None,
    annualization_factor: float = 252.0,
) -> pd.DataFrame:
    """Compute rolling Sharpe at multiple window sizes.

    Default windows: 30d and 90d equivalents.

    Args:
        equity: Equity curve.
        windows: List of window sizes in bars.
        annualization_factor: Bars per year.

    Returns:
        DataFrame with columns ['sharpe_{window}' for each window].
    """
    if windows is None:
        windows = [30, 90]

    result = pd.DataFrame(index=equity.index)
    for w in windows:
        col_name = f"sharpe_{w}"
        result[col_name] = compute_rolling_sharpe(
            equity, window=w, annualization_factor=annualization_factor
        )
    return result


def detect_strategy_decay(
    rolling_sharpe: pd.Series,
    decay_threshold: float = -1.0,
    min_consecutive: int = 5,
) -> pd.Series:
    """Detect periods where a strategy's rolling Sharpe indicates decay.

    A strategy is "decaying" when its rolling Sharpe falls below the
    decay threshold for at least min_consecutive bars.

    Args:
        rolling_sharpe: Rolling Sharpe ratio series.
        decay_threshold: Sharpe below this = potential decay.
        min_consecutive: Minimum consecutive bars to confirm decay.

    Returns:
        Boolean Series (True = strategy is in decay period).
    """
    below = rolling_sharpe < decay_threshold
    # Count consecutive bars below threshold
    groups = (~below).cumsum()
    consec = below.groupby(groups).cumsum()
    return consec >= min_consecutive


def compute_strategy_health_report(
    equity_curves: dict[str, pd.Series],
    window_short: int = 30,
    window_long: int = 90,
    annualization_factor: float = 252.0,
    decay_threshold: float = -1.0,
) -> dict[str, dict[str, Any]]:
    """Generate health report for each strategy.

    Includes current rolling Sharpe at multiple windows and decay detection.

    Args:
        equity_curves: Dict mapping strategy name → equity curve.
        window_short: Short rolling window (e.g., 30 bars).
        window_long: Long rolling window (e.g., 90 bars).
        annualization_factor: Bars per year.
        decay_threshold: Sharpe threshold for decay detection.

    Returns:
        Dict mapping strategy name → health metrics.
    """
    report: dict[str, dict[str, Any]] = {}

    for name, equity in equity_curves.items():
        sharpe_short = compute_rolling_sharpe(
            equity, window_short, annualization_factor
        )
        sharpe_long = compute_rolling_sharpe(
            equity, window_long, annualization_factor
        )
        is_decaying = detect_strategy_decay(sharpe_long, decay_threshold)

        current_short = float(sharpe_short.iloc[-1]) if not sharpe_short.empty else 0.0
        current_long = float(sharpe_long.iloc[-1]) if not sharpe_long.empty else 0.0
        currently_decaying = bool(is_decaying.iloc[-1]) if not is_decaying.empty else False

        # Peak-to-trough equity drawdown
        peak = equity.cummax()
        dd = (equity - peak) / peak.replace(0, np.nan)
        current_dd = float(dd.iloc[-1]) if not dd.empty else 0.0

        report[name] = {
            f"sharpe_{window_short}d": round(current_short, 3),
            f"sharpe_{window_long}d": round(current_long, 3),
            "is_decaying": currently_decaying,
            "current_drawdown": round(current_dd, 4),
            "total_return": round(
                float((equity.iloc[-1] / equity.iloc[0] - 1) * 100), 2
            )
            if len(equity) > 1
            else 0.0,
        }

    return report
