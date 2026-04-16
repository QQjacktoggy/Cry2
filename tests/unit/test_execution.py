"""Tests for execution layer."""

from datetime import datetime, timezone

from bot.core.constants import OrderSide, OrderType, PositionSide, EventType
from bot.core.events import FillEvent, OrderEvent
from bot.execution.fee_model import FeeModel
from bot.execution.slippage import SlippageModel
from bot.execution.funding_model import FundingModel


class TestFeeModel:
    def test_taker_fee(self):
        fm = FeeModel(maker_rate=0.0002, taker_rate=0.0004)
        fee = fm.calculate(10000.0, OrderType.MARKET)
        assert abs(fee - 4.0) < 0.001

    def test_maker_fee(self):
        fm = FeeModel(maker_rate=0.0002, taker_rate=0.0004)
        fee = fm.calculate(10000.0, OrderType.LIMIT)
        assert abs(fee - 2.0) < 0.001


class TestSlippageModel:
    def test_fixed_buy_slippage(self):
        sm = SlippageModel(model_type="fixed_bps", fixed_bps=2.0)
        price = sm.calculate(42000.0, OrderSide.BUY)
        assert price > 42000.0  # Buy pays more
        expected = 42000.0 * 1.0002
        assert abs(price - expected) < 0.01

    def test_fixed_sell_slippage(self):
        sm = SlippageModel(model_type="fixed_bps", fixed_bps=2.0)
        price = sm.calculate(42000.0, OrderSide.SELL)
        assert price < 42000.0  # Sell receives less


class TestFundingModel:
    def test_long_positive_rate(self):
        fm = FundingModel()
        payment = fm.calculate(PositionSide.LONG, 10000.0, 0.0001)
        assert payment < 0  # Long pays when rate is positive

    def test_short_positive_rate(self):
        fm = FundingModel()
        payment = fm.calculate(PositionSide.SHORT, 10000.0, 0.0001)
        assert payment > 0  # Short receives when rate is positive

    def test_flat_position(self):
        fm = FundingModel()
        payment = fm.calculate(PositionSide.FLAT, 10000.0, 0.0001)
        assert payment == 0.0


class TestSimExecutor:
    def test_market_buy(self, sim_executor, event_bus):
        fills = []
        event_bus.subscribe(EventType.FILL.value, lambda e: fills.append(e))

        sim_executor.set_bar_data("BTCUSDT", 42000.0, 1000.0)

        order = OrderEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            strategy_name="test",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            source="test",
        )
        sim_executor.submit_order(order)

        assert len(fills) == 1
        assert fills[0].symbol == "BTCUSDT"
        assert fills[0].quantity == 0.1
        assert fills[0].price > 0

    def test_balance_decreases_with_fees(self, sim_executor, event_bus):
        initial = sim_executor.get_balance()
        sim_executor.set_bar_data("BTCUSDT", 42000.0, 1000.0)

        order = OrderEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            strategy_name="test",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            source="test",
        )
        sim_executor.submit_order(order)

        # Balance should decrease by fee
        assert sim_executor.get_balance() < initial
