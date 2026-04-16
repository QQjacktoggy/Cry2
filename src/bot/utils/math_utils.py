"""Precision handling and mathematical utilities for trading."""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_DOWN


def round_to_tick(price: float, tick_size: float) -> float:
    """Round price to nearest tick size."""
    if tick_size <= 0:
        return price
    d_price = Decimal(str(price))
    d_tick = Decimal(str(tick_size))
    return float((d_price / d_tick).quantize(Decimal("1"), rounding=ROUND_DOWN) * d_tick)


def round_to_step(quantity: float, step_size: float) -> float:
    """Round quantity to nearest step size (rounds down)."""
    if step_size <= 0:
        return quantity
    d_qty = Decimal(str(quantity))
    d_step = Decimal(str(step_size))
    return float((d_qty / d_step).quantize(Decimal("1"), rounding=ROUND_DOWN) * d_step)


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division that returns default on zero denominator."""
    if denominator == 0.0:
        return default
    return numerator / denominator


def pct_change(old_value: float, new_value: float) -> float:
    """Calculate percentage change."""
    if old_value == 0.0:
        return 0.0
    return (new_value - old_value) / old_value * 100.0


def annualize_return(total_return: float, days: int) -> float:
    """Annualize a total return given number of days."""
    if days <= 0 or total_return <= -1.0:
        return 0.0
    return ((1.0 + total_return) ** (365.0 / days)) - 1.0


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max."""
    return max(min_val, min(value, max_val))


def is_close(a: float, b: float, rel_tol: float = 1e-9) -> bool:
    """Check if two floats are approximately equal."""
    return math.isclose(a, b, rel_tol=rel_tol, abs_tol=1e-12)
