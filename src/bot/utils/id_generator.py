"""Unique ID generation for client order IDs and run IDs."""

from __future__ import annotations

import time
import uuid


def generate_client_order_id(strategy_name: str = "", prefix: str = "bot") -> str:
    """Generate a unique client order ID.

    Format: {prefix}_{strategy}_{timestamp_ms}_{short_uuid}
    Max length: 36 chars (Binance limit)
    """
    ts = int(time.time() * 1000) % 10_000_000_000  # last 10 digits
    short_id = uuid.uuid4().hex[:6]
    strategy_part = strategy_name[:8] if strategy_name else "gen"
    order_id = f"{prefix}_{strategy_part}_{ts}_{short_id}"
    return order_id[:36]


def generate_run_id() -> str:
    """Generate a unique backtest run ID.

    Format: run_{YYYYMMDD_HHMMSS}_{short_uuid}
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    return f"run_{ts}_{short_id}"
