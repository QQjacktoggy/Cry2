"""Opt-in integration helpers for V8 phase-1 lab sleeves.

These helpers keep `strategies_lab` isolated from the default runtime while
making it easy to construct profile-based V8 candidate portfolios on top of
the current V7.4 control baseline.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from bot.strategy.base import BaseStrategy
from bot.strategy.bridge import create_v74_strategies
from strategies_lab.liquidation_hunter import LiquidationHunterStrategy
from strategies_lab.stat_arb_pairs import StatArbPairsStrategy

PHASE1_STRATEGY_NAMES = (
    "lab_stat_arb_pairs",
    "lab_stat_arb_pairs_bnb_btc",
    "lab_liquidation_hunter",
)

V8_PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "baseline": {
        "label": "V7.4 control baseline",
        "core_fraction": 1.00,
        "lab_allocations": {},
    },
    "v8a": {
        "label": "V8-A low-risk pilot",
        "core_fraction": 0.93,
        "lab_allocations": {
            "lab_stat_arb_pairs": 0.05,
            "lab_liquidation_hunter": 0.02,
        },
    },
    "v8b": {
        "label": "V8-B balanced target",
        "core_fraction": 0.89,
        "lab_allocations": {
            "lab_stat_arb_pairs": 0.08,
            "lab_liquidation_hunter": 0.03,
        },
    },
    "v8c": {
        "label": "V8-C research upper bound",
        "core_fraction": 0.85,
        "lab_allocations": {
            "lab_stat_arb_pairs": 0.08,
            "lab_stat_arb_pairs_bnb_btc": 0.04,
            "lab_liquidation_hunter": 0.03,
        },
    },
    "v8d": {
        "label": "V8-D micro pilot (liq-only)",
        "core_fraction": 0.99,
        "lab_allocations": {
            "lab_liquidation_hunter": 0.01,
        },
    },
}

LAB_CLASS_MAP: dict[str, type[BaseStrategy]] = {
    "lab_stat_arb_pairs": StatArbPairsStrategy,
    "lab_stat_arb_pairs_bnb_btc": StatArbPairsStrategy,
    "lab_liquidation_hunter": LiquidationHunterStrategy,
}

DEFAULT_LAB_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "strategies_lab.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data if data is not None else {}


def get_profile_preset(profile: str) -> dict[str, Any]:
    preset = V8_PROFILE_PRESETS.get(profile.lower())
    if preset is None:
        raise ValueError(f"Unknown V8 profile: {profile}")
    return deepcopy(preset)


def _load_phase1_base_configs(
    lab_config_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    config_path = Path(lab_config_path) if lab_config_path else DEFAULT_LAB_CONFIG_PATH
    lab_config = _load_yaml(config_path)
    strategy_cfg = deepcopy(lab_config.get("strategies", {}))
    return {
        name: deepcopy(strategy_cfg.get(name, {}))
        for name in PHASE1_STRATEGY_NAMES
        if name in strategy_cfg
    }


def build_phase1_lab_configs_for_allocations(
    lab_allocations: dict[str, float] | None = None,
    lab_config_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Return phase-1 lab configs after applying explicit target allocations."""
    phase1_configs = _load_phase1_base_configs(lab_config_path=lab_config_path)
    allocations = lab_allocations or {}

    unknown = set(allocations) - set(phase1_configs)
    if unknown:
        raise ValueError(f"Unknown lab strategies: {sorted(unknown)}")

    for name, params in phase1_configs.items():
        alloc = float(allocations.get(name, 0.0))
        if alloc < 0:
            raise ValueError(f"Allocation must be non-negative for {name}: {alloc}")
        params["enabled"] = alloc > 0
        if alloc > 0:
            params["allocation"] = alloc
    return phase1_configs


def build_phase1_lab_configs(
    profile: str = "default",
    lab_config_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Return the phase-1 lab configs for a given V8 profile."""
    phase1_configs = _load_phase1_base_configs(lab_config_path=lab_config_path)
    if profile.lower() == "default":
        return phase1_configs

    preset = get_profile_preset(profile)
    return build_phase1_lab_configs_for_allocations(
        lab_allocations=preset["lab_allocations"],
        lab_config_path=lab_config_path,
    )


def build_phase1_lab_strategies(
    profile: str = "default",
    lab_config_path: str | Path | None = None,
) -> list[BaseStrategy]:
    """Instantiate the immediate V8 lab sleeves for a given profile."""
    strategy_configs = build_phase1_lab_configs(profile=profile, lab_config_path=lab_config_path)
    strategies: list[BaseStrategy] = []
    for name, params in strategy_configs.items():
        if not params.get("enabled", True):
            continue
        strategy_cls = LAB_CLASS_MAP[name]
        strategy = strategy_cls(dict(params))
        strategy.name = name
        strategies.append(strategy)
    return strategies


def build_phase1_lab_strategies_for_allocations(
    lab_allocations: dict[str, float] | None = None,
    lab_config_path: str | Path | None = None,
) -> list[BaseStrategy]:
    """Instantiate phase-1 lab sleeves from explicit target allocations."""
    strategy_configs = build_phase1_lab_configs_for_allocations(
        lab_allocations=lab_allocations,
        lab_config_path=lab_config_path,
    )
    strategies: list[BaseStrategy] = []
    for name, params in strategy_configs.items():
        if not params.get("enabled", True):
            continue
        strategy_cls = LAB_CLASS_MAP[name]
        strategy = strategy_cls(dict(params))
        strategy.name = name
        strategies.append(strategy)
    return strategies


def build_custom_strategy_suite(
    initial_capital: float,
    core_fraction: float = 1.0,
    lab_allocations: dict[str, float] | None = None,
    lab_config_path: str | Path | None = None,
) -> list[BaseStrategy]:
    """Build a V7.4 core plus explicit phase-1 lab allocations on the same runner."""
    if not 0.0 <= core_fraction <= 1.0:
        raise ValueError(f"core_fraction must be between 0 and 1, got {core_fraction}")

    strategies: list[BaseStrategy] = create_v74_strategies(initial_capital=initial_capital * core_fraction)
    if lab_allocations:
        strategies.extend(
            build_phase1_lab_strategies_for_allocations(
                lab_allocations=lab_allocations,
                lab_config_path=lab_config_path,
            )
        )
    return strategies


def build_v8_strategy_suite(
    initial_capital: float,
    profile: str,
    lab_config_path: str | Path | None = None,
) -> list[BaseStrategy]:
    """Build the V7.4 control core plus optional V8 phase-1 sleeves."""
    preset = get_profile_preset(profile)
    return build_custom_strategy_suite(
        initial_capital=initial_capital,
        core_fraction=float(preset["core_fraction"]),
        lab_allocations=preset["lab_allocations"],
        lab_config_path=lab_config_path,
    )
