"""Risk-adjusted performance metrics for backtest results.

Used by scripts/optimize.py and scripts/walk_forward.py to score parameter
candidates by something more than raw net_profit (which over-rewards
high-drawdown / over-fit configurations).

All inputs are plain floats / lists so the module stays pure for unit tests.
"""

from __future__ import annotations

import math

ANNUAL_TRADING_DAYS = 252


def compute_sharpe(daily_returns_pct: list[float]) -> float:
    """Annualised Sharpe (rf = 0). Returns 0.0 when stddev is undefined.

    `daily_returns_pct` is a list of per-day returns expressed in percent
    (e.g. 0.5 = +0.5% on the day). With <2 samples we can't form a stddev,
    so 0.0 is returned as a "no signal" fallback.
    """
    if len(daily_returns_pct) < 2:
        return 0.0
    mean = sum(daily_returns_pct) / len(daily_returns_pct)
    variance = sum((r - mean) ** 2 for r in daily_returns_pct) / (len(daily_returns_pct) - 1)
    if variance <= 0:
        return 0.0
    stddev = math.sqrt(variance)
    return mean / stddev * math.sqrt(ANNUAL_TRADING_DAYS)


def compute_calmar(roi_pct: float, days: int, max_dd_pct: float) -> float:
    """Calmar ≈ annualised return / max drawdown (both in %).

    Returns 0 if max_dd_pct ≤ 0 (no drawdown means undefined Calmar; we treat
    it as zero so it doesn't dominate the composite when DD just hasn't been
    measured). Annualises ROI by the actual sample length.
    """
    if days <= 0 or max_dd_pct <= 0:
        return 0.0
    annualised = roi_pct * (365.0 / days)
    return annualised / max_dd_pct


def compute_score(
    sharpe: float,
    calmar: float,
    roi_pct: float,
    max_dd_pct: float,
    max_dd_reject_pct: float = 15.0,
    sharpe_weight: float = 0.5,
    calmar_weight: float = 0.3,
    roi_weight: float = 0.2,
) -> float:
    """Composite score: 0.5×Sharpe + 0.3×Calmar + 0.2×ROI%.

    If max_dd_pct exceeds the rejection threshold, returns -math.inf so the
    candidate cannot be picked as best regardless of headline numbers.
    """
    if max_dd_pct > max_dd_reject_pct:
        return -math.inf
    return sharpe * sharpe_weight + calmar * calmar_weight + roi_pct * roi_weight


def score_from_results(results: dict, max_dd_reject_pct: float = 15.0) -> float:
    """Convenience wrapper: pull the inputs we need out of a BacktestEngine
    results dict and return the composite score."""
    pnl = results.get("pnl", {})
    risk = results.get("risk", {})
    period = results.get("period", {})
    days = max(int(period.get("days", 1)), 1)
    capital = max(float(results.get("capital", 1.0)), 1e-9)
    max_dd = float(risk.get("max_drawdown", 0.0))
    max_dd_pct = max_dd / capital * 100.0
    daily_returns_pct = [
        float(d.get("profit", 0.0)) / capital * 100.0
        for d in results.get("daily_detail", {}).values()
    ]
    sharpe = compute_sharpe(daily_returns_pct)
    calmar = compute_calmar(float(pnl.get("roi_pct", 0.0)), days, max_dd_pct)
    return compute_score(
        sharpe=sharpe,
        calmar=calmar,
        roi_pct=float(pnl.get("roi_pct", 0.0)),
        max_dd_pct=max_dd_pct,
        max_dd_reject_pct=max_dd_reject_pct,
    )
