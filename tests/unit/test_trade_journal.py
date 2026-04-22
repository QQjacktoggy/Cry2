"""Tests for TradeJournal — persistent trade recording and export."""

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

# Ensure src is on path (conftest.py already does this, but be explicit)
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from bot.core.constants import EventType, OrderSide
from bot.core.event_bus import EventBus
from bot.core.events import FillEvent
from bot.portfolio.trade_journal import TradeJournal


@pytest.fixture
def db_path(tmp_path):
    """Temporary database path."""
    return tmp_path / "test_trades.db"


@pytest.fixture
def journal(db_path):
    """Fresh TradeJournal for each test."""
    j = TradeJournal(db_path)
    yield j
    j.close()


def _make_fill(
    strategy: str = "trend",
    symbol: str = "BTCUSDT",
    side: OrderSide = OrderSide.BUY,
    qty: float = 0.01,
    price: float = 50000.0,
    commission: float = 0.5,
    ts: datetime | None = None,
    source: str = "test",
) -> FillEvent:
    return FillEvent(
        timestamp=ts or datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        fill_id="FILL-001",
        strategy_name=strategy,
        symbol=symbol,
        side=side,
        quantity=qty,
        price=price,
        commission=commission,
        order_id="ORD-001",
        client_order_id="CLI-001",
        source=source,
    )


class TestRecordAndExport:
    """Fill recording and DataFrame export."""

    def test_record_single_fill(self, journal):
        fill = _make_fill()
        fill_id = journal.record_fill(fill)
        assert fill_id >= 1

        df = journal.export_fills()
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "BTCUSDT"
        assert df.iloc[0]["side"] == "BUY"
        assert df.iloc[0]["price"] == 50000.0

    def test_record_multiple_fills(self, journal):
        for i in range(5):
            journal.record_fill(_make_fill(price=50000.0 + i * 100))
        df = journal.export_fills()
        assert len(df) == 5

    def test_export_fills_filters(self, journal):
        journal.record_fill(_make_fill(strategy="trend", symbol="BTCUSDT"))
        journal.record_fill(_make_fill(strategy="mean_rev", symbol="ETHUSDT"))
        journal.record_fill(_make_fill(strategy="trend", symbol="ETHUSDT"))

        assert len(journal.export_fills(strategy="trend")) == 2
        assert len(journal.export_fills(symbol="ETHUSDT")) == 2
        assert len(journal.export_fills(strategy="trend", symbol="ETHUSDT")) == 1


class TestTradesPairing:
    """Round-trip trade pairing from fills."""

    def test_long_round_trip(self, journal):
        # Open long
        journal.record_fill(_make_fill(
            side=OrderSide.BUY, price=50000.0,
            ts=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        ))
        # Close long
        journal.record_fill(_make_fill(
            side=OrderSide.SELL, price=51000.0,
            ts=datetime(2024, 6, 2, 12, 0, 0, tzinfo=UTC),
        ))

        trades = journal.export_trades()
        assert len(trades) == 1
        t = trades.iloc[0]
        assert t["Direction"] == "Long"
        assert t["Entry Price"] == 50000.0
        assert t["Exit Price"] == 51000.0
        # PnL = (51000-50000)*0.01 - (0.5+0.5) = 10 - 1 = 9
        assert t["PnL"] == pytest.approx(9.0, abs=0.01)

    def test_short_round_trip(self, journal):
        journal.record_fill(_make_fill(
            side=OrderSide.SELL, price=50000.0,
            ts=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        ))
        journal.record_fill(_make_fill(
            side=OrderSide.BUY, price=49000.0,
            ts=datetime(2024, 6, 2, 12, 0, 0, tzinfo=UTC),
        ))

        trades = journal.export_trades()
        assert len(trades) == 1
        assert trades.iloc[0]["Direction"] == "Short"
        # PnL = (50000-49000)*0.01 - 1.0 = 10 - 1 = 9
        assert trades.iloc[0]["PnL"] == pytest.approx(9.0, abs=0.01)

    def test_open_trade_not_in_closed_export(self, journal):
        journal.record_fill(_make_fill(side=OrderSide.BUY))
        # Not closed yet
        trades_closed = journal.export_trades(closed_only=True)
        trades_all = journal.export_trades(closed_only=False)
        assert len(trades_closed) == 0
        assert len(trades_all) == 1

    def test_multiple_round_trips(self, journal):
        for i in range(3):
            journal.record_fill(_make_fill(
                side=OrderSide.BUY, price=50000.0 + i * 1000,
                ts=datetime(2024, 6, 1 + i * 2, 12, 0, 0, tzinfo=UTC),
            ))
            journal.record_fill(_make_fill(
                side=OrderSide.SELL, price=51000.0 + i * 1000,
                ts=datetime(2024, 6, 2 + i * 2, 12, 0, 0, tzinfo=UTC),
            ))
        trades = journal.export_trades()
        assert len(trades) == 3


