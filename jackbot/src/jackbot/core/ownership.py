"""Helpers for recognizing Jackbot-owned exchange orders."""

from __future__ import annotations

import re


_LEGACY_CLIENT_ORDER_RE = re.compile(
    r"^jb_grid_(?P<grid_id>grid_[A-Z0-9]+_[a-f0-9]{8})_"
    r"(?P<level>\d{2})_(?P<side>[BS])_(?P<cycle>\d{2})$"
)
_SHORT_CLIENT_ORDER_RE = re.compile(
    r"^jb_(?P<symbol>[A-Z0-9]+)_(?P<token>[a-f0-9]{8})_"
    r"(?P<level>\d{2})_(?P<side>[BS])_(?P<cycle>\d{2})$"
)


def make_grid_client_order_id(grid_id: str, level_index: int, side: str, cycle: int = 0) -> str:
    """Build a compact, parseable Binance clientOrderId for grid orders."""
    side_token = "B" if side.upper().startswith("B") else "S"
    prefix = "grid_"
    if grid_id.startswith(prefix):
        body = grid_id[len(prefix):]
        if "_" in body:
            symbol, token = body.rsplit("_", 1)
            return f"jb_{symbol}_{token}_{level_index:02d}_{side_token}_{cycle:02d}"
    return f"jb_{grid_id[-12:]}_{level_index:02d}_{side_token}_{cycle:02d}"


def parse_jackbot_client_order_id(client_order_id: str) -> tuple[str, int] | None:
    """Return ``(grid_id, level_index)`` if the id belongs to Jackbot."""
    match = _SHORT_CLIENT_ORDER_RE.match(client_order_id or "")
    if match:
        grid_id = f"grid_{match.group('symbol')}_{match.group('token')}"
        return grid_id, int(match.group("level"))

    legacy_match = _LEGACY_CLIENT_ORDER_RE.match(client_order_id or "")
    if legacy_match:
        return legacy_match.group("grid_id"), int(legacy_match.group("level"))

    return None


def is_jackbot_order(client_order_id: str) -> bool:
    return parse_jackbot_client_order_id(client_order_id) is not None
