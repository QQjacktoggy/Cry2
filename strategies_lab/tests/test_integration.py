from __future__ import annotations

import pytest

from strategies_lab.integration import (
    build_custom_strategy_suite,
    build_phase1_lab_configs,
    build_phase1_lab_configs_for_allocations,
    build_v8_strategy_suite,
    get_profile_preset,
)
from strategies_lab.liquidation_hunter import LiquidationHunterStrategy
from strategies_lab.stat_arb_pairs import StatArbPairsStrategy


def test_v8b_profile_enables_expected_phase1_sleeves():
    configs = build_phase1_lab_configs("v8b")

    assert configs["lab_stat_arb_pairs"]["enabled"] is True
    assert configs["lab_stat_arb_pairs"]["allocation"] == pytest.approx(0.08)
    assert configs["lab_liquidation_hunter"]["enabled"] is True
    assert configs["lab_liquidation_hunter"]["allocation"] == pytest.approx(0.03)
    assert configs["lab_stat_arb_pairs_bnb_btc"]["enabled"] is False


def test_v8d_profile_only_enables_micro_liquidation_hunter():
    configs = build_phase1_lab_configs("v8d")

    assert configs["lab_liquidation_hunter"]["enabled"] is True
    assert configs["lab_liquidation_hunter"]["allocation"] == pytest.approx(0.01)
    assert configs["lab_stat_arb_pairs"]["enabled"] is False
    assert configs["lab_stat_arb_pairs_bnb_btc"]["enabled"] is False


def test_v8c_strategy_suite_contains_three_lab_strategies():
    strategies = build_v8_strategy_suite(initial_capital=150.0, profile="v8c")
    names = {strategy.name for strategy in strategies}

    assert len(strategies) == 19
    assert {
        "lab_stat_arb_pairs",
        "lab_stat_arb_pairs_bnb_btc",
        "lab_liquidation_hunter",
    }.issubset(names)


def test_explicit_allocations_only_enable_requested_lab_sleeves():
    configs = build_phase1_lab_configs_for_allocations({
        "lab_liquidation_hunter": 0.02,
    })

    assert configs["lab_liquidation_hunter"]["enabled"] is True
    assert configs["lab_liquidation_hunter"]["allocation"] == pytest.approx(0.02)
    assert configs["lab_stat_arb_pairs"]["enabled"] is False
    assert configs["lab_stat_arb_pairs_bnb_btc"]["enabled"] is False


def test_custom_strategy_suite_matches_v8a_preset():
    preset = get_profile_preset("v8a")

    custom = build_custom_strategy_suite(
        initial_capital=150.0,
        core_fraction=preset["core_fraction"],
        lab_allocations=preset["lab_allocations"],
    )
    profile = build_v8_strategy_suite(initial_capital=150.0, profile="v8a")

    assert [strategy.name for strategy in custom] == [strategy.name for strategy in profile]
    assert [getattr(strategy, "_allocation_usd", None) for strategy in custom] == [
        getattr(strategy, "_allocation_usd", None) for strategy in profile
    ]


def test_stat_arb_leg_sizes_respect_allocation_budget():
    strategy = StatArbPairsStrategy({"allocation": 0.08, "leverage": 1})
    strategy.set_equity(10_000)
    strategy._beta = 1.0
    strategy._beta_init = True

    qty_a, qty_b = strategy._calc_leg_sizes(3_000, 50_000)

    total_notional = qty_a * 3_000 + qty_b * 50_000
    assert total_notional == pytest.approx(800, rel=0.01)


def test_liquidation_size_respects_allocation_budget():
    strategy = LiquidationHunterStrategy({"allocation": 0.03, "leverage": 2})
    strategy.set_equity(10_000)

    qty = strategy._calc_size(100)

    assert qty == pytest.approx(6.0)
