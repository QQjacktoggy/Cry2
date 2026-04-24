"""Regime-aware composite live strategy with daily profit target gate.

Market regime is detected by the shared RiskManager's ADX-based
RegimeDetector and routes to:
  TRENDING  → TrendDonchianMTF   (aggressive_leverage_trending, default 7x)
  RANGING   → MeanReversionBBVBT (aggressive_leverage_ranging,  default 3x)
  NEUTRAL   → MeanReversionBBVBT (aggressive_leverage_neutral,  default 2x)

Once daily realized PnL >= daily_profit_target_usd (as reported by the
RiskManager's conservative_mode flag), all new non-exit signals are
downscaled to conservative_leverage (1x) and conservative_size_factor (0.25)
for the rest of the UTC day.  The same underlying sub-strategy continues to
run — no strategy swap is needed.

A hard stop-loss metadata tag (max_sl_pct) is attached to every entry signal
so the executor can place a companion STOP_MARKET order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from bot.core.constants import OrderSide
from bot.core.events import FillEvent, MarketEvent, SignalEvent
from bot.strategy.base import BaseStrategy
from bot.strategy.bridge import VBTBridgeStrategy
from bot.strategy.regime_composite_config import RegimeCompositeConfig

if TYPE_CHECKING:
    from bot.risk.risk_manager import RiskManager
    from bot.risk.regime_detector import Regime

logger = structlog.get_logger(__name__)

# Per-symbol allocation split (even across symbols)
def _alloc_per_symbol(total: float, n: int) -> float:
    return total / max(n, 1)


class RegimeCompositeLiveStrategy(BaseStrategy):
    """Live event-driven regime-composite strategy.

    One instance manages all configured symbols on the same timeframe.
    Internally it maintains separate VBTBridgeStrategy instances per symbol
    for each sub-strategy family so they accumulate independent bar histories.
    """

    name = "regime_composite"

    def __init__(
        self,
        risk_manager: RiskManager,
        config: RegimeCompositeConfig | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        cfg = config or RegimeCompositeConfig.from_dict(params or {})
        # _cfg must be set before super().__init__() because the parent accesses
        # self.symbols (a property defined below) during its own init.
        self._cfg = cfg
        super().__init__({
            "symbols": cfg.symbols,
            "timeframe": cfg.timeframe,
            "leverage": cfg.aggressive_leverage_trending,
        })
        self._risk_manager = risk_manager
        self.name = "regime_composite"

        alloc = _alloc_per_symbol(cfg.allocation_usd, len(cfg.symbols))

        # One bridge pair per symbol: (trend_bridge, mr_bridge)
        self._trend_bridges: dict[str, VBTBridgeStrategy] = {}
        self._mr_bridges: dict[str, VBTBridgeStrategy] = {}

        for sym in cfg.symbols:
            self._trend_bridges[sym] = VBTBridgeStrategy(
                vbt_strategy_name="trend_donchian_mtf",
                params={
                    "entry_period": 10,
                    "exit_period": 10,
                    "adx_threshold": 15,
                    "htf_period": 150,
                    "leverage": cfg.aggressive_leverage_trending,
                },
                symbol=sym,
                timeframe=cfg.timeframe,
                warmup=cfg.warmup,
                allocation_usd=alloc,
            )
            self._trend_bridges[sym].name = f"rc_trend_{sym.replace('USDT', '').lower()}"

            self._mr_bridges[sym] = VBTBridgeStrategy(
                vbt_strategy_name="mean_reversion_bb",
                params={
                    "bb_period": 20,
                    "bb_std": 2.0,
                    "rsi_period": 14,
                    "rsi_oversold": 35,
                    "rsi_overbought": 65,
                    "sl_stop": cfg.max_sl_pct / 100.0,
                    "leverage": cfg.aggressive_leverage_ranging,
                },
                symbol=sym,
                timeframe=cfg.timeframe,
                warmup=cfg.warmup,
                allocation_usd=alloc,
            )
            self._mr_bridges[sym].name = f"rc_mr_{sym.replace('USDT', '').lower()}"

        logger.info(
            "regime_composite_created",
            symbols=cfg.symbols,
            timeframe=cfg.timeframe,
            daily_target=cfg.daily_profit_target_usd,
            allocation=cfg.allocation_usd,
        )

    # ── BaseStrategy interface ────────────────────────────────────────────

    def on_bar(self, event: MarketEvent) -> list[SignalEvent]:
        sym = event.symbol
        if sym not in self._cfg.symbols:
            return []

        regime = self._risk_manager.regime
        conservative = self._risk_manager.conservative_mode

        bridge = self._select_bridge(sym, regime)
        if bridge is None:
            return []

        raw_signals = bridge.on_bar(event)
        if not raw_signals:
            return []

        result: list[SignalEvent] = []
        for sig in raw_signals:
            processed = self._apply_mode_override(sig, regime, conservative)
            if processed is not None:
                result.append(processed)

        return result

    def on_fill(self, event: FillEvent) -> None:
        super().on_fill(event)
        sym = event.symbol
        for bridges in (self._trend_bridges, self._mr_bridges):
            if sym in bridges:
                bridges[sym].on_fill(event)

    def warmup_bars(self) -> int:
        return self._cfg.warmup

    # ── Properties for observability ─────────────────────────────────────

    @property
    def symbol(self) -> str:
        """Primary symbol (for health/status display compatibility)."""
        return self.symbols[0] if self.symbols else ""

    @property
    def _allocation_usd(self) -> float:
        return self._cfg.allocation_usd

    # ── Internal helpers ──────────────────────────────────────────────────

    def _select_bridge(self, symbol: str, regime: "Regime") -> VBTBridgeStrategy | None:
        from bot.risk.regime_detector import Regime

        if regime == Regime.TRENDING:
            return self._trend_bridges.get(symbol)

        if regime in (Regime.RANGING, Regime.NEUTRAL):
            if self._cfg.skip_entries_in_volatile and regime == Regime.NEUTRAL:
                # Allow exits but gate new entries via _apply_mode_override
                return self._mr_bridges.get(symbol)
            return self._mr_bridges.get(symbol)

        return None

    def _apply_mode_override(
        self,
        signal: SignalEvent,
        regime: "Regime",
        conservative: bool,
    ) -> SignalEvent | None:
        from bot.risk.regime_detector import Regime

        # Always allow exits (reduce-only); only gate new entries
        if signal.reduce_only:
            return signal

        # Skip new entries in NEUTRAL if configured
        if (
            self._cfg.skip_entries_in_volatile
            and regime == Regime.NEUTRAL
            and not signal.reduce_only
        ):
            logger.debug("regime_neutral_entry_skipped", symbol=signal.symbol)
            return None

        # Determine leverage and quantity multiplier
        if conservative:
            leverage = self._cfg.conservative_leverage
            size_factor = self._cfg.conservative_size_factor
        else:
            if regime == Regime.TRENDING:
                leverage = self._cfg.aggressive_leverage_trending
            elif regime == Regime.RANGING:
                leverage = self._cfg.aggressive_leverage_ranging
            else:
                leverage = self._cfg.aggressive_leverage_neutral
            size_factor = 1.0

        new_quantity = signal.quantity * size_factor

        new_metadata = dict(signal.metadata)
        new_metadata["requested_leverage"] = float(leverage)
        new_metadata["max_sl_pct"] = self._cfg.max_sl_pct
        new_metadata["conservative_mode"] = conservative
        new_metadata["regime"] = regime.value if hasattr(regime, "value") else str(regime)

        # Rebuild with overridden fields (SignalEvent is frozen Pydantic model)
        return signal.model_copy(update={
            "quantity": new_quantity,
            "metadata": new_metadata,
            "strategy_name": self.name,
        })

    def restore_position_state(self, position_state: str) -> None:
        """Restore position state across all sub-bridges."""
        for bridges in (self._trend_bridges, self._mr_bridges):
            for bridge in bridges.values():
                bridge.restore_position_state(position_state)
