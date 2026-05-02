"""Configuration helpers for Jackbot scripts."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override values into a copy of base."""
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_yaml_file(path: Path) -> dict[str, Any]:
    """Load one YAML file."""
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_merged_config(base_path: Path, overlay_paths: list[Path] | None = None) -> dict[str, Any]:
    """Load base config and apply any overlays in order."""
    config = load_yaml_file(base_path)
    for overlay_path in overlay_paths or []:
        config = deep_merge(config, load_yaml_file(overlay_path))
    return config


def build_day_trader_params(
    config: dict[str, Any],
    *,
    capital_override: float | None = None,
    maker_rate: float | None = None,
    taker_rate: float | None = None,
) -> dict[str, Any]:
    """Flatten YAML sections into DayTraderConfig kwargs."""
    trading = config.get("trading", {})
    trader_params: dict[str, Any] = {}
    trader_params.update(trading)
    trader_params.update(config.get("grid", {}))
    trader_params.update(config.get("targets", {}))
    trader_params.update(config.get("conservative", {}))
    trader_params.update(config.get("risk", {}))
    trader_params.update(config.get("review", {}))
    trader_params.update(config.get("exposure", {}))
    trader_params.update(config.get("market_assessor", {}))
    trader_params.update(config.get("strategy", {}))

    selected_variant = trader_params.get("variant", "baseline_grid")
    variant_params = config.get("variants", {}).get(selected_variant, {})
    trader_params.update(variant_params)

    leverage_cfg = config.get("leverage", {})
    if leverage_cfg:
        trader_params.update(leverage_cfg)

    fees = config.get("fees", {})
    if "maker" in fees:
        trader_params["maker_fee_rate"] = fees["maker"]
    if "taker" in fees:
        trader_params["taker_fee_rate"] = fees["taker"]

    if capital_override is not None:
        trader_params["total_capital_usd"] = capital_override
    if maker_rate is not None:
        trader_params["maker_fee_rate"] = maker_rate
    if taker_rate is not None:
        trader_params["taker_fee_rate"] = taker_rate

    trader_params["strategy_variant"] = selected_variant
    return trader_params
