"""Tests for monte_carlo.py + DayTraderConfig leverage cap.

These tests verify two things:
  1. DayTraderConfig.__post_init__ enforces the Phase A max_leverage <= 10 cap.
  2. The walk-forward window-level bootstrap is deterministic given a fixed seed,
     and produces sensible statistics on a synthetic 21-window sample.

The trade-level MC reuses MonteCarloSimulator from src/bot/backtest/monte_carlo.py
which has its own tests; here we only smoke-test our wrapper around it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

# Make scripts/ importable (mirrors the import path used in scripts/monte_carlo.py)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "src"))

from jackbot.strategy.day_trader import DayTraderConfig


# ── DayTraderConfig leverage cap ─────────────────────────────────────


def test_max_leverage_default_is_ten():
    cfg = DayTraderConfig()
    assert cfg.max_leverage == 10


def test_max_leverage_above_cap_rejected():
    with pytest.raises(AssertionError, match="max_leverage 上限為 10"):
        DayTraderConfig(max_leverage=20)


def test_max_leverage_at_cap_accepted():
    cfg = DayTraderConfig(max_leverage=10)
    assert cfg.max_leverage == 10


def test_max_leverage_below_cap_accepted():
    cfg = DayTraderConfig(max_leverage=5)
    assert cfg.max_leverage == 5


# ── Walk-forward bootstrap determinism ──────────────────────────────


def test_walk_forward_bootstrap_is_deterministic(tmp_path, monkeypatch):
    """Same seed + same input ⇒ identical bootstrap stats."""
    # Build a synthetic 21-window walk_forward_results.json
    data = {
        "config": {"months": 6, "train_days": 30, "test_days": 7},
        "summary": {},
        "windows": [
            {
                "window": i + 1,
                "test_start": "2025-11-26",
                "test_end": "2025-12-03",
                "best_gc": 10,
                "best_sl": 4.0,
                "roi_pct": 10.0 + i,
                "net_profit": 15.0 + i * 1.5,
                "max_dd_pct": 0.0,
                "profit_factor": 5.0,
            }
            for i in range(21)
        ],
    }
    fake_data_dir = tmp_path / "data"
    fake_data_dir.mkdir()
    wf_path = fake_data_dir / "walk_forward_results.json"
    wf_path.write_text(json.dumps(data), encoding="utf-8")

    # Import the function under test, rerouting ROOT to tmp_path
    import scripts.monte_carlo as mc_mod
    monkeypatch.setattr(mc_mod, "ROOT", tmp_path)

    r1 = mc_mod.run_walk_forward_bootstrap(sims=500, seed=123)
    r2 = mc_mod.run_walk_forward_bootstrap(sims=500, seed=123)

    assert r1 == r2, "Same seed must produce identical bootstrap output"
    assert r1["actual"]["positive_window_rate"] == 1.0
    # All synthetic windows are positive ⇒ loss_probability ≈ 0
    assert r1["loss_probability"] == 0.0
    # Bootstrap median total profit should be near actual sum
    actual_sum = sum(w["net_profit"] for w in data["windows"])
    median = r1["bootstrap_total_profit"]["median"]
    assert abs(median - actual_sum) / actual_sum < 0.05


def test_walk_forward_bootstrap_detects_loss(tmp_path, monkeypatch):
    """If half windows are losers, loss_probability should be material."""
    data = {
        "config": {},
        "summary": {},
        "windows": [
            {
                "window": i + 1,
                "test_start": "2025-11-26",
                "test_end": "2025-12-03",
                "best_gc": 10,
                "best_sl": 4.0,
                "roi_pct": (i % 2) * 20 - 10,
                "net_profit": (i % 2) * 30 - 15,  # alternating +15 / -15
                "max_dd_pct": 5.0,
                "profit_factor": 1.0,
            }
            for i in range(21)
        ],
    }
    fake_data_dir = tmp_path / "data"
    fake_data_dir.mkdir()
    (fake_data_dir / "walk_forward_results.json").write_text(json.dumps(data), encoding="utf-8")

    import scripts.monte_carlo as mc_mod
    monkeypatch.setattr(mc_mod, "ROOT", tmp_path)

    r = mc_mod.run_walk_forward_bootstrap(sims=2000, seed=7)
    # Mean of windows is (+15 + -15) / 2 = 0 (with one extra +15 since 21 is odd)
    # So loss_probability should be roughly ~50% (some bootstraps net negative)
    assert 0.2 < r["loss_probability"] < 0.6


def test_walk_forward_bootstrap_missing_file(tmp_path, monkeypatch):
    """Graceful error when walk_forward_results.json doesn't exist."""
    import scripts.monte_carlo as mc_mod
    monkeypatch.setattr(mc_mod, "ROOT", tmp_path)
    r = mc_mod.run_walk_forward_bootstrap(sims=100, seed=1)
    assert r.get("error") == "no_walk_forward_results"


# ── MonteCarloSimulator reproducibility (via wrapper) ──────────────


def test_monte_carlo_simulator_deterministic():
    """Verify the parent-project MC module is reachable & deterministic."""
    from bot.backtest.monte_carlo import MonteCarloSimulator

    pnls = [1.5, -0.8, 2.3, -1.1, 0.9, 1.7, -2.0, 0.5, 1.2, -0.3]
    mc1 = MonteCarloSimulator(num_simulations=500, seed=42)
    mc2 = MonteCarloSimulator(num_simulations=500, seed=42)
    r1 = mc1.run(trade_pnls=pnls, initial_capital=100.0)
    r2 = mc2.run(trade_pnls=pnls, initial_capital=100.0)

    assert r1["final_equity"]["mean"] == r2["final_equity"]["mean"]
    assert r1["ruin_probability"] == r2["ruin_probability"]
    # Sanity: total of pnls is positive ⇒ profit_probability > 0
    assert sum(pnls) > 0
    assert r1["final_equity"]["mean"] == pytest.approx(100.0 + sum(pnls), rel=1e-6)
