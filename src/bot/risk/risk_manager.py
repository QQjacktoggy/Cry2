"""Central risk manager - checks all risk rules before allowing orders.

Multi-layer risk checks:
- Per-trade risk limit
- Daily loss limit → halt for day
- Weekly loss limit → halt for week
- Leverage limit
- Position value limit
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from bot.core.clock import BaseClock, RealClock
from bot.core.constants import EventType
from bot.core.event_bus import EventBus
from bot.core.events import FillEvent, OrderEvent, RejectEvent, SignalEvent
from bot.risk.regime_detector import Regime, RegimeDetector

logger = structlog.get_logger(__name__)


class RiskManager:
    """Central risk manager that gates all orders."""

    def __init__(
        self,
        event_bus: EventBus,
        max_risk_per_trade_pct: float = 1.0,
        max_position_value_pct: float = 20.0,
        max_leverage: int = 3,
        daily_loss_limit_pct: float = 3.0,
        weekly_loss_limit_pct: float = 8.0,
        daily_trade_count_limit: int = 50,
        max_drawdown_pct: float = 20.0,
        drawdown_cooldown_bars: int = 48,
        max_consecutive_losses: int = 5,
        loss_cooldown_bars: int = 24,
        # D2 Regime detection
        regime_adx_period: int = 14,
        regime_trending_threshold: float = 25.0,
        regime_ranging_threshold: float = 20.0,
        block_trend_in_ranging: bool = False,
        # D3 Volatility-adaptive leverage
        atr_adaptive_leverage: bool = False,
        atr_window: int = 100,
        atr_high_pct: float = 80.0,
        atr_low_pct: float = 20.0,
        atr_high_leverage: float = 1.0,
        atr_low_leverage: float = 3.0,
        clock: BaseClock | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.max_position_value_pct = max_position_value_pct
        self.max_leverage = max_leverage
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.weekly_loss_limit_pct = weekly_loss_limit_pct
        self.daily_trade_count_limit = daily_trade_count_limit
        self.max_drawdown_pct = max_drawdown_pct
        self.drawdown_cooldown_bars = drawdown_cooldown_bars
        self.max_consecutive_losses = max_consecutive_losses
        self.loss_cooldown_bars = loss_cooldown_bars
        self._clock = clock or RealClock()

        # State tracking
        self._daily_pnl: float = 0.0
        self._weekly_pnl: float = 0.0
        self._daily_trade_count: int = 0
        self._daily_halted: bool = False
        self._weekly_halted: bool = False
        self._last_daily_reset: datetime = self._clock.now()
        self._last_weekly_reset: datetime = self._clock.now()
        self._equity: float = 0.0

        # Portfolio drawdown tracking
        self._peak_equity: float = 0.0
        self._drawdown_halted: bool = False
        self._drawdown_halt_bars_remaining: int = 0

        # Consecutive loss tracking (per strategy)
        self._consecutive_losses: dict[str, int] = {}
        self._strategy_cooldown: dict[str, int] = {}

        # D2 Regime detection
        self.block_trend_in_ranging = block_trend_in_ranging
        self._regime_detector = RegimeDetector(
            period=regime_adx_period,
            trending_threshold=regime_trending_threshold,
            ranging_threshold=regime_ranging_threshold,
        )

        # D3 Volatility-adaptive leverage
        self.atr_adaptive_leverage = atr_adaptive_leverage
        self._atr_window = atr_window
        self._atr_high_pct = atr_high_pct
        self._atr_low_pct = atr_low_pct
        self._atr_high_leverage = atr_high_leverage
        self._atr_low_leverage = atr_low_leverage
        self._atr_history: list[float] = []
        self._effective_max_leverage: float = float(max_leverage)

        # Subscribe to events
        self.event_bus.subscribe(EventType.SIGNAL.value, self._on_signal)
        self.event_bus.subscribe(EventType.FILL.value, self._on_fill)

    def set_equity(self, equity: float) -> None:
        """Update current equity for risk calculations."""
        self._equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

    def update_market_data(self, high: float, low: float, close: float, atr: float = 0.0) -> None:
        """Feed latest bar data for regime detection and adaptive leverage.

        Call this once per bar (e.g. in the on_market handler) before
        check_signal().
        """
        # D2: update ADX-based regime
        self._regime_detector.update(high, low, close)

        # D3: update ATR history and recompute effective leverage
        if self.atr_adaptive_leverage and atr > 0:
            self._atr_history.append(atr)
            if len(self._atr_history) > self._atr_window:
                self._atr_history.pop(0)
            if len(self._atr_history) >= 10:
                sorted_atrs = sorted(self._atr_history)
                n = len(sorted_atrs)
                pct_rank = (
                    sorted_atrs.index(
                        min(sorted_atrs, key=lambda x: abs(x - atr))
                    )
                    / n
                    * 100
                )
                if pct_rank >= self._atr_high_pct:
                    self._effective_max_leverage = self._atr_high_leverage
                    logger.debug("atr_high_vol_leverage",
                                 pct_rank=round(pct_rank, 1),
                                 leverage=self._atr_high_leverage)
                elif pct_rank <= self._atr_low_pct:
                    self._effective_max_leverage = self._atr_low_leverage
                    logger.debug("atr_low_vol_leverage",
                                 pct_rank=round(pct_rank, 1),
                                 leverage=self._atr_low_leverage)
                else:
                    self._effective_max_leverage = float(self.max_leverage)

    @property
    def regime(self) -> Regime:
        """Current market regime from ADX detector."""
        return self._regime_detector.regime

    @property
    def effective_max_leverage(self) -> float:
        """Current max leverage after ATR-adaptive adjustment."""
        return self._effective_max_leverage

    def _check_daily_reset(self) -> None:
        """Reset daily counters at UTC midnight."""
        now = self._clock.now()
        if now.date() > self._last_daily_reset.date():
            self._daily_pnl = 0.0
            self._daily_trade_count = 0
            self._daily_halted = False
            self._last_daily_reset = now
            logger.info("daily_risk_reset")

    def _check_weekly_reset(self) -> None:
        """Reset weekly counters on Monday."""
        now = self._clock.now()
        days_since_reset = (now - self._last_weekly_reset).days
        if days_since_reset >= 7:
            self._weekly_pnl = 0.0
            self._weekly_halted = False
            self._last_weekly_reset = now
            logger.info("weekly_risk_reset")

    def check_signal(self, signal: SignalEvent, equity: float) -> tuple[bool, str]:
        """Check if a signal passes all risk checks.

        Args:
            signal: The trading signal to check.
            equity: Current account equity.

        Returns:
            (passed, reason) tuple.
        """
        self._check_daily_reset()
        self._check_weekly_reset()
        self._equity = equity

        # D2: block trend-following signals in ranging markets
        if self.block_trend_in_ranging and self._regime_detector.regime == Regime.RANGING:
            strat = getattr(signal, "strategy_name", "")
            if any(k in strat for k in ("trend", "donchian", "breakout", "momentum")):
                return False, (
                    f"Regime is RANGING (ADX={self._regime_detector.adx:.1f}) "
                    f"- trend signal blocked for {strat}"
                )

        # Check portfolio drawdown halt
        if self._drawdown_halted:
            return False, (
                f"Portfolio drawdown exceeds {self.max_drawdown_pct}% "
                f"- halted for {self._drawdown_halt_bars_remaining} more bars"
            )

        # Check halted state
        if self._daily_halted:
            return False, "Daily loss limit reached - trading halted for today"

        if self._weekly_halted:
            return False, "Weekly loss limit reached - trading halted for this week"

        # Check per-strategy consecutive loss cooldown
        strat_name = getattr(signal, "strategy_name", "")
        if strat_name and strat_name in self._strategy_cooldown:
            remaining = self._strategy_cooldown[strat_name]
            return False, (
                f"Strategy {strat_name} paused after "
                f"{self.max_consecutive_losses} consecutive losses "
                f"({remaining} bars remaining)"
            )

        # Check daily trade count
        if self._daily_trade_count >= self.daily_trade_count_limit:
            return False, f"Daily trade count limit reached ({self.daily_trade_count_limit})"

        # Check position value limit
        if signal.quantity > 0 and signal.price > 0:
            notional = signal.quantity * signal.price
            max_notional = equity * (self.max_position_value_pct / 100.0)
            if notional > max_notional:
                return False, (
                    f"Position value {notional:.2f} exceeds limit "
                    f"{max_notional:.2f} ({self.max_position_value_pct}% of equity)"
                )

        return True, ""

    def _on_signal(self, event: SignalEvent) -> None:
        """Handle incoming signal - check risk and convert to order."""
        passed, reason = self.check_signal(event, self._equity)

        if not passed:
            reject = RejectEvent(
                timestamp=self._clock.now(),
                strategy_name=event.strategy_name,
                symbol=event.symbol,
                reason=reason,
                source="risk_manager",
            )
            self.event_bus.publish(reject)
            logger.warning(
                "signal_rejected",
                strategy=event.strategy_name,
                symbol=event.symbol,
                reason=reason,
            )
            return

        # Convert signal to order
        order = OrderEvent(
            timestamp=event.timestamp,
            strategy_name=event.strategy_name,
            symbol=event.symbol,
            side=event.side,
            order_type=event.order_type,
            quantity=event.quantity,
            price=event.price,
            stop_price=event.stop_price,
            reduce_only=event.reduce_only,
            post_only=event.post_only,
            client_order_id="",
            source="risk_manager",
        )
        self.event_bus.publish(order)

    def _on_fill(self, event: FillEvent) -> None:
        """Track PnL from fills for daily/weekly/drawdown limits."""
        self._daily_pnl += event.realized_pnl
        self._weekly_pnl += event.realized_pnl
        self._daily_trade_count += 1

        # Track consecutive losses per strategy
        strat_name = getattr(event, "strategy_name", "")
        if strat_name:
            if event.realized_pnl < 0:
                self._consecutive_losses[strat_name] = (
                    self._consecutive_losses.get(strat_name, 0) + 1
                )
                if self._consecutive_losses[strat_name] >= self.max_consecutive_losses:
                    self._strategy_cooldown[strat_name] = self.loss_cooldown_bars
                    logger.warning(
                        "strategy_consecutive_loss_halt",
                        strategy=strat_name,
                        consecutive=self._consecutive_losses[strat_name],
                        cooldown_bars=self.loss_cooldown_bars,
                    )
            else:
                self._consecutive_losses[strat_name] = 0

        # Check daily loss limit
        if self._equity > 0:
            daily_loss_pct = abs(min(self._daily_pnl, 0)) / self._equity * 100
            if daily_loss_pct >= self.daily_loss_limit_pct and not self._daily_halted:
                self._daily_halted = True
                logger.error(
                    "daily_loss_limit_hit",
                    daily_pnl=self._daily_pnl,
                    limit_pct=self.daily_loss_limit_pct,
                )

            weekly_loss_pct = abs(min(self._weekly_pnl, 0)) / self._equity * 100
            if weekly_loss_pct >= self.weekly_loss_limit_pct and not self._weekly_halted:
                self._weekly_halted = True
                logger.error(
                    "weekly_loss_limit_hit",
                    weekly_pnl=self._weekly_pnl,
                    limit_pct=self.weekly_loss_limit_pct,
                )

            # Check portfolio drawdown
            if self._peak_equity > 0:
                current_dd = (self._peak_equity - self._equity) / self._peak_equity * 100
                if current_dd >= self.max_drawdown_pct and not self._drawdown_halted:
                    self._drawdown_halted = True
                    self._drawdown_halt_bars_remaining = self.drawdown_cooldown_bars
                    logger.error(
                        "portfolio_drawdown_halt",
                        drawdown_pct=round(current_dd, 2),
                        peak_equity=self._peak_equity,
                        current_equity=self._equity,
                        cooldown_bars=self.drawdown_cooldown_bars,
                    )

    def tick_bar(self) -> None:
        """Called each bar to decrement cooldown counters."""
        if self._drawdown_halt_bars_remaining > 0:
            self._drawdown_halt_bars_remaining -= 1
            if self._drawdown_halt_bars_remaining == 0:
                self._drawdown_halted = False
                logger.info("drawdown_cooldown_expired")

        for strat in list(self._strategy_cooldown.keys()):
            self._strategy_cooldown[strat] -= 1
            if self._strategy_cooldown[strat] <= 0:
                del self._strategy_cooldown[strat]
                self._consecutive_losses.pop(strat, None)
                logger.info("strategy_cooldown_expired", strategy=strat)

    @property
    def is_halted(self) -> bool:
        """Check if trading is halted."""
        return self._daily_halted or self._weekly_halted or self._drawdown_halted

    @property
    def daily_pnl(self) -> float:
        """Get current daily PnL."""
        return self._daily_pnl

    @property
    def weekly_pnl(self) -> float:
        """Get current weekly PnL."""
        return self._weekly_pnl

    def get_status(self) -> dict[str, Any]:
        """Get risk manager status summary."""
        current_dd = 0.0
        if self._peak_equity > 0:
            current_dd = (self._peak_equity - self._equity) / self._peak_equity * 100

        return {
            "daily_pnl": self._daily_pnl,
            "weekly_pnl": self._weekly_pnl,
            "daily_trade_count": self._daily_trade_count,
            "daily_halted": self._daily_halted,
            "weekly_halted": self._weekly_halted,
            "drawdown_halted": self._drawdown_halted,
            "drawdown_pct": round(current_dd, 2),
            "drawdown_cooldown_remaining": self._drawdown_halt_bars_remaining,
            "peak_equity": self._peak_equity,
            "equity": self._equity,
            "consecutive_losses": dict(self._consecutive_losses),
            "strategy_cooldowns": dict(self._strategy_cooldown),
        }