class TestEventBusIntegration:
    """EventBus auto-capture."""

    def test_attach_captures_fills(self, journal):
        bus = EventBus()
        journal.attach(bus)

        fill = _make_fill()
        bus.publish(fill)

        df = journal.export_fills()
        assert len(df) == 1

    def test_attach_pairs_trades(self, journal):
        bus = EventBus()
        journal.attach(bus)

        bus.publish(_make_fill(
            side=OrderSide.BUY, price=50000.0,
            ts=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        ))
        bus.publish(_make_fill(
            side=OrderSide.SELL, price=51000.0,
            ts=datetime(2024, 6, 2, 12, 0, 0, tzinfo=UTC),
        ))

        trades = journal.export_trades()
        assert len(trades) == 1
        assert trades.iloc[0]["PnL"] > 0


class TestSummary:
    """Summary statistics."""

    def test_summary_with_trades(self, journal):
        journal.record_fill(_make_fill(side=OrderSide.BUY, price=50000.0,
            ts=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)))
        journal.record_fill(_make_fill(side=OrderSide.SELL, price=51000.0,
            ts=datetime(2024, 6, 2, 12, 0, 0, tzinfo=UTC)))

        s = journal.summary()
        assert s["total_fills"] == 2
        assert s["total_trades"] == 1
        assert s["win_rate"] == 1.0
        assert s["net_pnl"] > 0

    def test_summary_empty(self, journal):
        s = journal.summary()
        assert s["total_fills"] == 0
        assert s["total_trades"] == 0


class TestPersistence:
    """Data survives close/reopen."""

    def test_reopen_preserves_data(self, db_path):
        j1 = TradeJournal(db_path)
        j1.record_fill(_make_fill())
        j1.close()

        j2 = TradeJournal(db_path)
        df = j2.export_fills()
        assert len(df) == 1
        j2.close()


def _make_binance_fill(
    exchange_fill_id: str = "TRADE-001",
    order_id: str = "BINANCE-001",
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    qty: float = 0.01,
    price: float = 50000.0,
    strategy: str = "trend",
    ts: datetime | None = None,
) -> dict:
    """Simulate the dict format produced by _reconcile_from_binance()."""
    return {
        "timestamp": ts or datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        "strategy": strategy,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "commission": 0.5,
        "comm_asset": "USDT",
        "exchange_fill_id": exchange_fill_id,
        "order_id": order_id,
        "client_oid": "",
        "realized_pnl": 0.0,
        "source": "reconcile",
    }


