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
from jackbot.core.constants import GridDirection, TradingMode
from jackbot.core.event_bus import EventBus
from jackbot.core.events import FillEvent, GridProfitEvent, GridSignalEvent, MarketEvent, StrategyPnLEvent
from jackbot.strategy.grid_engine import GridEngine
from jackbot.strategy.market_assessor import MarketAssessment, MarketAssessor

logger = structlog.get_logger(__name__)


@dataclass
class DayTraderConfig:
    """All tunable parameters for the DayTrader."""

    symbols: list[str] = field(default_factory=lambda: ["BTCUSDC", "ETHUSDC"])
    timeframe: str = "5m"
    strategy_variant: str = "baseline_grid"

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

    # Fee-aware filters
    maker_fee_rate: float = 0.0
    taker_fee_rate: float = 0.0004
    expected_edge_floor_bps: float = 0.0
    slippage_buffer_bps: float = 0.0
    safety_margin_bps: float = 0.0
    # Non-bypassable guard: reject grids whose step_bps < round-trip fee × this ratio.
    # Set to 0 to disable. Independent of variant — protects against fee-induced losses.
    hard_fee_margin_ratio: float = 1.5
    breakout_cooldown_bars: int = 0
    adaptive_spacing_adx_weight: float = 0.0
    adaptive_spacing_atr_weight: float = 0.0
    adaptive_spacing_max_mult: float = 1.0

    # Hybrid trend sleeve
    grid_allocation_pct: float = 100.0
    trend_allocation_pct: float = 0.0
    buffer_allocation_pct: float = 0.0
    directional_adx_threshold: float = 22.0
    trend_activation_adx: float = 28.0
    trend_activation_slope: float = 1.0
    trend_trailing_atr_mult: float = 2.2

    warmup_bars: int = 50

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


