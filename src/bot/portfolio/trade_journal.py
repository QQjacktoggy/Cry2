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
    from bot.runtime.deploy_meta import DeployMeta

from bot.core.constants import EventType, OrderSide
from bot.core.events import FillEvent, SignalEvent

logger = structlog.get_logger(__name__)

_SCHEMA_VERSION = 4

_DEPLOY_META_COLUMNS = [
    ("run_id", "TEXT NOT NULL DEFAULT ''"),
    ("config_fingerprint", "TEXT NOT NULL DEFAULT ''"),
    ("git_sha", "TEXT NOT NULL DEFAULT ''"),
    ("environment", "TEXT NOT NULL DEFAULT ''"),
    ("version", "TEXT NOT NULL DEFAULT ''"),
]

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
    exchange_fill_id TEXT NOT NULL DEFAULT '',
    realized_pnl REAL  NOT NULL DEFAULT 0,
    source      TEXT    NOT NULL DEFAULT 'live',
    run_id             TEXT NOT NULL DEFAULT '',
    config_fingerprint TEXT NOT NULL DEFAULT '',
    git_sha            TEXT NOT NULL DEFAULT '',
    environment        TEXT NOT NULL DEFAULT '',
    version            TEXT NOT NULL DEFAULT ''
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
    remaining_size  REAL    NOT NULL DEFAULT 0,
    closed_qty      REAL    NOT NULL DEFAULT 0,
    exit_notional   REAL    NOT NULL DEFAULT 0,
    pnl             REAL    NOT NULL DEFAULT 0,
    fees            REAL    NOT NULL DEFAULT 0,
    holding_bars    INTEGER NOT NULL DEFAULT 0,
    source          TEXT    NOT NULL DEFAULT 'live',
    entry_fill_id   INTEGER,
    exit_fill_id    INTEGER,
    run_id             TEXT NOT NULL DEFAULT '',
    config_fingerprint TEXT NOT NULL DEFAULT '',
    git_sha            TEXT NOT NULL DEFAULT '',
    environment        TEXT NOT NULL DEFAULT '',
    version            TEXT NOT NULL DEFAULT '',
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

_CREATE_RUNTIME_EVENTS = """
CREATE TABLE IF NOT EXISTS runtime_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    run_id      TEXT    NOT NULL DEFAULT '',
    event_type  TEXT    NOT NULL,
    severity    TEXT    NOT NULL DEFAULT 'info',
    strategy    TEXT    NOT NULL DEFAULT '',
    symbol      TEXT    NOT NULL DEFAULT '',
    details     TEXT    NOT NULL DEFAULT '{}'
);
"""