class TestReconcile:
    """TradeJournal.reconcile() — Binance REST back-fill on reconnect."""

    def test_reconcile_inserts_new_fills(self, journal):
        fills = [
            _make_binance_fill(exchange_fill_id="TRADE-A", order_id="ORD-A"),
            _make_binance_fill(
                exchange_fill_id="TRADE-B",
                order_id="ORD-B",
                side="SELL",
                price=51000.0,
            ),
        ]
        inserted = journal.reconcile(fills)
        assert inserted == 2
        assert len(journal.export_fills()) == 2

    def test_reconcile_dedup_skips_existing_exchange_fill_id(self, journal):
        # Pre-insert a fill with the same exchange fill id
        journal.record_fill(_make_fill(symbol="BTCUSDT"))  # fill_id = "FILL-001"

        fills = [
            _make_binance_fill(exchange_fill_id="FILL-001", order_id="ORD-001"),   # already in DB → skip
            _make_binance_fill(exchange_fill_id="FILL-NEW", order_id="ORD-NEW"),   # new → insert
        ]
        inserted = journal.reconcile(fills)
        assert inserted == 1
        # Total fills = 1 (pre-existing) + 1 (new)
        assert len(journal.export_fills()) == 2

    def test_reconcile_dedup_skips_legacy_rows_without_exchange_fill_id(self, journal):
        journal.record_fill(_make_fill(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            qty=0.25,
            price=50500.0,
            ts=datetime(2024, 6, 1, 12, 15, 0, tzinfo=UTC),
        ))
        journal._conn.execute("UPDATE fills SET exchange_fill_id = ''")  # emulate pre-migration row
        journal._conn.commit()

        fills = [
            _make_binance_fill(
                exchange_fill_id="TRADE-LEGACY",
                order_id="ORD-001",
                symbol="BTCUSDT",
                side="BUY",
                qty=0.25,
                price=50500.0,
                ts=datetime(2024, 6, 1, 12, 15, 0, tzinfo=UTC),
            )
        ]
        inserted = journal.reconcile(fills)
        assert inserted == 0
        assert len(journal.export_fills()) == 1

    def test_same_side_partial_fills_aggregate_before_close(self, journal):
        journal.record_fill(_make_fill(
            side=OrderSide.BUY,
            qty=0.1,
            price=100.0,
            commission=0.1,
            ts=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        ))
        journal.record_fill(_make_fill(
            side=OrderSide.BUY,
            qty=0.2,
            price=101.0,
            commission=0.2,
            ts=datetime(2024, 6, 1, 12, 5, 0, tzinfo=UTC),
        ))
        journal.record_fill(_make_fill(
            side=OrderSide.SELL,
            qty=0.3,
            price=110.0,
            commission=0.3,
            ts=datetime(2024, 6, 1, 13, 0, 0, tzinfo=UTC),
        ))

        trades = journal.export_trades(closed_only=False)
        assert len(trades) == 1
        trade = trades.iloc[0]
        assert trade["Direction"] == "Long"
        assert trade["Size"] == pytest.approx(0.3)
        assert trade["Exit Price"] == 110.0

    def test_partial_exits_keep_original_size_and_weighted_exit_price(self, journal):
        journal.record_fill(_make_fill(
            side=OrderSide.BUY,
            qty=0.3,
            price=100.0,
            commission=0.1,
            ts=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        ))
        journal.record_fill(_make_fill(
            side=OrderSide.SELL,
            qty=0.1,
            price=110.0,
            commission=0.1,
            ts=datetime(2024, 6, 1, 12, 30, 0, tzinfo=UTC),
        ))
        journal.record_fill(_make_fill(
            side=OrderSide.SELL,
            qty=0.2,
            price=120.0,
            commission=0.1,
            ts=datetime(2024, 6, 1, 13, 0, 0, tzinfo=UTC),
        ))

        trades = journal.export_trades()
        assert len(trades) == 1
        trade = trades.iloc[0]
        assert trade["Size"] == pytest.approx(0.3)
        assert trade["Exit Price"] == pytest.approx((0.1 * 110.0 + 0.2 * 120.0) / 0.3)

    def test_overfill_reversal_opens_new_trade_for_residual_quantity(self, journal):
        journal.record_fill(_make_fill(
            side=OrderSide.BUY,
            qty=1.0,
            price=100.0,
            commission=0.0,
            ts=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        ))
        journal.record_fill(_make_fill(
            side=OrderSide.SELL,
            qty=1.5,
            price=110.0,
            commission=0.0,
            ts=datetime(2024, 6, 1, 13, 0, 0, tzinfo=UTC),
        ))

        closed = journal.export_trades()
        assert len(closed) == 1
        assert closed.iloc[0]["Direction"] == "Long"
        assert closed.iloc[0]["Size"] == pytest.approx(1.0)
        assert closed.iloc[0]["Exit Price"] == pytest.approx(110.0)

        open_trades = journal.open_trades()
        assert len(open_trades) == 1
        assert open_trades.iloc[0]["direction"] == "Short"
        assert open_trades.iloc[0]["size"] == pytest.approx(0.5)
        assert open_trades.iloc[0]["remaining_size"] == pytest.approx(0.5)

    def test_reconcile_empty_list_returns_zero(self, journal):
        inserted = journal.reconcile([])
        assert inserted == 0
        assert len(journal.export_fills()) == 0

    def test_open_trades_returns_only_unclosed(self, journal):
        # Open a long, then close it
        journal.record_fill(_make_fill(
            side=OrderSide.BUY, symbol="BTCUSDT",
            ts=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        ))
        journal.record_fill(_make_fill(
            side=OrderSide.SELL, symbol="BTCUSDT",
            ts=datetime(2024, 6, 2, 12, 0, 0, tzinfo=UTC),
        ))
        # Open a second position that is NOT closed
        journal.record_fill(_make_fill(
            side=OrderSide.BUY, symbol="ETHUSDT", strategy="eth_strat",
            ts=datetime(2024, 6, 3, 12, 0, 0, tzinfo=UTC),
        ))

        open_df = journal.open_trades()
        assert len(open_df) == 1
        assert open_df.iloc[0]["symbol"] == "ETHUSDT"