@dataclass
class TrendPosition:
    """Lightweight trend sleeve state for the hybrid variant."""

    symbol: str
    side: str
    quantity: float
    entry_price: float
    stop_price: float
    peak_price: float
    trough_price: float
    allocated_capital: float
    opened_at: datetime
    entry_fee: float = 0.0


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
        self._safe_mode: bool = False
        self._safe_mode_reason: str = ""
        self._last_reset_date: str = ""
        self._warming_up: bool = True

        # Bar counter per symbol (for warmup & hourly review)
        self._bar_counts: dict[str, int] = {}
        self._last_review_bar: dict[str, int] = {}  # symbol → bar count at last review
        self._last_prices: dict[str, float] = {}     # symbol → latest close price
        self._last_report_date: str = ""
        self._grid_cooldown_until_bar: dict[str, int] = {}
        self._latest_variant_regime: dict[str, str] = {}
        self._trend_positions: dict[str, TrendPosition] = {}
        self._grid_pnl_net: float = 0.0
        self._trend_pnl_net: float = 0.0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def mode(self) -> TradingMode:
        return self._mode

    def mark_warmup_complete(self) -> None:
        """Signal that historical warmup is done; grid creation is now allowed."""
        self._warming_up = False
        logger.info("warmup_mode_off")

    def ingest_warmup_bar(self, event: MarketEvent) -> None:
        """Replay a historical bar for indicators without running trading logic."""
        if event.symbol not in self._cfg.symbols:
            return
        self._bar_counts[event.symbol] = self._bar_counts.get(event.symbol, 0) + 1
        self._last_prices[event.symbol] = event.close
        self._assessor.update(event.symbol, event.high, event.low, event.close)

    @property
    def daily_profit(self) -> float:
        return self._daily_profit

    @property
    def unrealized_pnl(self) -> float:
        """Sum of unrealized PnL from all active grids."""
        return sum(grid.unrealized_pnl for grid in self._engine.active_grids)

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def safe_mode(self) -> bool:
        return self._safe_mode

    def enter_safe_mode(self, reason: str) -> None:
        self._safe_mode = True
        self._safe_mode_reason = reason
        logger.warning("day_trader_safe_mode_entered", reason=reason)

    def exit_safe_mode(self) -> None:
        self._safe_mode = False
        self._safe_mode_reason = ""
        logger.info("day_trader_safe_mode_exited")

    def snapshot_runtime_state(self) -> dict[str, Any]:
        """Return the minimal state needed to continue after a restart."""
        return {
            "mode": self._mode.value,
            "daily_profit": self._daily_profit,
            "daily_loss": self._daily_loss,
            "daily_resets": self._daily_resets,
            "halted": self._halted,
            "safe_mode": self._safe_mode,
            "safe_mode_reason": self._safe_mode_reason,
            "last_reset_date": self._last_reset_date,
            "bar_counts": dict(self._bar_counts),
            "last_review_bar": dict(self._last_review_bar),
            "last_prices": dict(self._last_prices),
            "last_report_date": self._last_report_date,
            "grid_cooldown_until_bar": dict(self._grid_cooldown_until_bar),
            "latest_variant_regime": dict(self._latest_variant_regime),
            "grid_pnl_net": self._grid_pnl_net,
            "trend_pnl_net": self._trend_pnl_net,
        }

    def restore_runtime_state(self, snapshot: dict[str, Any]) -> None:
        """Restore state captured by ``snapshot_runtime_state``."""
        self._mode = TradingMode(snapshot.get("mode", self._mode.value))
        self._daily_profit = float(snapshot.get("daily_profit", 0.0) or 0.0)
        self._daily_loss = float(snapshot.get("daily_loss", 0.0) or 0.0)
        self._daily_resets = int(snapshot.get("daily_resets", 0) or 0)
        self._halted = bool(snapshot.get("halted", False))
        self._safe_mode = bool(snapshot.get("safe_mode", False))
        self._safe_mode_reason = str(snapshot.get("safe_mode_reason", "") or "")
        self._last_reset_date = str(snapshot.get("last_reset_date", "") or "")
        self._bar_counts = {str(k): int(v) for k, v in dict(snapshot.get("bar_counts", {})).items()}
        self._last_review_bar = {
            str(k): int(v) for k, v in dict(snapshot.get("last_review_bar", {})).items()
        }
        self._last_prices = {str(k): float(v) for k, v in dict(snapshot.get("last_prices", {})).items()}
        self._last_report_date = str(snapshot.get("last_report_date", "") or "")
        self._grid_cooldown_until_bar = {
            str(k): int(v) for k, v in dict(snapshot.get("grid_cooldown_until_bar", {})).items()
        }
        self._latest_variant_regime = {
            str(k): str(v) for k, v in dict(snapshot.get("latest_variant_regime", {})).items()
        }
        self._grid_pnl_net = float(snapshot.get("grid_pnl_net", 0.0) or 0.0)
        self._trend_pnl_net = float(snapshot.get("trend_pnl_net", 0.0) or 0.0)

    # ── Main bar handler ──────────────────────────────────────────────

    def on_bar(self, event: MarketEvent) -> list[GridSignalEvent]:
        """Process a new 5m bar. Returns grid signals to execute."""
        self._check_daily_reset()
        self._check_daily_report()

        if self._halted or self._safe_mode:
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
            self._arm_breakout_cooldown(symbol)

        assessment = self._assessor.assess(symbol)

        # Hourly review: re-assess and close mismatched grids
        review_signals = self._hourly_review(symbol, event.close, assessment)
        signals.extend(review_signals)

        if assessment is not None and self._cfg.strategy_variant == "hybrid_trend_grid":
            self._manage_trend_rider(event, assessment)

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
            create_signals = self._try_create_grid(symbol, event.close, assessment)
            signals.extend(create_signals)

        return signals

    def _check_daily_report(self) -> None:
        """Trigger daily summary report at 13:00 UTC (21:00 TPE)."""
        now = self._clock.now()
        today = now.strftime("%Y-%m-%d")
        
        # Trigger if it's 13:00 UTC or later, and we haven't reported today
        if now.hour >= 13 and self._last_report_date != today:
            self._last_report_date = today
            logger.info("daily_report_triggered", time=now.isoformat())
            # Use GridProfitEvent as a carrier or create a dedicated event
            # For simplicity, we trigger a dedicated status push via EventBus
            self._bus.publish_status_report()

    # ── Fill handler ──────────────────────────────────────────────────

    def on_fill(self, fill: FillEvent) -> list[GridSignalEvent]:
        """Process fill, place counter-orders, track profit."""
        counter_signals, profit_event = self._engine.on_fill(fill)

        if fill.commission > 0:
            # Commission is realized the moment the fill happens, even if the
            # paired grid profit or forced close arrives later.
            self._record_realized_pnl(-fill.commission, bucket="grid")

        if profit_event is not None:
            self._record_realized_pnl(profit_event.profit_usd, bucket="grid")
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

    def record_realized_pnl(self, net_pnl: float, bucket: str = "grid") -> None:
        """Record realized PnL that did not arrive through a GridProfitEvent."""
        self._record_realized_pnl(net_pnl, bucket=bucket)

    def _record_realized_pnl(self, net_pnl: float, bucket: str) -> None:
        self._daily_profit += net_pnl
        if bucket == "trend":
            self._trend_pnl_net += net_pnl
        else:
            self._grid_pnl_net += net_pnl

    # ── Grid creation ─────────────────────────────────────────────────

    def _try_create_grid(
        self,
        symbol: str,
        current_price: float,
        assessment: MarketAssessment | None,
    ) -> list[GridSignalEvent]:
        """Assess market and create a grid if conditions are met."""
        # A5: Hard daily reset cap — redundant safety net regardless of caller
        if self._daily_resets >= self._cfg.max_daily_resets:
            return []

        if len(self._engine.active_grids) >= self._cfg.max_concurrent_grids:
            return []

        if self._is_in_breakout_cooldown(symbol):
            self._latest_variant_regime[symbol] = "cooldown"
            logger.info(
                "breakout_cooldown_skip",
                symbol=symbol,
                cooldown_until_bar=self._grid_cooldown_until_bar.get(symbol, 0),
                current_bar=self._bar_counts.get(symbol, 0),
            )
            return []

        # A3b: Total exposure cap — limit total invested margin across all active grids.
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

        if assessment is None:
            return []

        # Skip low-confidence assessments
        if assessment.confidence < 0.3:
            logger.debug("low_confidence_skip", symbol=symbol, confidence=assessment.confidence)
            return []

        variant_regime = self._resolve_variant_regime(symbol, assessment)
        self._latest_variant_regime[symbol] = variant_regime
        if variant_regime == "cooldown":
            return []

        direction = assessment.direction
        if self._cfg.strategy_variant == "hybrid_trend_grid":
            direction = self._resolve_hybrid_direction(assessment, variant_regime)

        leverage = assessment.suggested_leverage
        investment = self._cfg.total_capital_usd * (self._cfg.per_symbol_alloc_pct / 100)
        investment *= self._cfg.grid_allocation_pct / 100
        grid_count = assessment.suggested_grid_count
        upper = assessment.upper_price
        lower = assessment.lower_price

        spacing_mult = self._compute_spacing_multiplier(assessment)
        if spacing_mult > 1.0:
            upper, lower = self._widen_range(upper, lower, spacing_mult)

        if self._mode == TradingMode.CONSERVATIVE:
            leverage = min(leverage, self._cfg.conservative_leverage)
            investment *= self._cfg.conservative_size_factor
            upper, lower = self._widen_range(upper, lower, self._cfg.conservative_grid_spacing_mult)

        if self._cfg.strategy_variant == "hybrid_trend_grid" and variant_regime in {"directional", "trend"}:
            grid_count = max(4, int(round(grid_count * 0.8)))

        if self._cfg.hard_fee_margin_ratio > 0 and current_price > 0 and grid_count > 0 and upper > lower:
            fee_bps_guard = (self._cfg.maker_fee_rate * 2 + self._cfg.taker_fee_rate) * 10000
            step_bps_guard = (upper - lower) / grid_count / current_price * 10000
            required_step_bps = fee_bps_guard * self._cfg.hard_fee_margin_ratio
            if step_bps_guard < required_step_bps:
                logger.info(
                    "hard_fee_guard_skip",
                    symbol=symbol,
                    variant=self._cfg.strategy_variant,
                    regime=variant_regime,
                    step_bps=round(step_bps_guard, 3),
                    fee_bps=round(fee_bps_guard, 3),
                    required_step_bps=round(required_step_bps, 3),
                    ratio=self._cfg.hard_fee_margin_ratio,
                )
                return []

        expected_edge_bps = self._compute_expected_edge_bps(current_price, upper, lower, grid_count)
        if expected_edge_bps < self._cfg.expected_edge_floor_bps:
            logger.info(
                "expected_edge_skip",
                symbol=symbol,
                variant=self._cfg.strategy_variant,
                regime=variant_regime,
                expected_edge_bps=round(expected_edge_bps, 3),
                floor_bps=self._cfg.expected_edge_floor_bps,
                spacing_mult=round(spacing_mult, 3),
            )
            return []

        grid, signals = self._engine.create_grid(
            symbol=symbol,
            direction=direction,
            upper_price=upper,
            lower_price=lower,
            grid_count=grid_count,
            leverage=leverage,
            total_investment=investment,
            current_price=current_price,
        )

        logger.info(
            "grid_started",
            grid_id=grid.grid_id,
            symbol=symbol,
            direction=direction.value,
            regime=variant_regime,
            base_regime=assessment.regime.value,
            adx=assessment.adx,
            leverage=leverage,
            investment=round(investment, 2),
            mode=self._mode.value,
            spacing_mult=round(spacing_mult, 3),
            expected_edge_bps=round(expected_edge_bps, 3),
            variant=self._cfg.strategy_variant,
        )

        return signals

    def _compute_spacing_multiplier(self, assessment: MarketAssessment) -> float:
        if self._cfg.adaptive_spacing_max_mult <= 1.0:
            return 1.0
        adx_excess = max(0.0, assessment.adx - self._cfg.trending_threshold)
        # `atr_pct` is stored in percent units, so 0.35 means 0.35% ATR / price.
        atr_excess = max(0.0, assessment.atr_pct - 0.35)
        bonus = (
            adx_excess * self._cfg.adaptive_spacing_adx_weight / 20.0
            + atr_excess * self._cfg.adaptive_spacing_atr_weight / 1.5
        )
        return min(self._cfg.adaptive_spacing_max_mult, 1.0 + bonus)

    def _compute_expected_edge_bps(
        self,
        current_price: float,
        upper_price: float,
        lower_price: float,
        grid_count: int,
    ) -> float:
        if current_price <= 0 or grid_count <= 0 or upper_price <= lower_price:
            return -9999.0
        step = (upper_price - lower_price) / grid_count
        step_bps = step / current_price * 10000
        fee_bps = (self._cfg.maker_fee_rate * 2 + self._cfg.taker_fee_rate) * 10000
        return step_bps - fee_bps - self._cfg.slippage_buffer_bps - self._cfg.safety_margin_bps

    def _widen_range(self, upper_price: float, lower_price: float, spacing_mult: float) -> tuple[float, float]:
        mid = (upper_price + lower_price) / 2
        half_range = (upper_price - lower_price) / 2 * spacing_mult
        return mid + half_range, mid - half_range

    def _arm_breakout_cooldown(self, symbol: str) -> None:
        if self._cfg.breakout_cooldown_bars <= 0:
            return
        self._grid_cooldown_until_bar[symbol] = self._bar_counts.get(symbol, 0) + self._cfg.breakout_cooldown_bars

    def _is_in_breakout_cooldown(self, symbol: str) -> bool:
        return self._bar_counts.get(symbol, 0) < self._grid_cooldown_until_bar.get(symbol, 0)

    def _resolve_variant_regime(self, symbol: str, assessment: MarketAssessment) -> str:
        if self._cfg.strategy_variant != "hybrid_trend_grid":
            return assessment.regime.value
        if self._is_in_breakout_cooldown(symbol) and symbol not in self._trend_positions:
            return "cooldown"
        ema_bias = assessment.ema_fast - assessment.ema_slow
        long_breakout = assessment.current_price >= assessment.upper_price - assessment.atr * 0.2
        short_breakout = assessment.current_price <= assessment.lower_price + assessment.atr * 0.2
        if (
            assessment.adx >= self._cfg.trend_activation_adx
            and assessment.adx_slope >= self._cfg.trend_activation_slope
            and (
                (ema_bias > 0 and long_breakout)
                or (ema_bias < 0 and short_breakout)
            )
        ):
            return "trend"
        if (
            assessment.adx >= self._cfg.directional_adx_threshold
            and (
                abs(assessment.plus_di - assessment.minus_di) >= 4.0
                or abs(ema_bias) >= max(assessment.current_price * 0.0015, assessment.atr * 0.3)
            )
        ):
            return "directional"
        return "range"

    def _resolve_hybrid_direction(self, assessment: MarketAssessment, regime: str) -> GridDirection:
        if regime == "range":
            return GridDirection.NEUTRAL
        if assessment.ema_fast > assessment.ema_slow and assessment.plus_di >= assessment.minus_di:
            return GridDirection.LONG
        if assessment.ema_fast < assessment.ema_slow and assessment.minus_di >= assessment.plus_di:
            return GridDirection.SHORT
        return assessment.direction

    # ── Hourly review ─────────────────────────────────────────────

    def _hourly_review(
        self,
        symbol: str,
        current_price: float,
        assessment: MarketAssessment | None,
    ) -> list[GridSignalEvent]:
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

    def _manage_trend_rider(self, event: MarketEvent, assessment: MarketAssessment) -> None:
        symbol = event.symbol
        regime = self._resolve_variant_regime(symbol, assessment)
        self._latest_variant_regime[symbol] = regime

        position = self._trend_positions.get(symbol)
        if position is not None:
            self._update_trend_position(position, event, assessment)
            position = self._trend_positions.get(symbol)

        if position is None and regime == "trend":
            self._open_trend_position(symbol, event, assessment)

    def _open_trend_position(self, symbol: str, event: MarketEvent, assessment: MarketAssessment) -> None:
        capital = self._cfg.total_capital_usd * (self._cfg.per_symbol_alloc_pct / 100)
        allocated = capital * (self._cfg.trend_allocation_pct / 100)
        if allocated <= 0 or event.close <= 0:
            return

        side = "long" if assessment.ema_fast >= assessment.ema_slow else "short"
        quantity = round(allocated / event.close, 6)
        if quantity <= 0:
            return

        if side == "long":
            stop_price = event.close - assessment.atr * self._cfg.trend_trailing_atr_mult
        else:
            stop_price = event.close + assessment.atr * self._cfg.trend_trailing_atr_mult

        entry_fee = event.close * quantity * self._cfg.taker_fee_rate
        self._trend_positions[symbol] = TrendPosition(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=event.close,
            stop_price=stop_price,
            peak_price=event.high,
            trough_price=event.low,
            allocated_capital=allocated,
            opened_at=event.timestamp,
            entry_fee=entry_fee,
        )
        self._publish_strategy_pnl(
            symbol=symbol,
            source="trend_entry",
            bucket="trend",
            gross_pnl=0.0,
            commission=entry_fee,
            metadata={
                "side": side,
                "entry_price": round(event.close, 4),
                "quantity": quantity,
            },
        )
        logger.info(
            "trend_rider_entered",
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=round(event.close, 4),
            stop_price=round(stop_price, 4),
            adx=assessment.adx,
            adx_slope=assessment.adx_slope,
        )

    def _update_trend_position(
        self,
        position: TrendPosition,
        event: MarketEvent,
        assessment: MarketAssessment,
    ) -> None:
        if position.side == "long":
            position.peak_price = max(position.peak_price, event.high)
            trailing_stop = position.peak_price - assessment.atr * self._cfg.trend_trailing_atr_mult
            position.stop_price = max(position.stop_price, trailing_stop)
            if event.low <= position.stop_price:
                self._close_trend_position(position, position.stop_price, "atr_trailing_stop", event)
                return
            if assessment.ema_fast < assessment.ema_slow:
                self._close_trend_position(position, event.close, "ema_alignment_lost", event)
                return
        else:
            position.trough_price = min(position.trough_price, event.low)
            trailing_stop = position.trough_price + assessment.atr * self._cfg.trend_trailing_atr_mult
            position.stop_price = min(position.stop_price, trailing_stop)
            if event.high >= position.stop_price:
                self._close_trend_position(position, position.stop_price, "atr_trailing_stop", event)
                return
            if assessment.ema_fast > assessment.ema_slow:
                self._close_trend_position(position, event.close, "ema_alignment_lost", event)

    def _close_trend_position(
        self,
        position: TrendPosition,
        exit_price: float,
        reason: str,
        event: MarketEvent,
    ) -> None:
        exit_notional = exit_price * position.quantity
        exit_fee = exit_notional * self._cfg.taker_fee_rate
        if position.side == "long":
            gross_pnl = (exit_price - position.entry_price) * position.quantity
        else:
            gross_pnl = (position.entry_price - exit_price) * position.quantity
        self._publish_strategy_pnl(
            symbol=position.symbol,
            source="trend_exit",
            bucket="trend",
            gross_pnl=gross_pnl,
            commission=exit_fee,
            metadata={
                "reason": reason,
                "side": position.side,
                "entry_price": round(position.entry_price, 4),
                "exit_price": round(exit_price, 4),
                "quantity": position.quantity,
                "held_minutes": round((event.timestamp - position.opened_at).total_seconds() / 60, 2),
            },
        )
        self._trend_positions.pop(position.symbol, None)
        self._arm_breakout_cooldown(position.symbol)
        logger.info(
            "trend_rider_closed",
            symbol=position.symbol,
            side=position.side,
            exit_price=round(exit_price, 4),
            gross_pnl=round(gross_pnl, 4),
            reason=reason,
        )

    def _publish_strategy_pnl(
        self,
        symbol: str,
        source: str,
        bucket: str,
        gross_pnl: float,
        commission: float,
        metadata: dict[str, Any],
    ) -> None:
        net_pnl = gross_pnl - commission
        self._record_realized_pnl(net_pnl, bucket=bucket)
        self._bus.publish(StrategyPnLEvent(
            timestamp=self._clock.now(),
            symbol=symbol,
            source=source,
            bucket=bucket,
            gross_pnl=round(gross_pnl, 4),
            commission=round(commission, 6),
            net_pnl=round(net_pnl, 4),
            metadata=metadata,
        ))

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
        for symbol, position in list(self._trend_positions.items()):
            last_price = self._last_prices.get(symbol, position.entry_price)
            halt_event = MarketEvent(
                timestamp=self._clock.now(),
                symbol=symbol,
                timeframe=self._cfg.timeframe,
                open=last_price,
                high=last_price,
                low=last_price,
                close=last_price,
                volume=0.0,
                source="halt",
            )
            self._close_trend_position(position, last_price, reason, halt_event)
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
            "strategy_variant": self._cfg.strategy_variant,
            "mode": self._mode.value,
            "daily_profit": round(self._daily_profit, 4),
            "daily_target": self._cfg.daily_profit_target_usd,
            "daily_loss": round(self._daily_loss, 4),
            "daily_resets": self._daily_resets,
            "halted": self._halted,
            "safe_mode": self._safe_mode,
            "safe_mode_reason": self._safe_mode_reason,
            "latest_variant_regime": dict(self._latest_variant_regime),
            "active_grids": self._engine.get_status(),
            "trend_positions": {
                symbol: {
                    "side": pos.side,
                    "entry_price": round(pos.entry_price, 4),
                    "stop_price": round(pos.stop_price, 4),
                    "quantity": pos.quantity,
                }
                for symbol, pos in self._trend_positions.items()
            },
            "strategy_pnl": {
                "grid_pnl": round(self._grid_pnl_net, 4),
                "trend_pnl": round(self._trend_pnl_net, 4),
            },
            "total_profit": round(self._engine.get_total_profit(), 4),
            "bar_counts": dict(self._bar_counts),
        }

    def close_all(self, reason: str = "shutdown") -> list[GridSignalEvent]:
        """Close all active grids (for shutdown / kill switch)."""
        signals: list[GridSignalEvent] = []
        for grid in list(self._engine.active_grids):
            signals.extend(self._engine.close_grid(grid.grid_id, reason=reason))
        for symbol, position in list(self._trend_positions.items()):
            last_price = self._last_prices.get(symbol, position.entry_price)
            shutdown_event = MarketEvent(
                timestamp=self._clock.now(),
                symbol=symbol,
                timeframe=self._cfg.timeframe,
                open=last_price,
                high=last_price,
                low=last_price,
                close=last_price,
                volume=0.0,
                source=reason,
            )
            self._close_trend_position(position, last_price, reason, shutdown_event)
        return signals
