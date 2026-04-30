"""Regression tests for backtest accounting and close fee assumptions."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from jackbot.core.constants import GridDirection, GridLevelState
from jackbot.core.events import FillEvent, GridProfitEvent, MarketEvent
from jackbot.strategy.day_trader import DayTraderConfig
from jackbot.strategy.grid_engine import GridInstance, GridLevel
from scripts.backtest import BacktestEngine, FillSimulator


def _make_engine() -> BacktestEngine:
    config = DayTraderConfig(symbols=["ETHUSDC"], total_capital_usd=150.0, warmup_bars=1)
    return BacktestEngine(config, maker_rate=0.001, taker_rate=0.002)


def test_grid_strategy_pnl_tracks_fill_fees_and_matched_profit() -> None:
    engine = _make_engine()
    fill = FillEvent(
        timestamp=datetime(2026, 4, 30, 0, 0, tzinfo=UTC),
        symbol="ETHUSDC",
        side="BUY",
        quantity=1.0,
        price=100.0,
        commission=0.1,
        grid_id="grid-1",
        level_index=0,
    )
    engine._record_grid_fill_commission(fill)
    engine._on_profit(GridProfitEvent(
        timestamp=fill.timestamp,
        symbol="ETHUSDC",
        grid_id="grid-1",
        level_index=0,
        buy_price=100.0,
        sell_price=110.0,
        quantity=1.0,
        profit_usd=10.0,
        commission=0.1,
    ))

    results = engine._compile_results()

    assert results["pnl"]["net_profit"] == 9.9
    assert results["strategy_pnl"]["grid_pnl"] == 9.9
    assert results["strategy_pnl"]["trend_pnl"] == 0.0


def test_breakout_close_uses_taker_fee_assumption() -> None:
    sim = FillSimulator(maker_rate=0.001, taker_rate=0.002)
    grid = GridInstance(
        grid_id="grid-1",
        symbol="ETHUSDC",
        direction=GridDirection.LONG,
        upper_price=110.0,
        lower_price=90.0,
        grid_count=1,
        leverage=1,
        total_investment=100.0,
        per_level_qty=1.0,
        levels=[
            GridLevel(
                index=0,
                price=100.0,
                state=GridLevelState.FILLED_BUY,
                buy_fill_price=100.0,
                quantity=1.0,
            )
        ],
        created_at=datetime(2026, 4, 30, 0, 0, tzinfo=UTC),
    )
    bar = MarketEvent(
        timestamp=datetime(2026, 4, 30, 0, 5, tzinfo=UTC),
        symbol="ETHUSDC",
        timeframe="5m",
        open=95.0,
        high=96.0,
        low=94.0,
        close=95.0,
        volume=1000.0,
    )

    _, breakdown = sim.realize_close(grid, bar, "breakout")

    assert breakdown["maker_fee"] == 0.0
    assert breakdown["taker_fee"] > 0.0
