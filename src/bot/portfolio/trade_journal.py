"""Trade Journal — persistent storage for every fill and paired trade.

Stores each FillEvent to SQLite on disk, and pairs fills into round-trip
trades for analysis.  Exports DataFrames compatible with backtest_tool's
``_extract_trades()`` / ``render_trade_log()`` formats.

Usage (standalone)::

    journal = TradeJournal("./data/trades.db")
    journal.record_fill(fill_event)
    df = journal.export_fills()          # raw fills
    df = journal.export_trades()         # paired round-trips

Usage (with EventBus)::

    journal = TradeJournal("./data/trades.db")
    journal.attach(event_bus)            # auto-records every FillEvent
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import structlog

if TYPE_CHECKING:
    from bot.core.event_bus import EventBus

from bot.core.constants import EventType, OrderSide
from bot.core.events import FillEvent

logger = structlog.get_logger(__name__)

_SCHEMA_VERSION = 1

_CREATE_FILLS = """
CREATE TABLE IF NOT EXISTS fills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    strategy    TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    side        TEXT    NOT NULL,
    quantity    REAL    NOT NULL,
    price       REAL    NOT NULL,
    commission  REAL    NOT NULL DEFAULT 0,
    comm_asset  TEXT    NOT NULL DEFAULT 'USDT',
    order_id    TEXT    NOT NULL DEFAULT '',
    client_oid  TEXT    NOT NULL DEFAULT '',
    realized_pnl REAL  NOT NULL DEFAULT 0,
    source      TEXT    NOT NULL DEFAULT 'live'
);
"""

_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy        TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    direction       TEXT    NOT NULL,
    entry_time      TEXT    NOT NULL,
    entry_price     REAL    NOT NULL,
    exit_time       TEXT,
    exit_price      REAL,
    size            REAL    NOT NULL,
    pnl             REAL    NOT NULL DEFAULT 0,
    fees            REAL    NOT NULL DEFAULT 0,
    holding_bars    INTEGER NOT NULL DEFAULT 0,
    source          TEXT    NOT NULL DEFAULT 'live',
    entry_fill_id   INTEGER,
    exit_fill_id    INTEGER,
    FOREIGN KEY (entry_fill_id) REFERENCES fills(id),
    FOREIGN KEY (exit_fill_id)  REFERENCES fills(id)
);
"""

