"""Regime Detector — classifies market as trending or ranging using ADX.

Trending markets (ADX > trending_threshold) favour trend-following strategies.
Ranging markets (ADX < ranging_threshold) favour mean-reversion strategies.

The detector is bar-driven: call update(high, low, close) on every new bar,
then read the .regime property.

Default thresholds (calibrated on BTC/ETH 4h data):
  ADX > 25 → TRENDING
  ADX < 20 → RANGING
  20 ≤ ADX ≤ 25 → NEUTRAL (transitions)
"""

from __future__ import annotations

from collections import deque
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class Regime(str, Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    NEUTRAL = "neutral"


class RegimeDetector:
    """ADX-based market regime detector.

    Args:
        period: ADX / DI smoothing period (default 14).
        trending_threshold: ADX level above which market is trending (default 25).
        ranging_threshold: ADX level below which market is ranging (default 20).
    """

    def __init__(
        self,
        period: int = 14,
        trending_threshold: float = 25.0,
        ranging_threshold: float = 20.0,
    ) -> None:
        if ranging_threshold >= trending_threshold:
            raise ValueError("ranging_threshold must be < trending_threshold")

        self.period = period
        self.trending_threshold = trending_threshold
        self.ranging_threshold = ranging_threshold

        # Raw OHLC history (need period+1 bars minimum)
        self._highs: deque[float] = deque(maxlen=period + 2)
        self._lows: deque[float] = deque(maxlen=period + 2)
        self._closes: deque[float] = deque(maxlen=period + 2)

        # Smoothed DM and TR accumulators (Wilder smoothing)
        self._smooth_plus_dm: float = 0.0
        self._smooth_minus_dm: float = 0.0
        self._smooth_tr: float = 0.0

        # ADX smoothing accumulator
        self._adx: float = 0.0
        self._prev_dx: float | None = None

        self._bar_count: int = 0
        self._regime: Regime = Regime.NEUTRAL
        self._adx_value: float = 0.0

    # ── Public interface ─────────────────────────────────────────────────

    @property
    def regime(self) -> Regime:
        return self._regime

    @property
    def adx(self) -> float:
        return self._adx_value

    @property
    def is_trending(self) -> bool:
        return self._regime == Regime.TRENDING

    @property
    def is_ranging(self) -> bool:
        return self._regime == Regime.RANGING

    def update(self, high: float, low: float, close: float) -> Regime:
        """Feed one new bar and return the updated regime."""
        self._highs.append(high)
        self._lows.append(low)
        self._closes.append(close)
        self._bar_count += 1

        if self._bar_count < 2:
            return self._regime

        h, l, c = high, low, close
        ph, pl, pc = (
            self._highs[-2],
            self._lows[-2],
            self._closes[-2],
        )

        # True Range
        tr = max(h - l, abs(h - pc), abs(l - pc))

        # Directional Movement
        up_move = h - ph
        down_move = pl - l
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        if self._bar_count <= self.period:
            # Accumulate for initial SMA seed
            self._smooth_tr += tr
            self._smooth_plus_dm += plus_dm
            self._smooth_minus_dm += minus_dm
            if self._bar_count == self.period:
                self._adx_value = self._compute_adx_initial()
            return self._regime

        # Wilder smoothing
        self._smooth_tr = self._smooth_tr - self._smooth_tr / self.period + tr
        self._smooth_plus_dm = (
            self._smooth_plus_dm - self._smooth_plus_dm / self.period + plus_dm
        )
        self._smooth_minus_dm = (
            self._smooth_minus_dm - self._smooth_minus_dm / self.period + minus_dm
        )

        # DI lines
        plus_di = (
            100 * self._smooth_plus_dm / self._smooth_tr
            if self._smooth_tr > 0
            else 0.0
        )
        minus_di = (
            100 * self._smooth_minus_dm / self._smooth_tr
            if self._smooth_tr > 0
            else 0.0
        )

        # DX
        di_sum = plus_di + minus_di
        dx = 100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0

        # ADX = Wilder-smoothed DX
        if self._adx_value == 0.0:
            self._adx_value = dx
        else:
            self._adx_value = (self._adx_value * (self.period - 1) + dx) / self.period

        self._update_regime()
        return self._regime

    def get_status(self) -> dict[str, Any]:
        return {
            "regime": self._regime.value,
            "adx": round(self._adx_value, 2),
            "trending_threshold": self.trending_threshold,
            "ranging_threshold": self.ranging_threshold,
            "bars_seen": self._bar_count,
        }

    # ── Private helpers ──────────────────────────────────────────────────

    def _compute_adx_initial(self) -> float:
        """Compute first ADX value from the seeded accumulators."""
        plus_di = (
            100 * self._smooth_plus_dm / self._smooth_tr
            if self._smooth_tr > 0 else 0.0
        )
        minus_di = (
            100 * self._smooth_minus_dm / self._smooth_tr
            if self._smooth_tr > 0 else 0.0
        )
        di_sum = plus_di + minus_di
        dx = 100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
        return dx

    def _update_regime(self) -> None:
        prev = self._regime
        if self._adx_value >= self.trending_threshold:
            self._regime = Regime.TRENDING
        elif self._adx_value <= self.ranging_threshold:
            self._regime = Regime.RANGING
        else:
            self._regime = Regime.NEUTRAL

        if self._regime != prev:
            logger.info(
                "regime_change",
                prev=prev.value,
                new=self._regime.value,
                adx=round(self._adx_value, 2),
            )
