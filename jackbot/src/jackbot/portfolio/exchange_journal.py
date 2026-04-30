"""Exchange-sourced fill journal for Jackbot_V1."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jackbot.core.constants import GridDirection, GridLevelState
from jackbot.core.events import FillEvent
from jackbot.core.ownership import parse_jackbot_client_order_id
from jackbot.strategy.grid_engine import GridInstance, GridLevel


@dataclass(frozen=True)
class ExchangeJournalSummary:
    total_fills: int = 0
    today_fills: int = 0
    today_realized_pnl: float = 0.0
    today_commission: float = 0.0
    last_fill_at: str = ""


class ExchangeJournal:
    """Persist fills from websocket and REST reconcile into SQLite."""

    def __init__(self, path: str | Path = "data/exchange_journal.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma journal_mode=WAL")
        conn.execute("pragma synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                create table if not exists exchange_fills (
                    trade_key text primary key,
                    timestamp_ms integer not null,
                    timestamp_iso text not null,
                    symbol text not null,
                    side text not null,
                    quantity real not null,
                    price real not null,
                    notional real not null,
                    commission real not null default 0,
                    commission_asset text not null default '',
                    realized_pnl real not null default 0,
                    order_id text not null default '',
                    trade_id text not null default '',
                    client_order_id text not null default '',
                    grid_id text not null default '',
                    level_index integer not null default -1,
                    source text not null default '',
                    raw_json text not null default '{}',
                    updated_at text not null
                )
                """
            )
            conn.execute(
                "create index if not exists idx_exchange_fills_symbol_time "
                "on exchange_fills(symbol, timestamp_ms)"
            )
            conn.execute(
                """
                create table if not exists grids (
                    grid_id text primary key,
                    symbol text not null,
                    direction text not null,
                    upper_price real not null,
                    lower_price real not null,
                    grid_count integer not null,
                    leverage integer not null,
                    total_investment real not null,
                    per_level_qty real not null,
                    matched_profit real not null default 0,
                    unrealized_pnl real not null default 0,
                    total_matched integer not null default 0,
                    created_at text not null,
                    closed integer not null default 0,
                    close_reason text not null default '',
                    updated_at text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists grid_levels (
                    grid_id text not null,
                    level_index integer not null,
                    price real not null,
                    state text not null,
                    buy_order_id text not null default '',
                    sell_order_id text not null default '',
                    buy_fill_price real not null default 0,
                    sell_fill_price real not null default 0,
                    quantity real not null default 0,
                    filled_quantity real not null default 0,
                    matched_count integer not null default 0,
                    commission real not null default 0,
                    updated_at text not null,
                    primary key (grid_id, level_index)
                )
                """
            )
            conn.execute(
                """
                create table if not exists exchange_orders (
                    client_order_id text primary key,
                    grid_id text not null default '',
                    level_index integer not null default -1,
                    symbol text not null,
                    side text not null,
                    order_type text not null default '',
                    price real not null default 0,
                    quantity real not null default 0,
                    reduce_only integer not null default 0,
                    cancel_order_id text not null default '',
                    exchange_order_id text not null default '',
                    status text not null default '',
                    notional real not null default 0,
                    error_message text not null default '',
                    updated_at text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists runtime_state (
                    state_key text primary key,
                    session_started_at text not null default '',
                    safe_mode_reason text not null default '',
                    trader_json text not null default '{}',
                    portfolio_json text not null default '{}',
                    metadata_json text not null default '{}',
                    updated_at text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists reconcile_events (
                    id integer primary key autoincrement,
                    status text not null,
                    details_json text not null default '{}',
                    created_at text not null
                )
                """
            )

    def record_fill(self, fill: FillEvent | dict[str, Any], source: str | None = None) -> bool:
        if isinstance(fill, FillEvent):
            timestamp = fill.timestamp
            return self._upsert_fill(
                timestamp_ms=int(timestamp.timestamp() * 1000),
                symbol=fill.symbol,
                side=fill.side,
                quantity=fill.quantity,
                price=fill.price,
                commission=fill.commission,
                commission_asset=fill.commission_asset,
                realized_pnl=fill.realized_pnl,
                order_id=fill.order_id,
                trade_id=fill.trade_id,
                client_order_id=fill.client_order_id,
                grid_id=fill.grid_id,
                level_index=fill.level_index,
                source=source or fill.source,
                raw=fill.raw,
            )

        timestamp_ms = self._timestamp_ms(fill.get("timestamp") or fill.get("timestamp_iso"))
        client_order_id = str(fill.get("client_order_id", ""))
        parsed = parse_jackbot_client_order_id(client_order_id)
        grid_id, level_index = parsed if parsed else (str(fill.get("grid_id", "")), int(fill.get("level_index", -1)))
        return self._upsert_fill(
            timestamp_ms=timestamp_ms,
            symbol=str(fill.get("symbol", "")).upper(),
            side=str(fill.get("side", "")),
            quantity=float(fill.get("quantity", 0.0) or 0.0),
            price=float(fill.get("price", 0.0) or 0.0),
            commission=float(fill.get("commission", 0.0) or 0.0),
            commission_asset=str(fill.get("commission_asset", "")),
            realized_pnl=float(fill.get("realized_pnl", 0.0) or 0.0),
            order_id=str(fill.get("order_id", "")),
            trade_id=str(fill.get("trade_id", "")),
            client_order_id=client_order_id,
            grid_id=grid_id,
            level_index=level_index,
            source=source or str(fill.get("source", "")),
            raw=dict(fill),
        )

    def record_rest_trade(self, trade: dict[str, Any], source: str = "rest_reconcile") -> bool:
        client_order_id = str(trade.get("clientOrderId", ""))
        parsed = parse_jackbot_client_order_id(client_order_id)
        grid_id, level_index = parsed if parsed is not None else ("", -1)
        return self._upsert_fill(
            timestamp_ms=int(trade.get("time", 0) or 0),
            symbol=str(trade.get("symbol", "")).upper(),
            side=str(trade.get("side", "")),
            quantity=float(trade.get("qty", 0) or 0),
            price=float(trade.get("price", 0) or 0),
            commission=float(trade.get("commission", 0) or 0),
            commission_asset=str(trade.get("commissionAsset", "")),
            realized_pnl=float(trade.get("realizedPnl", 0) or 0),
            order_id=str(trade.get("orderId", "")),
            trade_id=str(trade.get("id", "")),
            client_order_id=client_order_id,
            grid_id=grid_id,
            level_index=level_index,
            source=source,
            raw=trade,
        )

    def _upsert_fill(
        self,
        *,
        timestamp_ms: int,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        commission: float,
        commission_asset: str,
        realized_pnl: float,
        order_id: str,
        trade_id: str,
        client_order_id: str,
        grid_id: str,
        level_index: int,
        source: str,
        raw: dict[str, Any],
    ) -> bool:
        if not symbol or timestamp_ms <= 0:
            return False
        timestamp_iso = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).isoformat()
        trade_key = self._trade_key(symbol, trade_id, order_id, timestamp_ms, side, quantity, price)
        updated_at = datetime.now(UTC).isoformat()
        with self._write_lock, self._connect() as conn:
            before = conn.total_changes
            conn.execute(
                """
                insert into exchange_fills (
                    trade_key, timestamp_ms, timestamp_iso, symbol, side,
                    quantity, price, notional, commission, commission_asset,
                    realized_pnl, order_id, trade_id, client_order_id,
                    grid_id, level_index, source, raw_json, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(trade_key) do update set
                    commission=excluded.commission,
                    commission_asset=excluded.commission_asset,
                    realized_pnl=excluded.realized_pnl,
                    client_order_id=excluded.client_order_id,
                    grid_id=excluded.grid_id,
                    level_index=excluded.level_index,
                    source=excluded.source,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    trade_key,
                    timestamp_ms,
                    timestamp_iso,
                    symbol,
                    side,
                    quantity,
                    price,
                    quantity * price,
                    commission,
                    commission_asset,
                    realized_pnl,
                    order_id,
                    trade_id,
                    client_order_id,
                    grid_id,
                    level_index,
                    source,
                    json.dumps(raw or {}, sort_keys=True),
                    updated_at,
                ),
            )
            return conn.total_changes > before

    def get_summary(self) -> ExchangeJournalSummary:
        return self.get_summary_since("")

    def save_grid_snapshot(self, grid: GridInstance) -> None:
        updated_at = datetime.now(UTC).isoformat()
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                insert into grids (
                    grid_id, symbol, direction, upper_price, lower_price, grid_count,
                    leverage, total_investment, per_level_qty, matched_profit,
                    unrealized_pnl, total_matched, created_at, closed, close_reason, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(grid_id) do update set
                    direction=excluded.direction,
                    upper_price=excluded.upper_price,
                    lower_price=excluded.lower_price,
                    grid_count=excluded.grid_count,
                    leverage=excluded.leverage,
                    total_investment=excluded.total_investment,
                    per_level_qty=excluded.per_level_qty,
                    matched_profit=excluded.matched_profit,
                    unrealized_pnl=excluded.unrealized_pnl,
                    total_matched=excluded.total_matched,
                    closed=excluded.closed,
                    close_reason=excluded.close_reason,
                    updated_at=excluded.updated_at
                """,
                (
                    grid.grid_id,
                    grid.symbol,
                    grid.direction.value,
                    grid.upper_price,
                    grid.lower_price,
                    grid.grid_count,
                    grid.leverage,
                    grid.total_investment,
                    grid.per_level_qty,
                    grid.matched_profit,
                    grid.unrealized_pnl,
                    grid.total_matched,
                    grid.created_at.isoformat(),
                    int(grid.closed),
                    grid.close_reason,
                    updated_at,
                ),
            )
            for level in grid.levels:
                conn.execute(
                    """
                    insert into grid_levels (
                        grid_id, level_index, price, state, buy_order_id, sell_order_id,
                        buy_fill_price, sell_fill_price, quantity, filled_quantity,
                        matched_count, commission, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(grid_id, level_index) do update set
                        price=excluded.price,
                        state=excluded.state,
                        buy_order_id=excluded.buy_order_id,
                        sell_order_id=excluded.sell_order_id,
                        buy_fill_price=excluded.buy_fill_price,
                        sell_fill_price=excluded.sell_fill_price,
                        quantity=excluded.quantity,
                        filled_quantity=excluded.filled_quantity,
                        matched_count=excluded.matched_count,
                        commission=excluded.commission,
                        updated_at=excluded.updated_at
                    """,
                    (
                        grid.grid_id,
                        level.index,
                        level.price,
                        level.state.value,
                        level.buy_order_id,
                        level.sell_order_id,
                        level.buy_fill_price,
                        level.sell_fill_price,
                        level.quantity,
                        level.filled_quantity,
                        level.matched_count,
                        level.commission,
                        updated_at,
                    ),
                )

    def save_order_signal(
        self,
        *,
        client_order_id: str,
        grid_id: str,
        level_index: int,
        symbol: str,
        side: str,
        order_type: str,
        price: float,
        quantity: float,
        reduce_only: bool,
        cancel_order_id: str,
        notional: float,
        status: str,
        exchange_order_id: str = "",
        error_message: str = "",
    ) -> None:
        updated_at = datetime.now(UTC).isoformat()
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                insert into exchange_orders (
                    client_order_id, grid_id, level_index, symbol, side, order_type, price,
                    quantity, reduce_only, cancel_order_id, exchange_order_id,
                    status, notional, error_message, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(client_order_id) do update set
                    grid_id=excluded.grid_id,
                    level_index=excluded.level_index,
                    side=excluded.side,
                    order_type=excluded.order_type,
                    price=excluded.price,
                    quantity=excluded.quantity,
                    reduce_only=excluded.reduce_only,
                    cancel_order_id=excluded.cancel_order_id,
                    exchange_order_id=excluded.exchange_order_id,
                    status=excluded.status,
                    notional=excluded.notional,
                    error_message=excluded.error_message,
                    updated_at=excluded.updated_at
                """,
                (
                    client_order_id,
                    grid_id,
                    level_index,
                    symbol,
                    side,
                    order_type,
                    price,
                    quantity,
                    int(reduce_only),
                    cancel_order_id,
                    exchange_order_id,
                    status,
                    notional,
                    error_message,
                    updated_at,
                ),
            )

    def save_runtime_state(
        self,
        *,
        session_started_at: str,
        safe_mode_reason: str,
        trader_state: dict[str, Any],
        portfolio_state: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        updated_at = datetime.now(UTC).isoformat()
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                insert into runtime_state (
                    state_key, session_started_at, safe_mode_reason,
                    trader_json, portfolio_json, metadata_json, updated_at
                ) values ('jackbot', ?, ?, ?, ?, ?, ?)
                on conflict(state_key) do update set
                    session_started_at=excluded.session_started_at,
                    safe_mode_reason=excluded.safe_mode_reason,
                    trader_json=excluded.trader_json,
                    portfolio_json=excluded.portfolio_json,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    session_started_at,
                    safe_mode_reason,
                    json.dumps(trader_state, sort_keys=True),
                    json.dumps(portfolio_state, sort_keys=True),
                    json.dumps(metadata or {}, sort_keys=True),
                    updated_at,
                ),
            )

    def load_runtime_state(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select session_started_at, safe_mode_reason, trader_json,
                       portfolio_json, metadata_json, updated_at
                from runtime_state
                where state_key = 'jackbot'
                """
            ).fetchone()
        if row is None:
            return None
        return {
            "session_started_at": row["session_started_at"],
            "safe_mode_reason": row["safe_mode_reason"],
            "trader_state": json.loads(row["trader_json"] or "{}"),
            "portfolio_state": json.loads(row["portfolio_json"] or "{}"),
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "updated_at": row["updated_at"],
        }

    def append_reconcile_event(self, report: dict[str, Any]) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                insert into reconcile_events (status, details_json, created_at)
                values (?, ?, ?)
                """,
                (
                    str(report.get("status", "")),
                    json.dumps(report, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def load_active_grids(self) -> list[GridInstance]:
        with self._connect() as conn:
            grid_rows = conn.execute(
                """
                select *
                from grids
                where closed = 0
                order by created_at asc
                """
            ).fetchall()
            level_rows = conn.execute(
                """
                select *
                from grid_levels
                order by grid_id asc, level_index asc
                """
            ).fetchall()
        levels_by_grid: dict[str, list[sqlite3.Row]] = {}
        for row in level_rows:
            levels_by_grid.setdefault(str(row["grid_id"]), []).append(row)

        grids: list[GridInstance] = []
        for row in grid_rows:
            grid = GridInstance(
                grid_id=str(row["grid_id"]),
                symbol=str(row["symbol"]),
                direction=GridDirection(str(row["direction"])),
                upper_price=float(row["upper_price"]),
                lower_price=float(row["lower_price"]),
                grid_count=int(row["grid_count"]),
                leverage=int(row["leverage"]),
                total_investment=float(row["total_investment"]),
                per_level_qty=float(row["per_level_qty"]),
                matched_profit=float(row["matched_profit"]),
                unrealized_pnl=float(row["unrealized_pnl"]),
                total_matched=int(row["total_matched"]),
                created_at=datetime.fromisoformat(str(row["created_at"])),
                closed=bool(row["closed"]),
                close_reason=str(row["close_reason"] or ""),
            )
            for level_row in levels_by_grid.get(grid.grid_id, []):
                grid.levels.append(
                    GridLevel(
                        index=int(level_row["level_index"]),
                        price=float(level_row["price"]),
                        state=GridLevelState(str(level_row["state"])),
                        buy_order_id=str(level_row["buy_order_id"] or ""),
                        sell_order_id=str(level_row["sell_order_id"] or ""),
                        buy_fill_price=float(level_row["buy_fill_price"]),
                        sell_fill_price=float(level_row["sell_fill_price"]),
                        quantity=float(level_row["quantity"]),
                        filled_quantity=float(level_row["filled_quantity"]),
                        matched_count=int(level_row["matched_count"]),
                        commission=float(level_row["commission"]),
                    )
                )
            grids.append(grid)
        return grids

    def fills_since(self, since_iso: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select timestamp_iso as timestamp, symbol, side, quantity, price, commission,
                       commission_asset, realized_pnl, order_id, trade_id,
                       client_order_id, grid_id, level_index, source
                from exchange_fills
                where timestamp_iso >= ?
                order by timestamp_ms asc
                """,
                (since_iso,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_summary_since(self, since_iso: str) -> ExchangeJournalSummary:
        with self._connect() as conn:
            row = conn.execute(
                """
                select count(*) as fills,
                       coalesce(sum(realized_pnl), 0) as realized,
                       coalesce(sum(commission), 0) as commission
                from exchange_fills
                where timestamp_iso >= ?
                """,
                (since_iso,),
            ).fetchone()
            last = conn.execute(
                """
                select timestamp_iso
                from exchange_fills
                where timestamp_iso >= ?
                order by timestamp_ms desc
                limit 1
                """,
                (since_iso,),
            ).fetchone()
        return ExchangeJournalSummary(
            total_fills=int(row["fills"]),
            today_fills=int(row["fills"]),
            today_realized_pnl=round(float(row["realized"]), 8),
            today_commission=round(float(row["commission"]), 8),
            last_fill_at=str(last["timestamp_iso"]) if last else "",
        )

    def recent_fills(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select timestamp_iso as timestamp, symbol, side, quantity, price, commission,
                       commission_asset, realized_pnl, order_id, trade_id,
                       client_order_id, grid_id, level_index, source
                from exchange_fills
                order by timestamp_ms desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _timestamp_ms(value: object) -> int:
        if isinstance(value, datetime):
            return int(value.timestamp() * 1000)
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value or "")
        if not text:
            return 0
        try:
            return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return 0

    @staticmethod
    def _trade_key(
        symbol: str,
        trade_id: str,
        order_id: str,
        timestamp_ms: int,
        side: str,
        quantity: float,
        price: float,
    ) -> str:
        if trade_id:
            return f"{symbol}:trade:{trade_id}"
        return f"{symbol}:fill:{order_id}:{timestamp_ms}:{side}:{quantity:.12g}:{price:.12g}"
