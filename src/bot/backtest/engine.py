"""Event-driven backtest engine.

Core loop:
1. DataFeed produces MarketEvent/FundingEvent in time order
2. Strategies consume events and produce SignalEvents
3. RiskManager checks signals and produces OrderEvents
4. SimExecutor fills orders and produces FillEvents
5. Portfolio tracks state

Key rule: Signals generated at bar close, execution at NEXT bar open.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from bot.core.clock import SimClock
from bot.core.constants import EventType
from bot.core.event_bus import EventBus
from bot.core.events import FillEvent, FundingEvent, MarketEvent, OrderEvent, SignalEvent
from bot.core.constants import PositionSide
from bot.data.feed_backtest import BacktestFeed
from bot.data.storage import ParquetStorage
from bot.execution.executor_sim import SimExecutor
from bot.execution.fee_model import FeeModel
from bot.execution.funding_model import FundingModel
from bot.execution.slippage import SlippageModel
from bot.portfolio.portfolio import Portfolio
from bot.risk.circuit_breaker import CircuitBreaker
from bot.risk.risk_manager import RiskManager
from bot.utils.id_generator import generate_run_id

logger = structlog.get_logger(__name__)


class BacktestEngine:
    """Event-driven backtest engine.

    Orchestrates the full backtest pipeline:
    DataFeed → Strategy → Risk → Execution → Portfolio
    """

    def __init__(
        self,
        strategies: list[Any],
        symbols: list[str],
        timeframe: str = "",
        start_ms: int = 0,
        end_ms: int = 0,
        initial_capital: float = 10000.0,
        storage: ParquetStorage | None = None,
        slippage_model: SlippageModel | None = None,
        fee_model: FeeModel | None = None,
        risk_config: dict[str, Any] | None = None,
        circuit_breaker_config: dict[str, Any] | None = None,
        timeframes: list[str] | None = None,
    ) -> None:
        self.run_id = generate_run_id()
        self.symbols = symbols
        # Derive all unique timeframes from strategies if not explicitly provided
        if timeframes:
            self.timeframes = list(set(timeframes))
        elif timeframe:
            self.timeframes = [timeframe]
        else:
            self.timeframes = list(set(s.timeframe for s in strategies)) or ["4h"]
        self.timeframe = self.timeframes[0]  # backward compat
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.initial_capital = initial_capital

        # Core components
        self.clock = SimClock(start_ms)
        self.event_bus = EventBus()
        self.storage = storage or ParquetStorage()

        # Execution
        self.executor = SimExecutor(
            event_bus=self.event_bus,
            initial_capital=initial_capital,
            slippage_model=slippage_model or SlippageModel(),
            fee_model=fee_model or FeeModel(),
            funding_model=FundingModel(),
            clock=self.clock,
        )

        # Portfolio
        self.portfolio = Portfolio(
            event_bus=self.event_bus,
            initial_capital=initial_capital,
        )

        # Risk
        risk_cfg = risk_config or {}
        self.risk_manager = RiskManager(
            event_bus=self.event_bus,
            clock=self.clock,
            **risk_cfg,
        )

        cb_cfg = circuit_breaker_config or {}
        self.circuit_breaker = CircuitBreaker(**cb_cfg)

        # Data feed
        self.feed = BacktestFeed(
            event_bus=self.event_bus,
            clock=self.clock,
            storage=self.storage,
            symbols=symbols,
            timeframes=self.timeframes,
            start_ms=start_ms,
            end_ms=end_ms,
        )

        # Strategies
        self.strategies = strategies

        # Subscribe executor to orders
        self.event_bus.subscribe(EventType.ORDER.value, self._on_order)

        # Route fills back to strategies so they can track their positions
        self.event_bus.subscribe(EventType.FILL.value, self._on_fill_for_strategies)

        # Track pending signals (fill at next bar open)
        self._pending_signals: list[SignalEvent] = []
        self._last_bar_open: dict[str, float] = {}
        self._last_bar_volume: dict[str, float] = {}

        # Results
        self._equity_curve: list[tuple[int, float]] = []

    def _on_order(self, event: OrderEvent) -> None:
        """Handle order events - send to executor."""
        self.executor.submit_order(event)

    def _on_fill_for_strategies(self, event: FillEvent) -> None:
        """Route fill events to the originating strategy for position tracking."""
        for strategy in self.strategies:
            if strategy.name == event.strategy_name:
                strategy.on_fill(event)
                break

    def run(self) -> dict[str, Any]:
        """Run the backtest.

        Returns:
            Dictionary with results including equity curve, fills, metrics.
        """
        start_time = time.time()
        logger.info(
            "backtest_starting",
            run_id=self.run_id,
            symbols=self.symbols,
            timeframe=self.timeframe,
            initial_capital=self.initial_capital,
        )

        self.feed.start()

        bar_count = 0
        while self.feed.has_next():
            event = self.feed.next()
            if event is None:
                continue

            if isinstance(event, MarketEvent):
                # Record bar open/volume for next-bar execution
                self._last_bar_open[event.symbol] = event.open
                self._last_bar_volume[event.symbol] = event.volume

                # Set executor bar data (signals from previous bar fill at this open)
                self.executor.set_bar_data(event.symbol, event.open, event.volume)

                # Process any pending signals at this bar's open
                self._process_pending_signals()

                # Check circuit breaker
                if self.circuit_breaker.is_tripped(event.timestamp):
                    continue

                if not self.circuit_breaker.check_bar(event):
                    continue

                # Update risk manager equity
                self.risk_manager.set_equity(self.portfolio.equity)

                # Feed event to strategies
                for strategy in self.strategies:
                    if event.symbol in strategy.symbols and event.timeframe == strategy.timeframe:
                        strategy.set_equity(self.portfolio.equity)
                        signals = strategy.on_bar(event)
                        if signals:
                            self._pending_signals.extend(signals)

                # Update prices for equity calculation
                self.executor.update_prices({event.symbol: event.close})

                # Liquidation check: if equity <= 0, force close all positions
                current_equity = self.portfolio.equity
                if current_equity <= 0:
                    logger.error(
                        "liquidation_triggered",
                        equity=current_equity,
                        symbol=event.symbol,
                    )
                    self._force_close_all(event)

                # Record equity
                self._equity_curve.append((self.clock.now_ms(), self.portfolio.equity))
                bar_count += 1

            elif isinstance(event, FundingEvent):
                # Process funding event
                for strategy in self.strategies:
                    strategy.on_funding(event)

        self.feed.stop()

        elapsed = time.time() - start_time
        logger.info(
            "backtest_complete",
            run_id=self.run_id,
            bars=bar_count,
            fills=len(self.executor.fills),
            final_equity=self.portfolio.equity,
            total_return=f"{self.portfolio.total_return:.2%}",
            elapsed_sec=f"{elapsed:.1f}",
        )

        return self._build_results()

    def _process_pending_signals(self) -> None:
        """Process pending signals from previous bar."""
        for signal in self._pending_signals:
            # Publish signal through event bus (risk manager will check)
            self.event_bus.publish(signal)
        self._pending_signals.clear()

    def _force_close_all(self, event: MarketEvent) -> None:
        """Force-close all open positions (simulated liquidation)."""
        from bot.core.constants import OrderSide, OrderType
        for (strategy, symbol), pos in list(self.executor.positions.items()):
            if not pos.is_open:
                continue
            close_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            order = OrderEvent(
                timestamp=event.timestamp,
                strategy_name=strategy,
                symbol=symbol,
                side=close_side,
                order_type=OrderType.MARKET,
                quantity=pos.quantity,
                reduce_only=True,
                source="liquidation",
            )
            self.executor.submit_order(order)
        logger.warning("all_positions_liquidated")

    def _build_results(self) -> dict[str, Any]:
        """Build results dictionary."""
        return {
            "run_id": self.run_id,
            "symbols": self.symbols,
            "timeframe": self.timeframe,
            "initial_capital": self.initial_capital,
            "final_equity": self.portfolio.equity,
            "total_return": self.portfolio.total_return,
            "equity_curve": self._equity_curve,
            "fills": self.executor.fills,
            "positions": self.executor.positions,
            "bar_count": len(self._equity_curve),
        }
