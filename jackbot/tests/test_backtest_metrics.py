"""Unit tests for jackbot.backtest_metrics (t3-objective-upgrade)."""

from __future__ import annotations

import math

from jackbot.backtest_metrics import (
    compute_calmar,
    compute_score,
    compute_sharpe,
    score_from_results,
)


class TestSharpe:
    def test_empty_returns_zero(self):
        assert compute_sharpe([]) == 0.0

    def test_single_sample_returns_zero(self):
        assert compute_sharpe([0.5]) == 0.0

    def test_constant_returns_zero(self):
        # Zero variance → Sharpe undefined; return 0 sentinel
        assert compute_sharpe([0.5, 0.5, 0.5]) == 0.0

    def test_positive_mean_yields_positive_sharpe(self):
        # mean > 0 with some variance → positive Sharpe (and annualised)
        sh = compute_sharpe([0.5, 1.0, 0.3, 0.8, 0.2])
        assert sh > 0
        # Annualisation factor sqrt(252) ≈ 15.87 — should clearly exceed raw mean
        raw_ratio = sum([0.5, 1.0, 0.3, 0.8, 0.2]) / 5
        assert sh > raw_ratio


class TestCalmar:
    def test_zero_dd_returns_zero(self):
        assert compute_calmar(roi_pct=10.0, days=30, max_dd_pct=0.0) == 0.0

    def test_zero_days_returns_zero(self):
        assert compute_calmar(roi_pct=10.0, days=0, max_dd_pct=5.0) == 0.0

    def test_basic_calmar(self):
        # 30d ROI 10%, DD 5% → annualised ~ 121.67% / 5 = 24.33
        c = compute_calmar(roi_pct=10.0, days=30, max_dd_pct=5.0)
        assert abs(c - (10.0 * 365 / 30 / 5.0)) < 1e-9


class TestScore:
    def test_max_dd_reject(self):
        # DD > threshold → -inf
        s = compute_score(sharpe=5, calmar=5, roi_pct=20, max_dd_pct=20.0)
        assert s == float("-inf")

    def test_basic_weighted_sum(self):
        # 0.5×2 + 0.3×4 + 0.2×10 = 1 + 1.2 + 2 = 4.2
        s = compute_score(sharpe=2, calmar=4, roi_pct=10, max_dd_pct=10.0)
        assert abs(s - 4.2) < 1e-9

    def test_negative_components_can_lower_score(self):
        # Negative ROI tugs score down
        s = compute_score(sharpe=2, calmar=4, roi_pct=-10, max_dd_pct=5.0)
        assert s < compute_score(sharpe=2, calmar=4, roi_pct=10, max_dd_pct=5.0)


class TestScoreFromResults:
    def test_typical_results_dict(self):
        results = {
            "capital": 150.0,
            "period": {"days": 30},
            "pnl": {"roi_pct": 10.0},
            "risk": {"max_drawdown": 7.5},  # 5% of 150
            "daily_detail": {
                f"day{i:02d}": {"profit": p}
                for i, p in enumerate([1.0, -0.5, 2.0, 0.5, -0.3, 1.5, 0.0])
            },
        }
        s = score_from_results(results)
        assert math.isfinite(s)

    def test_excessive_dd_yields_neg_inf(self):
        results = {
            "capital": 150.0,
            "period": {"days": 30},
            "pnl": {"roi_pct": 50.0},
            "risk": {"max_drawdown": 50.0},  # 33% of 150 → reject
            "daily_detail": {},
        }
        assert score_from_results(results) == float("-inf")
