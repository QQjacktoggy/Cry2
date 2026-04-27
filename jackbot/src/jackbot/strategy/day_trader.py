"""Day Trader — top-level orchestrator for Jackbot_V1.

Manages the daily lifecycle of grid trading:
  1. Market assessment → direction & range
  2. Grid creation & monitoring
  3. Daily profit target → aggressive/conservative mode switch
  4. UTC midnight reset

This is the single "strategy" entry point that scripts/run.py creates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from jackbot.core.clock import Clock
from jackbot.core.constants import GridDirection, Regime, TradingMode
from jackbot.core.event_bus import EventBus
from jackbot.core.events import FillEvent, GridProfitEvent, GridSignalEvent, MarketEvent
from jackbot.strategy.grid_engine import GridEngine
from jackbot.strategy.market_assessor import MarketAssessment, MarketAssessor

logger = structlog.get_logger(__name__)


@dataclass
class DayTraderConfig:
    """All tunable parameters for the DayTrader."""

    symbols: list[str] = field(default_factory=lambda: ["BTCUSDC", "ETHUSDC"])
    timeframe: str = "5m"

    # Capital
    total_capital_usd: float = 150.0
    per_symbol_alloc_pct: float = 50.0   # each symbol gets 50% of capital
    compound_pct: float = 0.0            # % of daily profit to add to capital

    # Grid defaults (overridden by MarketAssessor suggestions)
    default_grid_count: int = 10
    max_leverage: int = 10
    min_leverage: int = 5

    # Daily target
    daily_profit_target_usd: float = 10.0

    # Conservative mode (after target hit)
    conservative_size_factor: float = 0.25      # shrink qty to 25%
    conservative_grid_spacing_mult: float = 2.0  # wider grid spacing
    conservative_leverage: int = 3

    # Risk limits
    grid_stop_loss_pct: float = 2.0    # stop grid if unrealized loss > 2%
    max_concurrent_grids: int = 2      # one per symbol
    max_daily_resets: int = 10         # avoid infinite grid resets
    daily_loss_limit_pct: float = 10.0 # halt everything if lost > 10% of total capital
    max_total_notional_multiplier: float = 2.0  # total active notional ≤ capital × this
    margin_rate_threshold: float = 1.10         # A4: force close if margin rate < 110%
    maintenance_margin_rate: float = 0.005      # A4: Binance typical 0.5% mmr

    # Hourly review
    hourly_review_interval_bars: int = 12  # 12 x 5m = 1 hour

    # Market assessor params
    adx_period: int = 14
    trending_threshold: float = 25.0
    ranging_threshold: float = 20.0
    bb_period: int = 20
    bb_std: float = 2.0
    atr_period: int = 14

    warmup_bars: int = 50

    # ── Phase A optimisations (all OFF by default; opt-in via settings) ──

    # t1-dynamic-spacing: regime-aware grid spacing (overrides assessor's grid_count)
    dynamic_spacing_enabled: bool = False
    dynamic_spacing_tick_size: float = 0.1      # USDT, defensive floor
    dynamic_spacing_k_trending: float = 0.8     # spacing = ATR × this in trending regime
    dynamic_spacing_k_neutral: float = 0.6
    dynamic_spacing_k_ranging: float = 0.4
    dynamic_spacing_min_count: int = 5
    dynamic_spacing_max_count: int = 30

    # t1-partial-tp-trailing: lock 50% on +1% move, trail rest by 0.5×ATR
    partial_tp_enabled: bool = False
    partial_tp_pct: float = 1.0
    partial_tp_close_pct: float = 0.5
    trailing_atr_mult: float = 0.5

    def __post_init__(self) -> None:
        # Phase A risk decision: max_leverage hard-capped at 10 across all entry points.
        assert self.max_leverage <= 10, (
            f"max_leverage 上限為 10 (Phase A 風控決議), 收到 {self.max_leverage}"
        )

    @classmethod
    def from_dict(cls, d: dict) -> "DayTraderConfig":
        """Build config from a settings dict."""
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in fields}
        return cls(**filtered)


class DayTrader:
    """Orchestrates grid day-trading across multiple symbols.

    Lifecycle (per 5m bar):
      1. Feed bar data to MarketAssessor
      2. For each symbol with no active grid → assess & create
      3. Check breakouts & stop-losses → close & optionally recreate
      4. Hourly review → re-assess direction, close mismatched grids
      5. Check daily PnL → switch aggressive/conservative
      6. UTC midnight → full reset
    """

    def __init__(
        self,
        config: DayTraderConfig,
        event_bus: EventBus,
        clock: Clock | None = None,
    ) -> None:
        self._cfg = config
        self._bus = event_bus
        self._clock = clock or Clock()

        self._assessor = MarketAssessor(
            adx_period=config.adx_period,
            trending_threshold=config.trending_threshold,
            ranging_threshold=config.ranging_threshold,
            bb_period=config.bb_period,
            bb_std=config.bb_std,
            atr_period=config.atr_period,
            max_leverage=config.max_leverage,
            min_leverage=config.min_leverage,
            default_grid_count=config.default_grid_count,
        )
        self._engine = GridEngine()

        # Daily state
        self._mode: TradingMode = TradingMode.AGGRESSIVE
        self._daily_profit: float = 0.0
        self._daily_resets: int = 0
        self._daily_loss: float = 0.0
        self._halted: bool = False
        self._last_reset_date: str = ""
        self._warming_up: bool = True

        # Bar counter per symbol (for warmup & hourly review)
        self._bar_counts: dict[str, int] = {}
        self._last_review_bar: dict[str, int] = {}  # symbol → bar count at last review
        self._last_prices: dict[str, float] = {}     # symbol → latest close price

    # ── Properties ────────────────────────────────────────────────────

    @property
    def mode(self) -> TradingMode:
        return self._mode

    def mark_warmup_complete(self) -> None:
        """Signal that historical warmup is done; grid creation is now allowed."""
        self._warming_up = False
        logger.info("warmup_mode_off")

    @property
    def daily_profit(self) -> float:
        return self._daily_profit

    @property
    def is_halted(self) -> bool:
        return self._halted

    # ── Main bar handler ──────────────────────────────────────────────

    def on_bar(self, event: MarketEvent) -> list[GridSignalEvent]:
        """Process a new 5m bar. Returns grid signals to execute."""
        self._check_daily_reset()

        if self._halted:
            return []

        symbol = event.symbol
        if symbol not in self._cfg.symbols:
            return []

        # Warmup tracking
        self._bar_counts[symbol] = self._bar_counts.get(symbol, 0) + 1
        self._last_prices[symbol] = event.close

        # Feed to market assessor
        self._assessor.update(symbol, event.high, event.low, event.close)

        if self._bar_counts.get(symbol, 0) < self._cfg.warmup_bars:
            return []

        signals: list[GridSignalEvent] = []

        # Update unrealized PnL for active grids
        self._engine.update_unrealized_pnl(symbol, event.close)

        # t1-partial-tp-trailing: lock partial profit + trail rest before SL fires
        if self._cfg.partial_tp_enabled:
            assessment = self._assessor.assess(symbol)
            atr = assessment.atr if assessment is not None else 0.0
            tp_signals, trailing_ids = self._engine.check_partial_tp_and_trailing(
                symbol, event.close, atr,
            )
            signals.extend(tp_signals)
            for grid_id in trailing_ids:
                close_signals = self._engine.close_grid(grid_id, reason="trailing_exit")
                signals.extend(close_signals)
                self._daily_resets = min(self._daily_resets + 1, self._cfg.max_daily_resets)

        # A4: Margin rate check — fires BEFORE 3% SL to catch gap-down scenarios
        margin_critical_ids = self._engine.check_margin_rate(
            symbol, self._cfg.margin_rate_threshold, self._cfg.maintenance_margin_rate
        )
        for grid_id in margin_critical_ids:
            grid = self._engine.get_grid(grid_id)
            if grid:
                self._daily_loss += abs(grid.unrealized_pnl)
                logger.warning(
                    "grid_margin_liquidation_prevented",
                    grid_id=grid_id,
                    unrealized_pnl=grid.unrealized_pnl,
                )
            close_signals = self._engine.close_grid(grid_id, reason="margin_rate")
            signals.extend(close_signals)
            self._daily_resets = min(self._daily_resets + 1, self._cfg.max_daily_resets)

        # Check stop-losses on active grids (grids already closed above are skipped)
        stopped_ids = self._engine.check_stop_loss(symbol, self._cfg.grid_stop_loss_pct)
        for grid_id in stopped_ids:
            grid = self._engine.get_grid(grid_id)
            if grid:
                self._daily_loss += abs(grid.unrealized_pnl)
                logger.warning(
                    "grid_stopped_loss",
                    grid_id=grid_id,
                    unrealized_pnl=grid.unrealized_pnl,
                    daily_loss=round(self._daily_loss, 4),
                )
            close_signals = self._engine.close_grid(grid_id, reason="stop_loss")
            signals.extend(close_signals)
            self._daily_resets = min(self._daily_resets + 1, self._cfg.max_daily_resets)

        # Check breakouts on active grids for this symbol
        breakout_ids = self._engine.check_breakout(symbol, event.close)
        for grid_id in breakout_ids:
            close_signals = self._engine.close_grid(grid_id, reason="breakout")
            signals.extend(close_signals)
            self._daily_resets = min(self._daily_resets + 1, self._cfg.max_daily_resets)

        # Hourly review: re-assess and close mismatched grids
        review_signals = self._hourly_review(symbol, event.close)
        signals.extend(review_signals)

        # Check daily loss limit
        max_allowed_loss = self._cfg.total_capital_usd * (self._cfg.daily_loss_limit_pct / 100.0)
        if self._daily_loss >= max_allowed_loss:
            if not self._halted:
                self._halt("daily_loss_limit_hit")
            return signals

        # If no active grid for this symbol → assess & create
        active_for_symbol = [
            g for g in self._engine.active_grids if g.symbol == symbol
        ]

        if not active_for_symbol and not self._warming_up and self._daily_resets < self._cfg.max_daily_resets:
            create_signals = self._try_create_grid(symbol, event.close)
            signals.extend(create_signals)

        return signals

    # ── Fill handler ──────────────────────────────────────────────────

    def on_fill(self, fill: FillEvent) -> list[GridSignalEvent]:
        """Process fill, place counter-orders, track profit."""
        counter_signals, profit_event = self._engine.on_fill(fill)

        if profit_event is not None:
            self._daily_profit += profit_event.profit_usd
            self._bus.publish(profit_event)

            logger.info(
                "daily_profit_update",
                daily_profit=round(self._daily_profit, 4),
                target=self._cfg.daily_profit_target_usd,
                mode=self._mode.value,
            )

            # Check daily target
            if (
                self._mode == TradingMode.AGGRESSIVE
                and self._daily_profit >= self._cfg.daily_profit_target_usd
            ):
                self._switch_to_conservative()

        # Track losses from fill PnL
        if fill.realized_pnl < 0:
            self._daily_loss += abs(fill.realized_pnl)

        # Check daily loss limit based on percentage of current capital
        max_allowed_loss = self._cfg.total_capital_usd * (self._cfg.daily_loss_limit_pct / 100.0)
        if self._daily_loss >= max_allowed_loss and not self._halted:
            self._halt("daily_loss_limit_reached")

        return counter_signals

    # ── Grid creation ─────────────────────────────────────────────────

    def _try_create_grid(self, symbol: str, current_price: float) -> list[GridSignalEvent]:
        """Assess market and create a grid if conditions are met."""
        # A5: Hard daily reset cap — redundant safety net regardless of caller
        if self._daily_resets >= self._cfg.max_daily_resets:
            return []

        if len(self._engine.active_grids) >= self._cfg.max_concurrent_grids:
            return []

        # A3b: Total exposure cap — limit total invested margin across all active grids.
        # Uses margin (total_investment), not leveraged notional, so normal 2-grid
        # operation (75+75=150 USDT) stays well within cap (150 × 2.0 = 300 USDT).
        total_invested = sum(g.total_investment for g in self._engine.active_grids)
        investment_limit = self._cfg.total_capital_usd * self._cfg.max_total_notional_multiplier
        if total_invested >= investment_limit:
            logger.debug(
                "exposure_cap_skip",
                symbol=symbol,
                total_invested=round(total_invested, 2),
                limit=round(investment_limit, 2),
            )
            return []

        assessment = self._assessor.assess(symbol)
        if assessment is None:
            return []

        # Skip low-confidence assessments
        if assessment.confidence < 0.3:
            logger.debug("low_confidence_skip", symbol=symbol, confidence=assessment.confidence)
            return []

        # Apply conservative mode adjustments
        leverage = assessment.suggested_leverage
        investment = self._cfg.total_capital_usd * (self._cfg.per_symbol_alloc_pct / 100)
        grid_count = assessment.suggested_grid_count
        upper = assessment.upper_price
        lower = assessment.lower_price

        if self._cfg.dynamic_spacing_enabled:
            grid_count = self._compute_dynamic_grid_count(assessment)

        if self._mode == TradingMode.CONSERVATIVE:
            leverage = min(leverage, self._cfg.conservative_leverage)
            investment *= self._cfg.conservative_size_factor
            # Widen the range
            mid = (upper + lower) / 2
            half_range = (upper - lower) / 2 * self._cfg.conservative_grid_spacing_mult
            upper = mid + half_range
            lower = mid - half_range

        grid, signals = self._engine.create_grid(
            symbol=symbol,
            direction=assessment.direction,
            upper_price=upper,
            lower_price=lower,
            grid_count=grid_count,
            leverage=leverage,
            total_investment=investment,
            current_price=current_price,
            partial_tp_enabled=self._cfg.partial_tp_enabled,
            partial_tp_pct=self._cfg.partial_tp_pct,
            partial_tp_close_pct=self._cfg.partial_tp_close_pct,
            trailing_atr_mult=self._cfg.trailing_atr_mult,
        )

        logger.info(
            "grid_started",
            grid_id=grid.grid_id,
            symbol=symbol,
            direction=assessment.direction.value,
            regime=assessment.regime.value,
            adx=assessment.adx,
            leverage=leverage,
            investment=round(investment, 2),
            mode=self._mode.value,
        )

        return signals

    # ── Dynamic spacing (t1-dynamic-spacing) ─────────────────────────

    def _compute_dynamic_grid_count(self, assessment: MarketAssessment) -> int:
        """Choose grid_count from a regime-aware spacing instead of fixed N.

        spacing = max(tick_size × 5, ATR × k); k depends on regime so trending
        markets get wider levels (fewer trips, but each level captures real
        directional moves) while ranging markets get tighter levels (more
        round-trip profit at low risk).
        """
        if assessment.regime == Regime.TRENDING:
            k = self._cfg.dynamic_spacing_k_trending
        elif assessment.regime == Regime.RANGING:
            k = self._cfg.dynamic_spacing_k_ranging
        else:
            k = self._cfg.dynamic_spacing_k_neutral

        floor = self._cfg.dynamic_spacing_tick_size * 5
        spacing = max(floor, assessment.atr * k)

        price_range = max(assessment.upper_price - assessment.lower_price, spacing)
        raw = int(price_range // spacing)
        return max(
            self._cfg.dynamic_spacing_min_count,
            min(self._cfg.dynamic_spacing_max_count, raw),
        )

    # ── Hourly review ─────────────────────────────────────────────

    def _hourly_review(self, symbol: str, current_price: float) -> list[GridSignalEvent]:
        """Every hour, re-assess market and close grids that no longer fit.

        Checks:
          1. Has the market direction significantly changed?
             e.g. grid is LONG but assessment now says SHORT → close & recreate
          2. Has the grid drifted far from the optimal range?
             e.g. current price is in the top/bottom 10% of the grid range
          3. Is the grid too old without meaningful profit?
        """
        bar_count = self._bar_counts.get(symbol, 0)
        last_review = self._last_review_bar.get(symbol, 0)
        interval = self._cfg.hourly_review_interval_bars

        if bar_count - last_review < interval:
            return []

        self._last_review_bar[symbol] = bar_count
        signals: list[GridSignalEvent] = []

        active_for_symbol = [
            g for g in self._engine.active_grids if g.symbol == symbol
        ]

        if not active_for_symbol:
            return []

        # Get fresh assessment
        assessment = self._assessor.assess(symbol)
        if assessment is None:
            return []

        for grid in active_for_symbol:
            should_close = False
            reason = ""

            # Check 1: Direction mismatch
            # e.g. grid=LONG but market now clearly SHORT
            if (
                grid.direction == GridDirection.LONG
                and assessment.direction == GridDirection.SHORT
            ):
                should_close = True
                reason = f"direction_mismatch: grid={grid.direction.value} market={assessment.direction.value}"
            elif (
                grid.direction == GridDirection.SHORT
                and assessment.direction == GridDirection.LONG
            ):
                should_close = True
                reason = f"direction_mismatch: grid={grid.direction.value} market={assessment.direction.value}"

            # Check 2: Price drifted to edge of grid (top/bottom 10%)
            grid_range = grid.upper_price - grid.lower_price
            if grid_range > 0:
                position_in_grid = (current_price - grid.lower_price) / grid_range
                if position_in_grid > 0.95 or position_in_grid < 0.05:
                    should_close = True
                    reason = f"price_at_edge: position={position_in_grid:.2f}"

            # Check 3: Grid too old with negative PnL
            age_hours = (self._clock.now() - grid.created_at).total_seconds() / 3600
            net_pnl = grid.matched_profit + grid.unrealized_pnl
            if age_hours >= 3 and net_pnl < 0:
                should_close = True
                reason = f"stale_negative: age={age_hours:.1f}h pnl={net_pnl:.4f}"

            if should_close:
                logger.info(
                    "hourly_review_close",
                    grid_id=grid.grid_id,
                    symbol=symbol,
                    reason=reason,
                    adx=assessment.adx,
                    direction=assessment.direction.value,
                )
                close_signals = self._engine.close_grid(grid.grid_id, reason=f"review:{reason}")
                signals.extend(close_signals)
                self._daily_resets = min(self._daily_resets + 1, self._cfg.max_daily_resets)
            else:
                logger.info(
                    "hourly_review_ok",
                    grid_id=grid.grid_id,
                    symbol=symbol,
                    direction=grid.direction.value,
                    market_direction=assessment.direction.value,
                    realized=round(grid.matched_profit, 4),
                    unrealized=round(grid.unrealized_pnl, 4),
                    adx=assessment.adx,
                )

        return signals

    # ── Mode switching ────────────────────────────────────────────────

    def _switch_to_conservative(self) -> None:
        self._mode = TradingMode.CONSERVATIVE
        logger.info(
            "daily_target_reached",
            daily_profit=round(self._daily_profit, 4),
            target=self._cfg.daily_profit_target_usd,
            action="switch_to_conservative",
        )

    def _halt(self, reason: str) -> None:
        self._halted = True
        # Close all active grids
        for grid in list(self._engine.active_grids):
            self._engine.close_grid(grid.grid_id, reason=reason)
        logger.error("day_trader_halted", reason=reason, daily_loss=round(self._daily_loss, 4))

    # ── Daily reset ───────────────────────────────────────────────────

    def _check_daily_reset(self) -> None:
        """Reset daily state at UTC midnight."""
        today = self._clock.now().strftime("%Y-%m-%d")
        if today != self._last_reset_date:
            if self._last_reset_date:
                # Compound profits if configured
                compound_amount = 0.0
                if self._daily_profit > 0 and self._cfg.compound_pct > 0:
                    compound_amount = self._daily_profit * (self._cfg.compound_pct / 100.0)
                    self._cfg.total_capital_usd += compound_amount

                logger.info(
                    "daily_reset",
                    prev_profit=round(self._daily_profit, 4),
                    prev_mode=self._mode.value,
                    prev_resets=self._daily_resets,
                    compounded_amount=round(compound_amount, 4),
                    new_capital=round(self._cfg.total_capital_usd, 4),
                )
            self._daily_profit = 0.0
            self._daily_loss = 0.0
            self._daily_resets = 0
            self._mode = TradingMode.AGGRESSIVE
            self._halted = False
            self._last_reset_date = today

    # ── Status ────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        return {
            "mode": self._mode.value,
            "daily_profit": round(self._daily_profit, 4),
            "daily_target": self._cfg.daily_profit_target_usd,
            "daily_loss": round(self._daily_loss, 4),
            "daily_resets": self._daily_resets,
            "halted": self._halted,
            "active_grids": self._engine.get_status(),
            "total_profit": round(self._engine.get_total_profit(), 4),
            "bar_counts": dict(self._bar_counts),
        }

    def close_all(self, reason: str = "shutdown") -> list[GridSignalEvent]:
        """Close all active grids (for shutdown / kill switch)."""
        signals: list[GridSignalEvent] = []
        for grid in list(self._engine.active_grids):
            signals.extend(self._engine.close_grid(grid.grid_id, reason=reason))
        return signals

    def manual_halt(self, reason: str = "manual") -> None:
        """Manually halt trading via external control (e.g. Telegram)."""
        if not self._halted:
            self._halt(reason)

    def manual_resume(self) -> bool:
        """Resume trading if hard risk constraints are not currently violated."""
        max_allowed_loss = self._cfg.total_capital_usd * (self._cfg.daily_loss_limit_pct / 100.0)
        if self._daily_loss >= max_allowed_loss:
            return False
        self._halted = False
        return True

    def set_mode(self, mode: TradingMode) -> None:
        """Force trading mode via external control."""
        self._mode = mode
