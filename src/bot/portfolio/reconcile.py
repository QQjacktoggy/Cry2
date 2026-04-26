"""Startup reconciliation helper.

Fetches recent fills from Binance REST and back-fills any gaps in the
TradeJournal after a disconnect.  Shared by run_live.py and run_paper.py.
"""

from __future__ import annotations

import datetime as _dt
from typing import TYPE_CHECKING, Any

import structlog

from bot.utils.id_generator import extract_strategy_from_client_oid

if TYPE_CHECKING:
    from bot.exchange.binance_rest import BinanceRestClient
    from bot.portfolio.trade_journal import TradeJournal

logger = structlog.get_logger(__name__)


def reconcile_from_binance(
    client: BinanceRestClient,
    journal: TradeJournal,
    log: Any = None,
    known_strategies: list[str] | None = None,
    strategy_resolver: Any = None,
) -> int:
    """Fetch recent fills from Binance and back-fill any missing from the journal.

    Called on startup to recover fills that occurred during a disconnect.
    Queries the last 100 trades per symbol found in open trades or 24h fills.

    Args:
        client: Binance REST client.
        journal: TradeJournal to back-fill.
        log: Optional structlog logger.
        known_strategies: Full strategy names used to resolve the 8-char prefix
            embedded in client_order_id (e.g. ``["trend_donchian", ...]``).

    Returns:
        Number of newly inserted fills.
    """
    _log = log or logger

    open_trades = journal.open_trades()
    symbols: set[str] = set()
    if not open_trades.empty:
        symbols = set(open_trades["symbol"].unique())

    fills_df = journal.export_fills()
    if not fills_df.empty:
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=24)
        recent = fills_df[fills_df["timestamp"] >= cutoff]
        if not recent.empty:
            symbols.update(recent["symbol"].unique())

    if not symbols:
        _log.info("reconcile_skipped", reason="no_active_symbols")
        return 0

    total_inserted = 0
    for sym in symbols:
        try:
            raw_trades = client.get_account_trades(sym, limit=100)
            mapped = [
                {
                    "timestamp": _dt.datetime.fromtimestamp(
                        int(t["time"]) / 1000, tz=_dt.timezone.utc
                    ),
                    "strategy": (
                        strategy_resolver(
                            str(t.get("orderId", "")),
                            str(t.get("clientOrderId", "")),
                        )
                        if strategy_resolver is not None
                        else None
                    )
                    or (
                        extract_strategy_from_client_oid(
                            str(t.get("clientOrderId", "")), known_strategies
                        ) or "unknown"
                    ),
                    "symbol": t["symbol"],
                    "side": t["side"],
                    "qty": float(t["qty"]),
                    "price": float(t["price"]),
                    "commission": float(t.get("commission", 0)),
                    "comm_asset": t.get("commissionAsset", "USDT"),
                    "exchange_fill_id": str(t.get("id", "")),
                    "order_id": str(t.get("orderId", "")),
                    "client_oid": "",
                    "realized_pnl": float(t.get("realizedPnl", 0)),
                    "source": "reconcile",
                }
                for t in raw_trades
            ]
            inserted = journal.reconcile(mapped, dedup_key="exchange_fill_id")
            total_inserted += inserted
        except Exception as e:
            _log.warning("reconcile_symbol_failed", symbol=sym, error=str(e))

    if total_inserted > 0:
        _log.info("reconcile_complete", new_fills=total_inserted)
    else:
        _log.info("reconcile_complete", message="no_missing_fills")
    return total_inserted
