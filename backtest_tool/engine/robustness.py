"""Robustness validation: walk-forward, Monte Carlo, stress testing, parameter stability, fee sensitivity.

Phase 5 implementation:
  5A: Multi-objective portfolio optimization (extends PortfolioOptimizer)
  5B: Walk-Forward analysis (train/test split validation)
  5C: Monte Carlo simulation (PnL shuffle)
  5D: Parameter stability analysis (neighbour sensitivity)
  5E: Stress testing (extreme event periods)
  5F: Fee sensitivity / breakeven analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


# =============================================================================
# 5A: Multi-Objective Portfolio Optimization (helper utilities)
# =============================================================================


def optimize_multi_objective(
    equity_curves: dict[str, pd.Series],
    objectives: list[str] | None = None,
    allocation_step: float = 0.10,
    max_allocation: float = 0.50,
) -> dict[str, Any]:
    """Find optimal allocation across multiple objectives.

    Tests allocations by Sharpe, Calmar, and minimum-drawdown objectives,
    returning the best for each.

    Args:
        equity_curves: Dict mapping strategy name → equity curve.
        objectives: List of objectives ('sharpe', 'calmar', 'min_dd', 'equal').
        allocation_step: Weight step size (e.g., 0.10 = 10%).
        max_allocation: Maximum weight per strategy.

    Returns:
        Dict with best allocation per objective.
    """
    if objectives is None:
        objectives = ["sharpe", "calmar", "min_dd", "equal"]

    names = list(equity_curves.keys())
    n = len(names)
    if n == 0:
        return {}

    # Build returns matrix
    returns_df = pd.DataFrame(
        {name: eq.pct_change().fillna(0.0) for name, eq in equity_curves.items()}
    )

    # Generate allocation grid
    import itertools

    possible = [round(v * allocation_step, 4) for v in range(int(1 / allocation_step) + 1)
                if v * allocation_step <= max_allocation + 1e-9]
    possible = [p for p in possible if p <= max_allocation]
    if 0.0 not in possible:
        possible.insert(0, 0.0)

    valid_allocs = []
    for combo in itertools.product(possible, repeat=n):
        if abs(sum(combo) - 1.0) < 1e-6:
            valid_allocs.append(combo)

    if not valid_allocs:
        equal_w = round(1.0 / n, 4)
        valid_allocs = [tuple([equal_w] * n)]

    results: dict[str, Any] = {"n_combinations": len(valid_allocs)}

    for obj in objectives:
        if obj == "equal":
            w = round(1.0 / n, 4)
            alloc = {name: w for name in names}
            combined_ret = returns_df.mean(axis=1)
            results["equal"] = {
                "allocation": alloc,
                **_compute_alloc_metrics(combined_ret),
            }
            continue

        best_val = -np.inf if obj != "min_dd" else np.inf
        best_alloc = None

        for combo in valid_allocs:
            weights = np.array(combo)
            port_ret = (returns_df.values * weights).sum(axis=1)
            port_ret_s = pd.Series(port_ret, index=returns_df.index)
            metrics = _compute_alloc_metrics(port_ret_s)

            if obj == "sharpe":
                val = metrics.get("sharpe_ratio", -999)
                if val > best_val:
                    best_val = val
                    best_alloc = combo
            elif obj == "calmar":
                val = metrics.get("calmar_ratio", -999)
                if val > best_val:
                    best_val = val
                    best_alloc = combo
            elif obj == "min_dd":
                val = metrics.get("max_drawdown", -999)
                if val > best_val:  # Less negative = better
                    best_val = val
                    best_alloc = combo

        if best_alloc is not None:
            alloc_dict = {name: round(w, 4) for name, w in zip(names, best_alloc)}
            port_ret = (returns_df.values * np.array(best_alloc)).sum(axis=1)
            results[obj] = {
                "allocation": alloc_dict,
                **_compute_alloc_metrics(pd.Series(port_ret, index=returns_df.index)),
            }

    return results


def _compute_alloc_metrics(returns: pd.Series, ann_factor: float = 252.0) -> dict[str, float]:
    """Compute portfolio metrics from return series."""
    if len(returns) < 2:
        return {}
    equity = (1 + returns).cumprod()
    total_ret = float(equity.iloc[-1] - 1)
    ann_ret = (1 + total_ret) ** (ann_factor / len(returns)) - 1 if len(returns) > 0 else 0
    vol = float(returns.std() * np.sqrt(ann_factor))
    sharpe = float(returns.mean() / returns.std() * np.sqrt(ann_factor)) if returns.std() > 0 else 0
    cummax = equity.cummax()
    dd = ((equity - cummax) / cummax.replace(0, np.nan)).min()
    max_dd = float(dd) if not np.isnan(dd) else 0
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0
    downside = returns[returns < 0]
    sortino = float(returns.mean() / downside.std() * np.sqrt(ann_factor)) if len(downside) > 0 and downside.std() > 0 else 0
    return {
        "total_return": round(total_ret, 4),
        "annualized_return": round(ann_ret, 4),
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "max_drawdown": round(max_dd, 4),
        "calmar_ratio": round(calmar, 4),
        "volatility": round(vol, 4),
    }


# =============================================================================
# 5B: Walk-Forward Analysis
# =============================================================================


@dataclass
class WalkForwardWindow:
    """Result for a single walk-forward window."""

    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_sharpe: float
    test_sharpe: float
    train_return: float
    test_return: float
    test_max_dd: float
    best_params: dict[str, Any] = field(default_factory=dict)


def walk_forward_analysis(
    equity: pd.Series,
    train_bars: int = 720,
    test_bars: int = 180,
    step_bars: int = 180,
    annualization_factor: float = 252.0,
) -> dict[str, Any]:
    """Perform walk-forward analysis on an equity curve.

    Splits the equity curve into overlapping train/test windows and
    computes in-sample (IS) vs out-of-sample (OOS) Sharpe ratios.

    Args:
        equity: Full equity curve.
        train_bars: Training window size in bars (default: 720 = ~12 months at 4h).
        test_bars: Testing window size in bars (default: 180 = ~3 months at 4h).
        step_bars: Step size between windows (default: 180 = ~3 months).
        annualization_factor: Bars per year for Sharpe computation.

    Returns:
        Dict with 'windows', 'summary', and pass/fail assessment.
    """
    n = len(equity)
    if n < train_bars + test_bars:
        return {
            "windows": [],
            "summary": {"error": "Insufficient data for walk-forward analysis"},
            "passed": False,
        }

    windows: list[WalkForwardWindow] = []
    window_id = 0
    start = 0

    while start + train_bars + test_bars <= n:
        train_slice = equity.iloc[start : start + train_bars]
        test_slice = equity.iloc[start + train_bars : start + train_bars + test_bars]

        train_ret = train_slice.pct_change().fillna(0.0)
        test_ret = test_slice.pct_change().fillna(0.0)

        ann = np.sqrt(annualization_factor)
        train_sharpe = float(train_ret.mean() / train_ret.std() * ann) if train_ret.std() > 0 else 0
        test_sharpe = float(test_ret.mean() / test_ret.std() * ann) if test_ret.std() > 0 else 0

        train_total = float(train_slice.iloc[-1] / train_slice.iloc[0] - 1) if train_slice.iloc[0] != 0 else 0
        test_total = float(test_slice.iloc[-1] / test_slice.iloc[0] - 1) if test_slice.iloc[0] != 0 else 0

        test_eq = (1 + test_ret).cumprod()
        test_dd = float(((test_eq - test_eq.cummax()) / test_eq.cummax().replace(0, np.nan)).min())
        if np.isnan(test_dd):
            test_dd = 0.0

        wf = WalkForwardWindow(
            window_id=window_id,
            train_start=str(train_slice.index[0]),
            train_end=str(train_slice.index[-1]),
            test_start=str(test_slice.index[0]),
            test_end=str(test_slice.index[-1]),
            train_sharpe=round(train_sharpe, 3),
            test_sharpe=round(test_sharpe, 3),
            train_return=round(train_total * 100, 2),
            test_return=round(test_total * 100, 2),
            test_max_dd=round(test_dd * 100, 2),
        )
        windows.append(wf)
        window_id += 1
        start += step_bars

    if not windows:
        return {"windows": [], "summary": {}, "passed": False}

    # Summary statistics
    oos_sharpes = [w.test_sharpe for w in windows]
    oos_returns = [w.test_return for w in windows]
    is_sharpes = [w.train_sharpe for w in windows]

    avg_oos_sharpe = float(np.mean(oos_sharpes))
    avg_is_sharpe = float(np.mean(is_sharpes))
    efficiency = avg_oos_sharpe / avg_is_sharpe if avg_is_sharpe != 0 else 0
    positive_oos = sum(1 for s in oos_sharpes if s > 0) / len(oos_sharpes)
    passed = avg_oos_sharpe > 0.3  # OOS Sharpe > 0.3 = pass

    summary = {
        "n_windows": len(windows),
        "avg_is_sharpe": round(avg_is_sharpe, 3),
        "avg_oos_sharpe": round(avg_oos_sharpe, 3),
        "min_oos_sharpe": round(float(np.min(oos_sharpes)), 3),
        "max_oos_sharpe": round(float(np.max(oos_sharpes)), 3),
        "oos_sharpe_std": round(float(np.std(oos_sharpes)), 3),
        "efficiency_ratio": round(efficiency, 3),
        "pct_positive_oos": round(positive_oos * 100, 1),
        "avg_oos_return_pct": round(float(np.mean(oos_returns)), 2),
        "total_oos_return_pct": round(float(np.sum(oos_returns)), 2),
    }

    return {
        "windows": [
            {
                "id": w.window_id,
                "train": f"{w.train_start} → {w.train_end}",
                "test": f"{w.test_start} → {w.test_end}",
                "IS_sharpe": w.train_sharpe,
                "OOS_sharpe": w.test_sharpe,
                "OOS_return%": w.test_return,
                "OOS_maxDD%": w.test_max_dd,
            }
            for w in windows
        ],
        "summary": summary,
        "passed": passed,
    }


# =============================================================================
# 5C: Monte Carlo Simulation
# =============================================================================


def monte_carlo_simulation(
    equity: pd.Series,
    n_simulations: int = 1000,
    seed: int = 42,
    annualization_factor: float = 365.0,
    method: str = "bootstrap",
) -> dict[str, Any]:
    """Monte Carlo simulation of the return series.

    Two supported methods:
      * ``bootstrap``  (default): sample daily returns with replacement.
        Produces a real distribution of final values, Sharpe, and drawdown.
      * ``shuffle`` : permute returns without replacement. This only
        reorders the same return set, so ∏(1+r_i) and mean/std of returns
        are mathematically invariant → final value and Sharpe are identical
        across sims (only MaxDD varies). Retained for path-order stress
        testing only.

    Args:
        equity: Equity curve (cumulative).
        n_simulations: Number of simulations.
        seed: Random seed for reproducibility.
        annualization_factor: Bars per year for Sharpe (default 365 for crypto daily).
        method: "bootstrap" (with replacement) or "shuffle" (permutation).

    Returns:
        Dict with percentile statistics and distribution info. Includes
        ``method`` key so downstream readers can tell which was used.
    """
    rng = np.random.default_rng(seed)
    returns = equity.pct_change().dropna().values

    if len(returns) < 10:
        return {"error": "Insufficient data for Monte Carlo simulation"}

    if method not in {"bootstrap", "shuffle"}:
        raise ValueError(f"method must be 'bootstrap' or 'shuffle', got {method!r}")

    final_values = []
    max_drawdowns = []
    sharpe_ratios = []

    n_returns = len(returns)
    for _ in range(n_simulations):
        if method == "bootstrap":
            sim_returns = rng.choice(returns, size=n_returns, replace=True)
        else:  # shuffle
            sim_returns = rng.permutation(returns)
        sim_equity = np.cumprod(1 + sim_returns)

        final_values.append(sim_equity[-1])

        cummax = np.maximum.accumulate(sim_equity)
        dd = (sim_equity - cummax) / cummax
        max_drawdowns.append(float(dd.min()))

        mean_r = sim_returns.mean()
        std_r = sim_returns.std()
        sharpe = mean_r / std_r * np.sqrt(annualization_factor) if std_r > 0 else 0
        sharpe_ratios.append(sharpe)

    final_arr = np.array(final_values)
    dd_arr = np.array(max_drawdowns)
    sharpe_arr = np.array(sharpe_ratios)

    # Original strategy metrics
    orig_equity = np.cumprod(1 + returns)
    orig_cummax = np.maximum.accumulate(orig_equity)
    orig_dd = ((orig_equity - orig_cummax) / orig_cummax).min()
    orig_sharpe = returns.mean() / returns.std() * np.sqrt(annualization_factor) if returns.std() > 0 else 0

    # Percentile rank of original vs simulation
    orig_final_pct = float(np.mean(final_arr <= orig_equity[-1]) * 100)
    orig_dd_pct = float(np.mean(dd_arr <= orig_dd) * 100)

    return {
        "n_simulations": n_simulations,
        "method": method,
        "original": {
            "final_value": round(float(orig_equity[-1]), 4),
            "max_drawdown": round(float(orig_dd) * 100, 2),
            "sharpe": round(float(orig_sharpe), 3),
        },
        "simulation": {
            "final_value": {
                "mean": round(float(final_arr.mean()), 4),
                "median": round(float(np.median(final_arr)), 4),
                "p5": round(float(np.percentile(final_arr, 5)), 4),
                "p25": round(float(np.percentile(final_arr, 25)), 4),
                "p75": round(float(np.percentile(final_arr, 75)), 4),
                "p95": round(float(np.percentile(final_arr, 95)), 4),
            },
            "max_drawdown": {
                "mean": round(float(dd_arr.mean()) * 100, 2),
                "median": round(float(np.median(dd_arr)) * 100, 2),
                "p5": round(float(np.percentile(dd_arr, 5)) * 100, 2),
                "p95": round(float(np.percentile(dd_arr, 95)) * 100, 2),
            },
            "sharpe": {
                "mean": round(float(sharpe_arr.mean()), 3),
                "median": round(float(np.median(sharpe_arr)), 3),
                "p5": round(float(np.percentile(sharpe_arr, 5)), 3),
                "p95": round(float(np.percentile(sharpe_arr, 95)), 3),
            },
        },
        "percentile_rank": {
            "final_value": round(orig_final_pct, 1),
            "max_drawdown": round(orig_dd_pct, 1),
        },
    }


# =============================================================================
# 5D: Parameter Stability Analysis
# =============================================================================


def parameter_stability_analysis(
    scan_results: pd.DataFrame,
    param_columns: list[str],
    metric_column: str = "sharpe_ratio",
    best_idx: int = 0,
) -> dict[str, Any]:
    """Analyze how sensitive performance is to parameter changes.

    Evaluates whether the best parameter set is in a smooth "plateau"
    (robust) or a sharp "spike" (overfitted).

    Args:
        scan_results: DataFrame from ParamScanner with columns for params and metrics.
        param_columns: List of parameter column names.
        metric_column: Performance metric to analyze.
        best_idx: Index of the best parameter set.

    Returns:
        Dict with stability scores per parameter and overall assessment.
    """
    if scan_results.empty or metric_column not in scan_results.columns:
        return {"error": "No scan results or metric column not found"}

    best_row = scan_results.iloc[best_idx]
    best_metric = float(best_row[metric_column])

    stability: dict[str, Any] = {}
    overall_scores = []

    for param in param_columns:
        if param not in scan_results.columns:
            continue

        # Find rows where only this param differs from best
        other_params = [p for p in param_columns if p != param]
        mask = pd.Series(True, index=scan_results.index)
        for op in other_params:
            mask &= scan_results[op] == best_row[op]

        neighbours = scan_results[mask].sort_values(param)
        if len(neighbours) < 2:
            stability[param] = {"n_values": len(neighbours), "stable": True}
            overall_scores.append(1.0)
            continue

        metrics = neighbours[metric_column].values
        param_vals = neighbours[param].values

        # Stability score: ratio of metric at neighbours vs best
        if best_metric > 0:
            relative = metrics / best_metric
            mean_relative = float(np.mean(relative))
            std_relative = float(np.std(relative))
        else:
            mean_relative = 1.0
            std_relative = 0.0

        # A high mean_relative (>0.8) and low std_relative (<0.3) = stable
        score = mean_relative * (1 - min(std_relative, 1.0))
        overall_scores.append(score)

        stability[param] = {
            "best_value": float(best_row[param]),
            "best_metric": round(best_metric, 4),
            "n_values": len(neighbours),
            "mean_relative_perf": round(mean_relative, 3),
            "std_relative_perf": round(std_relative, 3),
            "stability_score": round(score, 3),
            "stable": score > 0.6,
            "param_range": [float(param_vals.min()), float(param_vals.max())],
        }

    overall = float(np.mean(overall_scores)) if overall_scores else 0.0
    return {
        "per_parameter": stability,
        "overall_stability": round(overall, 3),
        "assessment": "ROBUST" if overall > 0.6 else "CAUTION" if overall > 0.4 else "OVERFIT_RISK",
    }


# =============================================================================
# 5E: Stress Testing
# =============================================================================


# Known crypto extreme events (approximate date ranges)
STRESS_EVENTS: list[dict[str, str]] = [
    {"name": "COVID_Crash_2020", "start": "2020-03-09", "end": "2020-03-23"},
    {"name": "May_2021_Crash", "start": "2021-05-12", "end": "2021-05-26"},
    {"name": "Terra_Luna_2022", "start": "2022-05-05", "end": "2022-05-19"},
    {"name": "FTX_Collapse_2022", "start": "2022-11-02", "end": "2022-11-16"},
    {"name": "SVB_Crisis_2023", "start": "2023-03-08", "end": "2023-03-22"},
    {"name": "Aug_2024_Unwind", "start": "2024-08-01", "end": "2024-08-15"},
]


def stress_test(
    equity: pd.Series,
    events: list[dict[str, str]] | None = None,
    buffer_days: int = 7,
) -> dict[str, Any]:
    """Evaluate strategy performance during known stress events.

    Extracts ±buffer_days around each event and computes metrics.

    Args:
        equity: Full equity curve with DatetimeIndex.
        events: List of dicts with 'name', 'start', 'end'. Uses defaults if None.
        buffer_days: Extra days before/after event to include.

    Returns:
        Dict with per-event metrics and overall stress assessment.
    """
    if events is None:
        events = STRESS_EVENTS

    if not hasattr(equity.index, 'date'):
        return {"error": "Equity curve must have DatetimeIndex for stress testing"}

    results: list[dict[str, Any]] = []
    tested_count = 0

    for event in events:
        try:
            start = pd.Timestamp(event["start"]) - pd.Timedelta(days=buffer_days)
            end = pd.Timestamp(event["end"]) + pd.Timedelta(days=buffer_days)
        except Exception:
            continue

        mask = (equity.index >= start) & (equity.index <= end)
        event_eq = equity[mask]

        if len(event_eq) < 5:
            results.append({
                "event": event["name"],
                "period": f"{event['start']} → {event['end']}",
                "status": "no_data",
            })
            continue

        tested_count += 1
        ret = float(event_eq.iloc[-1] / event_eq.iloc[0] - 1)
        cummax = event_eq.cummax()
        dd = float(((event_eq - cummax) / cummax.replace(0, np.nan)).min())
        if np.isnan(dd):
            dd = 0.0

        event_rets = event_eq.pct_change().dropna()
        vol = float(event_rets.std() * np.sqrt(252)) if len(event_rets) > 1 else 0

        results.append({
            "event": event["name"],
            "period": f"{event['start']} → {event['end']}",
            "status": "tested",
            "bars": len(event_eq),
            "return_pct": round(ret * 100, 2),
            "max_dd_pct": round(dd * 100, 2),
            "volatility_ann": round(vol, 2),
            "survived": dd > -0.30,  # < 30% DD during crisis = survived
        })

    tested = [r for r in results if r.get("status") == "tested"]
    survived = sum(1 for r in tested if r.get("survived", False))
    avg_dd = float(np.mean([r["max_dd_pct"] for r in tested])) if tested else 0
    avg_ret = float(np.mean([r["return_pct"] for r in tested])) if tested else 0

    return {
        "events": results,
        "summary": {
            "events_tested": tested_count,
            "events_survived": survived,
            "survival_rate": round(survived / tested_count * 100, 1) if tested_count > 0 else 0,
            "avg_stress_return_pct": round(avg_ret, 2),
            "avg_stress_dd_pct": round(avg_dd, 2),
        },
        "passed": survived == tested_count if tested_count > 0 else False,
    }


# =============================================================================
# 5F: Fee Sensitivity / Breakeven Analysis
# =============================================================================


def fee_sensitivity_analysis(
    equity: pd.Series,
    n_trades: int,
    base_fee_bps: float = 6.0,
    fee_range_bps: tuple[float, float] = (0.0, 30.0),
    fee_step_bps: float = 2.0,
) -> dict[str, Any]:
    """Analyze how sensitive strategy profit is to transaction fees.

    Estimates PnL at various fee levels to find the breakeven fee.

    Args:
        equity: Equity curve from a backtest.
        n_trades: Total number of round-trip trades.
        base_fee_bps: Base fee in basis points used in the original backtest.
        fee_range_bps: (min, max) fee levels to test.
        fee_step_bps: Step between fee levels.

    Returns:
        Dict with fee sensitivity curve and breakeven fee.
    """
    if len(equity) < 2:
        return {"error": "Insufficient equity data"}

    original_return = float(equity.iloc[-1] / equity.iloc[0] - 1)
    original_pnl = float(equity.iloc[-1] - equity.iloc[0])
    capital = float(equity.iloc[0])

    # Each round-trip trade costs ~2× fee (entry + exit)
    # Approximate average trade size as capital
    avg_trade_value = capital

    results: list[dict[str, float]] = []
    breakeven_fee = None

    fee = fee_range_bps[0]
    while fee <= fee_range_bps[1] + 1e-9:
        fee_diff_bps = fee - base_fee_bps
        # Additional cost per round-trip = 2 × (fee_diff / 10000) × avg_trade_value
        additional_cost = 2 * (fee_diff_bps / 10000) * avg_trade_value * n_trades
        adjusted_pnl = original_pnl - additional_cost
        adjusted_return = adjusted_pnl / capital

        results.append({
            "fee_bps": round(fee, 1),
            "pnl": round(adjusted_pnl, 2),
            "return_pct": round(adjusted_return * 100, 2),
            "profitable": adjusted_pnl > 0,
        })

        # Find breakeven crossing
        if breakeven_fee is None and adjusted_pnl <= 0 and fee > base_fee_bps:
            # Interpolate breakeven
            if len(results) >= 2:
                prev = results[-2]
                curr = results[-1]
                if prev["pnl"] > 0:
                    frac = prev["pnl"] / (prev["pnl"] - curr["pnl"])
                    breakeven_fee = prev["fee_bps"] + frac * fee_step_bps

        fee += fee_step_bps

    # Safety margin: ratio of breakeven fee to actual fee
    safety_margin = breakeven_fee / base_fee_bps if breakeven_fee and base_fee_bps > 0 else None

    # If breakeven wasn't found but portfolio is profitable at max fee, it's very safe
    if breakeven_fee is None and results and results[-1]["profitable"]:
        breakeven_fee = fee_range_bps[1]  # at least this high
        safety_margin = breakeven_fee / base_fee_bps if base_fee_bps > 0 else None

    return {
        "base_fee_bps": base_fee_bps,
        "n_trades": n_trades,
        "original_return_pct": round(original_return * 100, 2),
        "breakeven_fee_bps": round(breakeven_fee, 1) if breakeven_fee else None,
        "safety_margin": round(safety_margin, 2) if safety_margin else None,
        "fee_curve": results,
        "assessment": (
            "SAFE" if safety_margin and safety_margin > 3
            else "OK" if safety_margin and safety_margin > 1.5
            else "TIGHT" if safety_margin and safety_margin > 1
            else "UNPROFITABLE"
        ),
    }


# =============================================================================
# Comprehensive Robustness Report
# =============================================================================


def generate_robustness_report(
    equity: pd.Series,
    n_trades: int = 100,
    train_bars: int = 720,
    test_bars: int = 180,
    mc_sims: int = 1000,
    base_fee_bps: float = 6.0,
) -> dict[str, Any]:
    """Run all robustness checks and produce a unified report.

    Args:
        equity: Full equity curve with DatetimeIndex.
        n_trades: Number of round-trip trades.
        train_bars: Walk-forward training window.
        test_bars: Walk-forward test window.
        mc_sims: Number of Monte Carlo simulations.
        base_fee_bps: Base fee for fee sensitivity.

    Returns:
        Dict with results from all 5 robustness tests + overall pass/fail.
    """
    report: dict[str, Any] = {}

    # 5B: Walk-Forward
    wf = walk_forward_analysis(equity, train_bars=train_bars, test_bars=test_bars)
    report["walk_forward"] = wf

    # 5C: Monte Carlo
    mc = monte_carlo_simulation(equity, n_simulations=mc_sims)
    report["monte_carlo"] = mc

    # 5E: Stress Test
    st = stress_test(equity)
    report["stress_test"] = st

    # 5F: Fee Sensitivity
    fs = fee_sensitivity_analysis(equity, n_trades=n_trades, base_fee_bps=base_fee_bps)
    report["fee_sensitivity"] = fs

    # Overall assessment
    checks = {
        "walk_forward": wf.get("passed", False),
        "monte_carlo": mc.get("original", {}).get("sharpe", 0) > mc.get("simulation", {}).get("sharpe", {}).get("median", 999),
        "stress_test": st.get("passed", False),
        "fee_sensitivity": fs.get("assessment") in ("SAFE", "OK"),
    }
    report["overall"] = {
        "checks": checks,
        "passed": sum(checks.values()),
        "total": len(checks),
        "verdict": "DEPLOYABLE" if all(checks.values()) else "NEEDS_WORK",
    }

    return report


# =============================================================================
# Block Bootstrap (preserves short-range autocorrelation)
# =============================================================================


def block_bootstrap_simulation(
    equity: pd.Series,
    n_simulations: int = 1000,
    block_size: int = 5,
    seed: int = 42,
    annualization_factor: float = 365.0,
) -> dict[str, Any]:
    """Block bootstrap: sample contiguous return blocks with replacement.

    Preserves short-range autocorrelation (e.g., volatility clustering) that
    i.i.d. bootstrap destroys. Each sim is assembled from randomly chosen
    blocks of ``block_size`` consecutive daily returns.

    Args:
        equity: Daily equity curve.
        n_simulations: Number of simulated paths.
        block_size: Length of each contiguous block (days).
        seed: RNG seed.
        annualization_factor: Bars per year for Sharpe (365 for crypto daily).

    Returns:
        Dict with same shape as ``monte_carlo_simulation`` plus ``block_size``.
    """
    rng = np.random.default_rng(seed)
    returns = equity.pct_change().dropna().values
    n = len(returns)
    if n < block_size * 2:
        return {"error": "Insufficient data for block bootstrap"}

    n_blocks = int(np.ceil(n / block_size))
    max_start = n - block_size

    final_values, max_drawdowns, sharpe_ratios = [], [], []
    for _ in range(n_simulations):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        blocks = [returns[s : s + block_size] for s in starts]
        sim_returns = np.concatenate(blocks)[:n]
        sim_equity = np.cumprod(1 + sim_returns)

        final_values.append(float(sim_equity[-1]))
        cummax = np.maximum.accumulate(sim_equity)
        dd = (sim_equity - cummax) / cummax
        max_drawdowns.append(float(dd.min()))

        mean_r, std_r = sim_returns.mean(), sim_returns.std()
        sharpe = mean_r / std_r * np.sqrt(annualization_factor) if std_r > 0 else 0
        sharpe_ratios.append(float(sharpe))

    fa, da, sa = map(np.array, (final_values, max_drawdowns, sharpe_ratios))
    orig_eq = np.cumprod(1 + returns)
    orig_cummax = np.maximum.accumulate(orig_eq)
    orig_dd = float(((orig_eq - orig_cummax) / orig_cummax).min())
    orig_sh = returns.mean() / returns.std() * np.sqrt(annualization_factor) if returns.std() > 0 else 0

    return {
        "n_simulations": n_simulations,
        "method": "block_bootstrap",
        "block_size": block_size,
        "original": {
            "final_value": round(float(orig_eq[-1]), 4),
            "max_drawdown": round(orig_dd * 100, 2),
            "sharpe": round(float(orig_sh), 3),
        },
        "simulation": {
            "final_value": {
                "mean": round(float(fa.mean()), 4),
                "median": round(float(np.median(fa)), 4),
                "p5": round(float(np.percentile(fa, 5)), 4),
                "p25": round(float(np.percentile(fa, 25)), 4),
                "p75": round(float(np.percentile(fa, 75)), 4),
                "p95": round(float(np.percentile(fa, 95)), 4),
            },
            "max_drawdown": {
                "mean": round(float(da.mean()) * 100, 2),
                "median": round(float(np.median(da)) * 100, 2),
                "p5": round(float(np.percentile(da, 5)) * 100, 2),
                "p95": round(float(np.percentile(da, 95)) * 100, 2),
            },
            "sharpe": {
                "mean": round(float(sa.mean()), 3),
                "median": round(float(np.median(sa)), 3),
                "p5": round(float(np.percentile(sa, 5)), 3),
                "p95": round(float(np.percentile(sa, 95)), 3),
            },
        },
    }


# =============================================================================
# Nested Walk-Forward (per-window allocation re-selection)
# =============================================================================


def walk_forward_nested_analysis(
    equity_curves: dict[str, pd.Series],
    train_bars: int = 365,
    test_bars: int = 90,
    step_bars: int = 90,
    annualization_factor: float = 365.0,
    weighting: str = "sharpe_pos",
) -> dict[str, Any]:
    """Nested walk-forward that re-selects allocation per IS window.

    Unlike ``walk_forward_analysis`` (which slices a single post-selection
    combined curve), this takes **per-strategy** equity curves, aligns them
    on a shared daily index, and for each window:

      1. Uses IS slice to compute per-strategy IS Sharpe.
      2. Re-derives allocation weights (``weighting``):
         - ``"equal"``: equal weights across all strategies
         - ``"sharpe_pos"``: positive IS Sharpe only, normalized
      3. Applies those weights to OOS daily returns → new combined OOS curve.
      4. Computes OOS Sharpe / return / MaxDD on that curve.

    Args:
        equity_curves: Dict mapping strategy name → daily-frequency equity.
        train_bars / test_bars / step_bars: window sizing in days.
        annualization_factor: 365 for crypto daily.
        weighting: "equal" or "sharpe_pos".

    Returns:
        Dict with per-window OOS stats and summary. Includes ``weighting``
        key so callers can tell which scheme produced the numbers.
    """
    if weighting not in {"equal", "sharpe_pos"}:
        raise ValueError(f"weighting must be 'equal' or 'sharpe_pos', got {weighting!r}")

    if not equity_curves:
        return {"windows": [], "summary": {"error": "No curves"}, "passed": False}

    aligned = pd.DataFrame(equity_curves).ffill().bfill().dropna()
    if len(aligned) < train_bars + test_bars:
        return {"windows": [], "summary": {"error": "Insufficient data"}, "passed": False}

    returns = aligned.pct_change().fillna(0.0)
    n = len(aligned)

    windows = []
    start = 0
    while start + train_bars + test_bars <= n:
        is_ret = returns.iloc[start : start + train_bars]
        oos_ret = returns.iloc[start + train_bars : start + train_bars + test_bars]

        ann = np.sqrt(annualization_factor)
        is_sharpe = (is_ret.mean() / is_ret.std().replace(0, np.nan)) * ann
        is_sharpe = is_sharpe.fillna(0.0)

        if weighting == "equal":
            weights = pd.Series(1.0 / len(is_sharpe), index=is_sharpe.index)
        else:  # sharpe_pos
            pos = is_sharpe.clip(lower=0.0)
            total = pos.sum()
            weights = (pos / total) if total > 0 else pd.Series(1.0 / len(pos), index=pos.index)

        oos_combined_ret = (oos_ret * weights).sum(axis=1)
        oos_std = oos_combined_ret.std()
        oos_sharpe = float(oos_combined_ret.mean() / oos_std * ann) if oos_std > 0 else 0.0

        oos_eq = (1 + oos_combined_ret).cumprod()
        oos_total = float(oos_eq.iloc[-1] - 1) if len(oos_eq) else 0.0
        oos_dd_series = (oos_eq - oos_eq.cummax()) / oos_eq.cummax().replace(0, np.nan)
        oos_dd = float(oos_dd_series.min()) if not oos_dd_series.empty else 0.0
        if np.isnan(oos_dd):
            oos_dd = 0.0

        is_sharpe_avg = float(is_sharpe[weights > 0].mean()) if (weights > 0).any() else 0.0

        windows.append({
            "id": len(windows),
            "train": f"{aligned.index[start].date()} → {aligned.index[start + train_bars - 1].date()}",
            "test": f"{aligned.index[start + train_bars].date()} → {aligned.index[start + train_bars + test_bars - 1].date()}",
            "is_sharpe_avg_weighted": round(is_sharpe_avg, 3),
            "oos_sharpe": round(oos_sharpe, 3),
            "oos_return%": round(oos_total * 100, 2),
            "oos_maxdd%": round(oos_dd * 100, 2),
            "n_strategies_weighted": int((weights > 0).sum()),
        })
        start += step_bars

    if not windows:
        return {"windows": [], "summary": {"error": "No windows"}, "passed": False}

    oos_sh = [w["oos_sharpe"] for w in windows]
    summary = {
        "weighting": weighting,
        "n_windows": len(windows),
        "avg_oos_sharpe": round(float(np.mean(oos_sh)), 3),
        "min_oos_sharpe": round(float(np.min(oos_sh)), 3),
        "max_oos_sharpe": round(float(np.max(oos_sh)), 3),
        "oos_sharpe_std": round(float(np.std(oos_sh)), 3),
        "pct_positive_oos": round(sum(1 for s in oos_sh if s > 0) / len(oos_sh) * 100, 1),
        "avg_oos_return_pct": round(float(np.mean([w["oos_return%"] for w in windows])), 2),
    }
    return {"windows": windows, "summary": summary, "weighting": weighting, "passed": summary["avg_oos_sharpe"] > 0.3}
