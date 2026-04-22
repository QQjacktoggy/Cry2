"""Tests for the new runtime_events / equity_snapshots tables."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from bot.core.constants import OrderSide
from bot.core.events import SignalEvent
from bot.portfolio.trade_journal import TradeJournal
from bot.runtime.deploy_meta import build_deploy_meta


def _journal(tmp_path: Path) -> TradeJournal:
    meta = build_deploy_meta(config={"environment": "paper"}, version="v74")
    return TradeJournal(tmp_path / "j.db", deploy_meta=meta)


def test_runtime_event_written_and_exported(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    try:
        j.record_runtime_event(
            "signal_rejected",
            severity="warning",
            strategy="v74_trend_donchian_btc",
            symbol="BTCUSDT",
            details={"reason": "regime ranging"},
        )
        df = j.export_runtime_events(event_type="signal_rejected")
        assert len(df) == 1
        row = df.iloc[0]
        assert row["strategy"] == "v74_trend_donchian_btc"
        assert row["severity"] == "warning"
        assert "regime ranging" in row["details"]
        assert row["run_id"]  # deploy meta stamped
    finally:
        j.close()


def test_equity_snapshot_roundtrip(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    try:
        j.record_equity_snapshot(
            equity=150.5,
            realized_pnl=0.5,
            unrealized_pnl=0.0,
            positions=[{"symbol": "BTCUSDT", "qty": 0.001}],
        )
        df = j.export_equity_snapshots()
        assert len(df) == 1
        assert df.iloc[0]["equity"] == 150.5
        assert "BTCUSDT" in df.iloc[0]["positions"]
    finally:
        j.close()


def test_signal_generated_event_captures_leverage_context(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    try:
        signal = SignalEvent(
            timestamp=datetime.now(UTC),
            source="test",
            strategy_name="v74_trend_donchian_btc",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.01,
            reason="breakout",
            metadata={"requested_leverage": 3.0, "source_tf": "4h"},
        )
        j.record_signal_generated(signal, effective_max_leverage=1.5)
        df = j.export_runtime_events(event_type="signal_generated")
        assert len(df) == 1
        details = json.loads(df.iloc[0]["details"])
        assert details["requested_leverage"] == 3.0
        assert details["effective_max_leverage"] == 1.5
        assert details["applied_leverage"] == 1.5
        assert details["metadata"]["source_tf"] == "4h"
    finally:
        j.close()


def test_migration_adds_deploy_meta_columns_to_legacy_db(tmp_path: Path) -> None:
    """An existing schema-v1 DB must survive opening under the new schema."""
    import sqlite3

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            commission REAL NOT NULL DEFAULT 0,
            comm_asset TEXT NOT NULL DEFAULT 'USDT',
            order_id TEXT NOT NULL DEFAULT '',
            client_oid TEXT NOT NULL DEFAULT '',
            realized_pnl REAL NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'live'
        );
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_time TEXT,
            exit_price REAL,
            size REAL NOT NULL,
            pnl REAL NOT NULL DEFAULT 0,
            fees REAL NOT NULL DEFAULT 0,
            holding_bars INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'live',
            entry_fill_id INTEGER,
            exit_fill_id INTEGER
        );
        CREATE TABLE journal_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO journal_meta (key, value) VALUES ('schema_version', '1');
        """
    )
    conn.commit()
    conn.close()

    j = TradeJournal(db)
    try:
        cols = {row[1] for row in j._conn.execute("PRAGMA table_info(fills)")}
        assert "run_id" in cols
        assert "config_fingerprint" in cols
        tcols = {row[1] for row in j._conn.execute("PRAGMA table_info(trades)")}
        assert "run_id" in tcols
    finally:
        j.close()
