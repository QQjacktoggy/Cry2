from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from bot.exchange.binance_rest import BinanceRestClient


@dataclass(slots=True)
class ExchangeResetResult:
    canceled_orders: int = 0
    closed_positions: int = 0
    balance_before: float | None = None
    balance_after: float | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RuntimeResetResult:
    archived_journal: str = ""
    cleared_health: bool = False
    errors: list[str] = field(default_factory=list)


def flatten_testnet_state(
    client: BinanceRestClient,
    *,
    symbols: Iterable[str] | None = None,
) -> ExchangeResetResult:
    """Cancel open orders and flatten any remaining futures positions."""
    result = ExchangeResetResult()

    try:
        result.balance_before = float(client.get_balance())
    except Exception as exc:
        result.errors.append(f"balance_before:{exc}")

    raw_client = client._client
    if raw_client is None:
        result.errors.append("client_unavailable")
        return result

    target_symbols = sorted({str(symbol).upper() for symbol in (symbols or []) if symbol})

    if not target_symbols:
        try:
            open_orders = raw_client.futures_get_open_orders()
            target_symbols = sorted({str(order.get("symbol", "")).upper() for order in open_orders if order.get("symbol")})
        except Exception as exc:
            result.errors.append(f"discover_symbols:{exc}")
            open_orders = []
        for order in open_orders:
            symbol = str(order.get("symbol", "")).upper()
            if not symbol:
                continue
            order_id = order.get("orderId")
            try:
                client.cancel_order(symbol=symbol, order_id=int(order_id) if order_id is not None else None)
                result.canceled_orders += 1
            except Exception as exc:
                result.errors.append(f"cancel_order:{symbol}:{exc}")
    else:
        for symbol in target_symbols:
            try:
                open_orders = raw_client.futures_get_open_orders(symbol=symbol)
            except Exception as exc:
                result.errors.append(f"list_orders:{symbol}:{exc}")
                continue
            for order in open_orders:
                order_id = order.get("orderId")
                try:
                    client.cancel_order(symbol=symbol, order_id=int(order_id) if order_id is not None else None)
                    result.canceled_orders += 1
                except Exception as exc:
                    result.errors.append(f"cancel_order:{symbol}:{exc}")

    try:
        raw_positions = raw_client.futures_position_information()
    except Exception as exc:
        result.errors.append(f"list_positions:{exc}")
        raw_positions = []

    tracked_symbols = set(target_symbols)
    for raw in raw_positions:
        symbol = str(raw.get("symbol", "")).upper()
        if tracked_symbols and symbol not in tracked_symbols:
            continue
        qty = float(raw.get("positionAmt", 0.0) or 0.0)
        if qty == 0:
            continue
        side = "SELL" if qty > 0 else "BUY"
        try:
            client.place_order(
                symbol=symbol,
                side=side,
                order_type="MARKET",
                quantity=abs(qty),
                reduce_only=True,
            )
            result.closed_positions += 1
        except Exception as exc:
            result.errors.append(f"close_position:{symbol}:{exc}")

    try:
        result.balance_after = float(client.get_balance())
    except Exception as exc:
        result.errors.append(f"balance_after:{exc}")

    return result


def clear_runtime_state(
    *,
    journal_path: str | Path | None = None,
    archive_journal: bool = False,
    health_path: str | Path | None = None,
) -> RuntimeResetResult:
    """Archive the current paper journal and clear transient runtime files."""
    result = RuntimeResetResult()

    if archive_journal and journal_path:
        try:
            archived = _archive_file(Path(journal_path))
            if archived is not None:
                result.archived_journal = str(archived)
        except Exception as exc:
            result.errors.append(f"archive_journal:{exc}")

    if health_path:
        try:
            path = Path(health_path)
            if path.exists():
                path.unlink()
                result.cleared_health = True
        except Exception as exc:
            result.errors.append(f"clear_health:{exc}")

    return result


def _archive_file(path: Path) -> Path | None:
    if not path.exists():
        return None

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.stem}.{stamp}.bak{path.suffix}")
    path.rename(target)

    for sidecar_suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{sidecar_suffix}")
        if sidecar.exists():
            sidecar.unlink()

    return target