_CREATE_EQUITY_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    run_id          TEXT    NOT NULL DEFAULT '',
    equity          REAL    NOT NULL,
    realized_pnl    REAL    NOT NULL DEFAULT 0,
    unrealized_pnl  REAL    NOT NULL DEFAULT 0,
    positions       TEXT    NOT NULL DEFAULT '[]'
);
"""


class TradeJournal:
    """SQLite-backed trade journal with EventBus integration."""

    def __init__(
        self,
        db_path: str | Path = "./data/trades.db",
        *,
        deploy_meta: DeployMeta | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._deploy_meta: DeployMeta | None = deploy_meta
        self._init_schema()
        logger.info(
            "trade_journal_initialized",
            db=str(self._db_path),
            run_id=(deploy_meta.run_id if deploy_meta else ""),
            config_fingerprint=(deploy_meta.config_fingerprint if deploy_meta else ""),
        )

    # ── Schema ────────────────────────────────────────────────────────

    def _existing_columns(self, table: str) -> set[str]:
        cur = self._conn.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}

    def _migrate_deploy_meta_columns(self, table: str) -> None:
        existing = self._existing_columns(table)
        for column, decl in _DEPLOY_META_COLUMNS:
            if column not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def _migrate_fill_identity_columns(self) -> None:
        existing = self._existing_columns("fills")
        if "exchange_fill_id" not in existing:
            self._conn.execute(
                "ALTER TABLE fills ADD COLUMN exchange_fill_id TEXT NOT NULL DEFAULT ''"
            )

    def _migrate_trade_aggregation_columns(self) -> None:
        existing = self._existing_columns("trades")
        for column, decl in [
            ("remaining_size", "REAL NOT NULL DEFAULT 0"),
            ("closed_qty", "REAL NOT NULL DEFAULT 0"),
            ("exit_notional", "REAL NOT NULL DEFAULT 0"),
        ]:
            if column not in existing:
                self._conn.execute(f"ALTER TABLE trades ADD COLUMN {column} {decl}")

        self._conn.execute(
            """UPDATE trades
               SET remaining_size = CASE
                   WHEN exit_time IS NULL AND remaining_size = 0 THEN size
                   ELSE remaining_size
               END"""
        )
        self._conn.execute(
            """UPDATE trades
               SET closed_qty = CASE
                   WHEN exit_time IS NOT NULL AND closed_qty = 0 THEN size
                   ELSE closed_qty
               END"""
        )
        self._conn.execute(
            """UPDATE trades
               SET exit_notional = CASE
                   WHEN exit_time IS NOT NULL AND exit_price IS NOT NULL AND exit_notional = 0
                       THEN size * exit_price
                   ELSE exit_notional
               END"""
        )

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            "\n".join(
                [
                    _CREATE_FILLS,
                    _CREATE_TRADES,
                    _CREATE_META,
                    _CREATE_RUNTIME_EVENTS,
                    _CREATE_EQUITY_SNAPSHOTS,
                ]
            )
        )

        # Additive migration for pre-existing databases: deploy meta columns.
        self._migrate_fill_identity_columns()
        self._migrate_trade_aggregation_columns()
        self._migrate_deploy_meta_columns("fills")
        self._migrate_deploy_meta_columns("trades")

        cur.execute(
            "INSERT OR REPLACE INTO journal_meta (key, value) VALUES (?, ?)",
            ("schema_version", str(_SCHEMA_VERSION)),
        )
        self._conn.commit()

    # ── Deploy metadata ────────────────────────────────────────────────

    def set_deploy_meta(self, meta: DeployMeta) -> None:
        """Stamp subsequent writes with the given deployment metadata."""
        self._deploy_meta = meta

    def _deploy_meta_values(self) -> tuple[str, str, str, str, str]:
        """Return the five deploy meta fields in the column order used by INSERTs."""
        if self._deploy_meta is None:
            return ("", "", "", "", "")
        return (
            self._deploy_meta.run_id,
            self._deploy_meta.config_fingerprint,
            self._deploy_meta.git_sha,
            self._deploy_meta.environment,
            self._deploy_meta.version,
        )

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
            deploy = self._deploy_meta_values()
            cur.execute(
                """INSERT INTO fills
                   (timestamp, strategy, symbol, side, quantity, price,
                    commission, comm_asset, order_id, client_oid, exchange_fill_id,
                    realized_pnl, source,
                     run_id, config_fingerprint, git_sha, environment, version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?)""",
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
                    fill.fill_id,
                    fill.realized_pnl,
                    fill.source,
                    *deploy,
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

    @staticmethod
    def _normalize_quantity(quantity: float) -> float:
        if abs(quantity) < 1e-12:
            return 0.0
        return quantity

    @staticmethod
    def _reconcile_signature(
        timestamp: Any,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_id: str,
    ) -> tuple[str, str, str, float, float, str]:
        ts_value = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
        return (
            ts_value,
            symbol,
            side,
            round(float(quantity), 12),
            round(float(price), 12),
            order_id,
        )

    def _open_trade(
        self,
        cur: sqlite3.Cursor,
        fill: FillEvent,
        fill_id: int,
        *,
        quantity: float,
        commission: float,
    ) -> None:
        direction = "Long" if fill.side.value == "BUY" else "Short"
        deploy = self._deploy_meta_values()
        cur.execute(
            """INSERT INTO trades
               (strategy, symbol, direction, entry_time, entry_price,
                size, remaining_size, closed_qty, exit_notional,
                fees, pnl, source, entry_fill_id,
                run_id, config_fingerprint, git_sha, environment, version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?)""",
            (
                fill.strategy_name,
                fill.symbol,
                direction,
                fill.timestamp.isoformat(),
                fill.price,
                quantity,
                quantity,
                0.0,
                0.0,
                commission,
                -commission,
                fill.source,
                fill_id,
                *deploy,
            ),
        )

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
                """SELECT id, direction, entry_price, size, remaining_size,
                          closed_qty, exit_notional, fees, pnl
                   FROM trades
                   WHERE strategy = ? AND symbol = ? AND exit_time IS NULL
                   ORDER BY id DESC LIMIT 1""",
                (fill.strategy_name, fill.symbol),
            )
            open_trade = cur.fetchone()

            if open_trade:
                (
                    tid,
                    direction,
                    entry_price,
                    size,
                    remaining_size,
                    closed_qty,
                    exit_notional,
                    prev_fees,
                    prev_pnl,
                ) = open_trade
                same_direction = (
                    (direction == "Long" and fill.side.value == "BUY")
                    or (direction == "Short" and fill.side.value == "SELL")
                )
                # Check if this fill closes the position
                closes = (
                    (direction == "Long" and fill.side.value == "SELL")
                    or (direction == "Short" and fill.side.value == "BUY")
                )
                if same_direction:
                    new_size = size + fill.quantity
                    new_remaining_size = remaining_size + fill.quantity
                    weighted_entry = (
                        (entry_price * remaining_size + fill.price * fill.quantity)
                        / new_remaining_size
                        if new_remaining_size > 0
                        else fill.price
                    )
                    total_fees = prev_fees + fill.commission
                    cur.execute(
                        """UPDATE trades
                           SET entry_price = ?, size = ?, remaining_size = ?, fees = ?, pnl = ?
                           WHERE id = ?""",
                        (
                            weighted_entry,
                            new_size,
                            new_remaining_size,
                            total_fees,
                            prev_pnl - fill.commission,
                            tid,
                        ),
                    )
                    self._conn.commit()
                    return
                if closes:
                    fill_qty = self._normalize_quantity(fill.quantity)
                    close_qty = min(fill_qty, remaining_size)
                    close_commission = (
                        fill.commission * (close_qty / fill_qty)
                        if fill_qty > 0
                        else 0.0
                    )
                    if direction == "Long":
                        realized = (fill.price - entry_price) * close_qty
                    else:
                        realized = (entry_price - fill.price) * close_qty
                    total_fees = prev_fees + close_commission
                    updated_pnl = prev_pnl + realized - close_commission
                    new_remaining_size = self._normalize_quantity(remaining_size - close_qty)
                    new_closed_qty = closed_qty + close_qty
                    new_exit_notional = exit_notional + close_qty * fill.price
                    weighted_exit = (
                        new_exit_notional / new_closed_qty
                        if new_closed_qty > 0
                        else fill.price
                    )

                    if new_remaining_size > 0:
                        cur.execute(
                            """UPDATE trades
                               SET remaining_size = ?, closed_qty = ?, exit_notional = ?,
                                   exit_price = ?, pnl = ?, fees = ?
                               WHERE id = ?""",
                            (
                                new_remaining_size,
                                new_closed_qty,
                                new_exit_notional,
                                weighted_exit,
                                updated_pnl,
                                total_fees,
                                tid,
                            ),
                        )
                    else:
                        residual_qty = self._normalize_quantity(fill_qty - close_qty)
                        residual_commission = max(fill.commission - close_commission, 0.0)
                        cur.execute(
                            """UPDATE trades
                               SET exit_time = ?, exit_price = ?, pnl = ?,
                                    fees = ?, exit_fill_id = ?, remaining_size = ?,
                                   closed_qty = ?, exit_notional = ?
                               WHERE id = ?""",
                            (
                                fill.timestamp.isoformat(),
                                weighted_exit,
                                updated_pnl,
                                total_fees,
                                fill_id,
                                0.0,
                                new_closed_qty,
                                new_exit_notional,
                                tid,
                            ),
                        )
                        if residual_qty > 0:
                            self._open_trade(
                                cur,
                                fill,
                                fill_id,
                                quantity=residual_qty,
                                commission=residual_commission,
                            )
                    self._conn.commit()
                    return

            # No matching open trade → open a new one
            self._open_trade(
                cur,
                fill,
                fill_id,
                quantity=fill.quantity,
                commission=fill.commission,
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
        dedup_key: str = "exchange_fill_id",
    ) -> int:
        """Back-fill missed fills after a server disconnect.

        Call this on startup with fills fetched from Binance REST
        (``/fapi/v1/userTrades``).  Any fill whose *dedup_key* is already
        in the journal is skipped; new ones are inserted and paired.

        Args:
            recent_fills: List of dicts with the same keys as ``record_fill``
                          (timestamp, strategy, symbol, side, qty, price, ...).
            dedup_key: Column used to detect duplicates (default
                ``exchange_fill_id`` for per-fill Binance trade ids).

        Returns:
            Number of newly inserted fills.
        """
        existing = set()
        legacy_signatures: set[tuple[str, str, str, float, float, str]] = set()
        with self._lock:
            cur = self._conn.execute(f"SELECT {dedup_key} FROM fills")
            existing = {row[0] for row in cur.fetchall()}
            if dedup_key == "exchange_fill_id":
                legacy_rows = self._conn.execute(
                    """SELECT timestamp, symbol, side, quantity, price, order_id
                       FROM fills
                       WHERE exchange_fill_id = ''"""
                )
                legacy_signatures = {
                    self._reconcile_signature(*row) for row in legacy_rows.fetchall()
                }

        inserted = 0
        for f in recent_fills:
            key_val = f.get(dedup_key)
            signature = self._reconcile_signature(
                f["timestamp"],
                f["symbol"],
                f["side"],
                f["qty"],
                f["price"],
                f.get("order_id", ""),
            )
            if key_val and key_val in existing:
                continue
            if dedup_key == "exchange_fill_id" and signature in legacy_signatures:
                continue
            fill_event = FillEvent(
                timestamp=f["timestamp"],
                fill_id=str(f.get("exchange_fill_id", "")),
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
            if key_val:
                existing.add(key_val)
            if dedup_key == "exchange_fill_id":
                legacy_signatures.add(signature)

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

    # ── Runtime events & equity snapshots (review-bundle data) ───────

    def record_runtime_event(
        self,
        event_type: str,
        *,
        severity: str = "info",
        strategy: str = "",
        symbol: str = "",
        details: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> int:
        """Persist a non-fill runtime event (signals, rejects, WS state, halts, ...).

        Review-bundle analyzers read this table to separate *engineering*
        problems (WS drops, API latency, reconcile deltas) from *strategy*
        problems (rejects, halts) when evaluating paper runs.
        """
        import json as _json
        from datetime import UTC as _UTC
        from datetime import datetime as _dt

        ts = timestamp or _dt.now(_UTC).isoformat()
        run_id = self._deploy_meta.run_id if self._deploy_meta else ""
        payload = _json.dumps(details or {}, default=str, sort_keys=True)
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO runtime_events
                   (timestamp, run_id, event_type, severity, strategy, symbol, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ts, run_id, event_type, severity, strategy, symbol, payload),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def record_signal_generated(
        self,
        signal: SignalEvent,
        *,
        effective_max_leverage: float | None = None,
        timestamp: str | None = None,
    ) -> int:
        """Persist a generated signal with leverage context for later analysis."""
        details: dict[str, Any] = {
            "side": signal.side.value,
            "order_type": signal.order_type.value,
            "quantity": signal.quantity,
            "price": signal.price,
            "stop_price": signal.stop_price,
            "reason": signal.reason,
            "reduce_only": signal.reduce_only,
            "post_only": signal.post_only,
        }
        if signal.metadata:
            details["metadata"] = dict(signal.metadata)

        requested = signal.metadata.get("requested_leverage")
        if requested is not None:
            try:
                requested_leverage = float(requested)
            except (TypeError, ValueError):
                details["requested_leverage"] = requested
            else:
                details["requested_leverage"] = requested_leverage
                if effective_max_leverage is not None:
                    effective = float(effective_max_leverage)
                    details["effective_max_leverage"] = effective
                    details["applied_leverage"] = min(requested_leverage, effective)
        elif effective_max_leverage is not None:
            details["effective_max_leverage"] = float(effective_max_leverage)

        return self.record_runtime_event(
            "signal_generated",
            severity="info",
            strategy=signal.strategy_name,
            symbol=signal.symbol,
            details=details,
            timestamp=timestamp,
        )

    def record_equity_snapshot(
        self,
        *,
        equity: float,
        realized_pnl: float = 0.0,
        unrealized_pnl: float = 0.0,
        positions: list[dict[str, Any]] | None = None,
        timestamp: str | None = None,
    ) -> int:
        """Persist a point-in-time equity + positions snapshot.

        Intended cadence: at least once per day (end-of-session) and on
        every graceful shutdown. The positions payload is JSON-encoded so
        analyzers can pick whatever fields they need without the journal
        dictating a fixed position schema.
        """
        import json as _json
        from datetime import UTC as _UTC
        from datetime import datetime as _dt

        ts = timestamp or _dt.now(_UTC).isoformat()
        run_id = self._deploy_meta.run_id if self._deploy_meta else ""
        pos_payload = _json.dumps(positions or [], default=str, sort_keys=True)
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO equity_snapshots
                   (timestamp, run_id, equity, realized_pnl, unrealized_pnl, positions)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ts, run_id, float(equity), float(realized_pnl), float(unrealized_pnl), pos_payload),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def export_runtime_events(
        self,
        event_type: str | None = None,
        run_id: str | None = None,
    ) -> pd.DataFrame:
        """Return runtime events for analysis, optionally filtered."""
        query = "SELECT * FROM runtime_events WHERE 1=1"
        params: list[Any] = []
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        query += " ORDER BY id"
        with self._lock:
            return pd.read_sql_query(query, self._conn, params=params)

    def export_equity_snapshots(self, run_id: str | None = None) -> pd.DataFrame:
        """Return equity snapshots, optionally filtered by run_id."""
        query = "SELECT * FROM equity_snapshots"
        params: list[Any] = []
        if run_id:
            query += " WHERE run_id = ?"
            params.append(run_id)
        query += " ORDER BY id"
        with self._lock:
            return pd.read_sql_query(query, self._conn, params=params)

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
