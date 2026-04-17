"""Shared test fixtures."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bot.core.clock import RealClock, SimClock
from bot.core.event_bus import EventBus
from bot.core.events import MarketEvent
from bot.core.types import SymbolInfo
from bot.execution.executor_sim import SimExecutor
from bot.execution.fee_model import FeeModel
from bot.execution.slippage import SlippageModel
from bot.portfolio.portfolio import Portfolio
from bot.risk.risk_manager import RiskManager


@pytest.fixture
def event_bus():
    """Fresh event bus for each test."""
    return EventBus()


@pytest.fixture
def sim_clock():
    """SimClock starting at 2024-01-01."""
    return SimClock(start_ms=1704067200000)  # 2024-01-01 00:00:00 UTC


@pytest.fixture
def real_clock():
    """RealClock instance."""
    return RealClock()


@pytest.fixture
def fee_model():
    """Standard fee model."""
    return FeeModel(maker_rate=0.0002, taker_rate=0.0004)


@pytest.fixture
def slippage_model():
    """Fixed 2 bps slippage."""
    return SlippageModel(model_type="fixed_bps", fixed_bps=2.0)


@pytest.fixture
def sim_executor(event_bus, slippage_model, fee_model):
    """Simulated executor with 10k capital."""
    return SimExecutor(
        event_bus=event_bus,
        initial_capital=10000.0,
        slippage_model=slippage_model,
        fee_model=fee_model,
    )


@pytest.fixture
def portfolio(event_bus):
    """Portfolio with 10k capital."""
    return Portfolio(event_bus=event_bus, initial_capital=10000.0)


@pytest.fixture
def risk_manager(event_bus):
    """Risk manager with default limits."""
    return RiskManager(event_bus=event_bus)


@pytest.fixture
def sample_market_event():
    """Sample BTC market event."""
    return MarketEvent(
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        symbol="BTCUSDT",
        timeframe="4h",
        open=42000.0,
        high=42500.0,
        low=41800.0,
        close=42200.0,
        volume=1000.0,
        source="test",
    )


@pytest.fixture
def btc_symbol_info():
    """BTC contract spec."""
    return SymbolInfo(
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        price_precision=2,
        quantity_precision=3,
        tick_size=0.10,
        step_size=0.001,
        min_quantity=0.001,
        min_notional=5.0,
        max_leverage=125,
    )
