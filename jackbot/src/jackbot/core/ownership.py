"""Helpers for recognizing Jackbot-owned exchange orders."""

from __future__ import annotations

import re


_CLIENT_ORDER_RE = re.compile(
    r"^jb_grid_(?P<grid_id>grid_[A-Z0-9]+_[a-f0-9]{8})_"
    r"(?P<level>\d{2})_(?P<side>[BS])_(?P<cycle>\d{2})$"
)


def make_grid_client_order_id(grid_id: str, level_index: int, side: str, cycle: int = 0) -> str:
    """Build a compact, parseable Binance clientOrderId for grid orders."""
    side_token = "B" if side.upper().startswith("B") else "S"
    return f"jb_grid_{grid_id}_{level_index:02d}_{side_token}_{cycle:02d}"


def parse_jackbot_client_order_id(client_order_id: str) -> tuple[str, int] | None:
    """Return ``(grid_id, level_index)`` if the id belongs to Jackbot."""
    match = _CLIENT_ORDER_RE.match(client_order_id or "")
    if not match:
        return None
    return match.group("grid_id"), int(match.group("level"))


def is_jackbot_order(client_order_id: str) -> bool:
    return parse_jackbot_client_order_id(client_order_id) is not None