_CREATE_META = """
CREATE TABLE IF NOT EXISTS journal_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class TradeJournal:
    """SQLite-backed trade journal with EventBus integration."""

    def __init__(self, db_path: str | Path = "./data/trades.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()
        logger.info("trade_journal_initialized", db=str(self._db_path))

    # ── Schema ────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(f"{_CREATE_FILLS}\n{_CREATE_TRADES}\n{_CREATE_META}")

        cur.execute(
            "INSERT OR IGNORE INTO journal_meta (key, value) VALUES (?, ?)",
            ("schema_version", str(_SCHEMA_VERSION)),
        )
        self._conn.commit()

    # ── EventBus integration ──────────────────────────────────────────

    def attach(self, event_bus: EventBus) -> None:
        """Subscribe to FillEvent on an EventBus."""
        event_bus.subscribe(EventType.FILL.value, self._on_fill)
        logger.info("trade_journal_attached_to_event_bus")

    def _on_fill(self, event: FillEvent) -> None:
        self.record_fill(event)

    # ── Write operations ──────────────────────────────────────────────

    def record_fill(self, fill: FillEvent) -> int:
        """Persist a single fill and update the paired-trade tracker.

        Returns:
            The ``fills.id`` of the newly inserted row.
        """
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """INSERT INTO fills
                   (timestamp, strategy, symbol, side, quantity, price,
                    commission, comm_asset, order_id, client_oid,
                    realized_pnl, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fill.timestamp.isoformat(),
                    fill.strategy_name,
                    fill.symbol,
                    fill.side.value,
                    fill.quantity,
                    fill.price,
                    fill.commission,
                    fill.commission_asset,
                    fill.order_id,
                    fill.client_order_id,
                    fill.realized_pnl,
                    fill.source,
                ),
            )
            fill_id = cur.lastrowid or 0
            self._conn.commit()

        self._try_pair_trade(fill, fill_id)

        logger.debug(
            "fill_recorded",
            fill_id=fill_id,
            symbol=fill.symbol,
            side=fill.side.value,
            qty=fill.quantity,
            price=fill.price,
        )
        return fill_id

    def _try_pair_trade(self, fill: FillEvent, fill_id: int) -> None:
        """Attempt to pair this fill into a round-trip trade.

        Simple heuristic:
        - BUY opens a Long, SELL closes it (and vice-versa for Short).
        - Looks for the most recent open trade for the same (strategy, symbol).
        """
        with self._lock:
            cur = self._conn.cursor()

            # Find the latest open trade (no exit yet) for this strategy+symbol
            cur.execute(
                """SELECT id, direction, entry_price, size, fees
                   FROM trades
                   WHERE strategy = ? AND symbol = ? AND exit_time IS NULL
                   ORDER BY id DESC LIMIT 1""",
                (fill.strategy_name, fill.symbol),
            )
            open_trade = cur.fetchone()

            if open_trade:
                tid, direction, entry_price, size, prev_fees = open_trade
                # Check if this fill closes the position
                closes = (
                    (direction == "Long" and fill.side.value == "SELL")
                    or (direction == "Short" and fill.side.value == "BUY")
                )
                if closes:
                    if direction == "Long":
                        pnl = (fill.price - entry_price) * min(fill.quantity, size)
                    else:
                        pnl = (entry_price - fill.price) * min(fill.quantity, size)
                    total_fees = prev_fees + fill.commission

                    cur.execute(
                        """UPDATE trades
                           SET exit_time = ?, exit_price = ?, pnl = ?,
                               fees = ?, exit_fill_id = ?
                           WHERE id = ?""",
                        (
                            fill.timestamp.isoformat(),
                            fill.price,
                            pnl - total_fees,
                            total_fees,
                            fill_id,
                            tid,
                        ),
                    )
                    self._conn.commit()
                    return

            # No matching open trade → open a new one
            direction = "Long" if fill.side.value == "BUY" else "Short"
            cur.execute(
                """INSERT INTO trades
                   (strategy, symbol, direction, entry_time, entry_price,
                    size, fees, source, entry_fill_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fill.strategy_name,
                    fill.symbol,
                    direction,
                    fill.timestamp.isoformat(),
                    fill.price,
                    fill.quantity,
                    fill.commission,
                    fill.source,
                    fill_id,
                ),
            )
            self._conn.commit()

    # ── Read / export ─────────────────────────────────────────────────

    def export_fills(
        self,
        strategy: str | None = None,
        symbol: str | None = None,
        source: str | None = None,
        since: str | None = None,
    ) -> pd.DataFrame:
        """Export fills as a DataFrame.

        Args:
            strategy: Filter by strategy name.
            symbol: Filter by symbol.
            source: Filter by source ('live', 'paper', 'sim').
            since: ISO-8601 timestamp lower bound.

        Returns:
            DataFrame with one row per fill.
        """
        query = "SELECT * FROM fills WHERE 1=1"
        params: list[Any] = []
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if source:
            query += " AND source = ?"
            params.append(source)
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        query += " ORDER BY id"

        with self._lock:
            df = pd.read_sql_query(query, self._conn, params=params)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df

    def export_trades(
        self,
        strategy: str | None = None,
        symbol: str | None = None,
        closed_only: bool = True,
    ) -> pd.DataFrame:
        """Export paired trades as a DataFrame.

        The output columns match backtest_tool's trade_log renderer:
        Direction, Entry Timestamp, Entry Price, Exit Timestamp,
        Exit Price, Size, PnL, Fees.

        Args:
            strategy: Filter by strategy name.
            symbol: Filter by symbol.
            closed_only: Only return trades with an exit (default True).

        Returns:
            DataFrame compatible with ``render_trade_log()``.
        """
        query = "SELECT * FROM trades WHERE 1=1"
        params: list[Any] = []
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if closed_only:
            query += " AND exit_time IS NOT NULL"
        query += " ORDER BY id"

        with self._lock:
            df = pd.read_sql_query(query, self._conn, params=params)

        if df.empty:
            return df

        # Rename to match backtest_tool's trade_log column conventions
        rename_map = {
            "direction": "Direction",
            "entry_time": "Entry Timestamp",
            "entry_price": "Entry Price",
            "exit_time": "Exit Timestamp",
            "exit_price": "Exit Price",
            "size": "Size",
            "pnl": "PnL",
            "fees": "Fees",
        }
        df = df.rename(columns=rename_map)
        for col in ("Entry Timestamp", "Exit Timestamp"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
        return df

    def summary(
        self,
        strategy: str | None = None,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """Quick summary statistics.

        Returns:
            Dict with total_fills, total_trades, win_rate, net_pnl, total_fees.
        """
        fills = self.export_fills(strategy=strategy, symbol=symbol)
        trades = self.export_trades(strategy=strategy, symbol=symbol)

        if trades.empty:
            return {
                "total_fills": len(fills),
                "total_trades": 0,
                "win_rate": 0.0,
                "net_pnl": 0.0,
                "total_fees": 0.0,
            }

        pnl = trades["PnL"]
        wins = pnl[pnl > 0]
        return {
            "total_fills": len(fills),
            "total_trades": len(trades),
            "win_rate": len(wins) / len(trades) if len(trades) > 0 else 0.0,
            "net_pnl": float(pnl.sum()),
            "total_fees": float(trades["Fees"].sum()),
            "avg_pnl": float(pnl.mean()),
            "best_trade": float(pnl.max()),
            "worst_trade": float(pnl.min()),
        }

    # ── Server Disconnect Resilience ─────────────────────────────────

    def reconcile(
        self,
        recent_fills: list[dict[str, Any]],
        *,
        dedup_key: str = "order_id",
    ) -> int:
        """Back-fill missed fills after a server disconnect.

        Call this on startup with fills fetched from Binance REST
        (``/fapi/v1/userTrades``).  Any fill whose *dedup_key* is already
        in the journal is skipped; new ones are inserted and paired.

        Args:
            recent_fills: List of dicts with the same keys as ``record_fill``
                          (timestamp, strategy, symbol, side, qty, price, ...).
            dedup_key: Column used to detect duplicates (default ``order_id``).

        Returns:
            Number of newly inserted fills.
        """
        existing = set()
        with self._lock:
            cur = self._conn.execute(f"SELECT {dedup_key} FROM fills")
            existing = {row[0] for row in cur.fetchall()}

        inserted = 0
        for f in recent_fills:
            key_val = f.get(dedup_key)
            if key_val and key_val in existing:
                continue
            fill_event = FillEvent(
                timestamp=f["timestamp"],
                strategy_name=f.get("strategy", "unknown"),
                symbol=f["symbol"],
                side=OrderSide(f["side"]),
                quantity=f["qty"],
                price=f["price"],
                commission=f.get("commission", 0.0),
                commission_asset=f.get("comm_asset", "USDT"),
                order_id=f.get("order_id", ""),
                client_order_id=f.get("client_oid", ""),
                realized_pnl=f.get("realized_pnl", 0.0),
                source=f.get("source", "reconcile"),
            )
            self.record_fill(fill_event)
            inserted += 1

        if inserted > 0:
            logger.info("reconciled_fills", count=inserted)
        return inserted

    def open_trades(self) -> pd.DataFrame:
        """Return trades that have an entry but no exit yet.

        Useful on restart to know which positions are still open.
        """
        query = "SELECT * FROM trades WHERE exit_time IS NULL"
        with self._lock:
            return pd.read_sql_query(query, self._conn)

    # ── Housekeeping ──────────────────────────────────────────────────

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
        logger.info("trade_journal_closed")

    def __del__(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
