"""Market Assessor — evaluates market conditions to decide grid direction & range.

Combines ADX (trend strength), Bollinger Bands (price range), and ATR (volatility)
to produce an actionable MarketAssessment for the GridEngine.

Logic ported from cry2/risk/regime_detector.py (ADX) and
cry/strategy/signals.py (multi-indicator scoring).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import structlog

from jackbot.core.constants import GridDirection, Regime

logger = structlog.get_logger(__name__)


@dataclass
class MarketAssessment:
    """Output of a market evaluation for one symbol."""

    symbol: str
    direction: GridDirection
    regime: Regime
    upper_price: float
    lower_price: float
    current_price: float
    adx: float
    atr: float
    confidence: float                  # 0.0 ~ 1.0
    suggested_grid_count: int
    suggested_leverage: int


class MarketAssessor:
    """Evaluates market state and recommends grid parameters.

    Uses:
      - ADX (Wilder) for trend vs ranging classification
      - +DI / -DI for directional bias
      - Bollinger Bands for price range boundaries
      - ATR for volatility-adaptive grid spacing and leverage
    """

    def __init__(
        self,
        adx_period: int = 14,
        trending_threshold: float = 25.0,
        ranging_threshold: float = 20.0,
        bb_period: int = 20,
        bb_std: float = 2.0,
        atr_period: int = 14,
        max_leverage: int = 20,
        min_leverage: int = 5,
        default_grid_count: int = 10,
        mtf_enabled: bool = False,
        mtf_higher_tf_bars: int = 12,           # 12 × 5m = 1h
        mtf_conflict_confidence_mult: float = 0.5,
    ) -> None:
        self._adx_period = adx_period
        self._trending_threshold = trending_threshold
        self._ranging_threshold = ranging_threshold
        self._bb_period = bb_period
        self._bb_std = bb_std
        self._atr_period = atr_period
        self._max_leverage = max_leverage
        self._min_leverage = min_leverage
        self._default_grid_count = default_grid_count
        self._mtf_enabled = mtf_enabled
        self._mtf_higher_tf_bars = mtf_higher_tf_bars
        self._mtf_conflict_confidence_mult = mtf_conflict_confidence_mult

        # Per-symbol bar history
        self._bars: dict[str, deque[dict]] = {}
        # ADX internals per symbol (5m primary timeframe)
        self._adx_state: dict[str, dict] = {}
        # Higher-TF accumulator + ADX state (used only when mtf_enabled)
        self._htf_buffer: dict[str, dict] = {}
        self._htf_state: dict[str, dict] = {}

    def update(self, symbol: str, high: float, low: float, close: float) -> None:
        """Feed one new bar for a symbol."""
        if symbol not in self._bars:
            self._bars[symbol] = deque(maxlen=300)
            self._adx_state[symbol] = self._fresh_adx_state()
            self._htf_state[symbol] = self._fresh_adx_state()
            self._htf_buffer[symbol] = {"high": high, "low": low, "close": close, "count": 0}

        self._bars[symbol].append({"high": high, "low": low, "close": close})
        self._update_adx(symbol, high, low, close, self._adx_state[symbol])

        if self._mtf_enabled:
            self._accumulate_higher_tf(symbol, high, low, close)

    def _fresh_adx_state(self) -> dict:
        return {
            "smooth_plus_dm": 0.0,
            "smooth_minus_dm": 0.0,
            "smooth_tr": 0.0,
            "adx": 0.0,
            "bar_count": 0,
            "prev_high": None,
            "prev_low": None,
            "prev_close": None,
            "plus_di": 0.0,
            "minus_di": 0.0,
        }

    def _accumulate_higher_tf(self, symbol: str, high: float, low: float, close: float) -> None:
        """Roll up 5m bars into a synthetic higher-TF bar; feed to _htf_state on close."""
        buf = self._htf_buffer[symbol]
        if buf["count"] == 0:
            buf["high"] = high
            buf["low"] = low
        else:
            buf["high"] = max(buf["high"], high)
            buf["low"] = min(buf["low"], low)
        buf["close"] = close
        buf["count"] += 1

        if buf["count"] >= self._mtf_higher_tf_bars:
            self._update_adx(symbol, buf["high"], buf["low"], buf["close"], self._htf_state[symbol])
            buf["high"] = close
            buf["low"] = close
            buf["close"] = close
            buf["count"] = 0

    def assess(self, symbol: str) -> MarketAssessment | None:
        """Produce a market assessment for the given symbol.

        Returns None if not enough data has accumulated.
        """
        bars = self._bars.get(symbol)
        if bars is None or len(bars) < max(self._bb_period, self._atr_period, self._adx_period) + 5:
            return None

        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        current_price = closes[-1]

        # Bollinger Bands → price range
        bb_upper, bb_middle, bb_lower = self._calc_bb(closes)

        # ATR → volatility
        atr = self._calc_atr(highs, lows, closes)

        # ADX + DI → regime & direction
        state = self._adx_state[symbol]
        adx_val = state["adx"]
        plus_di = state["plus_di"]
        minus_di = state["minus_di"]

        regime = self._classify_regime(adx_val)
        direction = self._determine_direction(regime, plus_di, minus_di)

        # Grid range: use BB bands, widened by a fraction of ATR
        atr_pad = atr * 0.5
        upper_price = bb_upper + atr_pad
        lower_price = max(bb_lower - atr_pad, current_price * 0.95)  # floor at -5%

        # Ensure range is valid
        if upper_price <= lower_price:
            upper_price = current_price * 1.03
            lower_price = current_price * 0.97

        # Leverage: inverse of volatility (high vol → low leverage)
        leverage = self._calc_leverage(atr, current_price)

        # Grid count: tighter range → fewer grids
        range_pct = (upper_price - lower_price) / current_price * 100
        grid_count = self._calc_grid_count(range_pct)

        # Confidence: higher ADX + narrower BB → higher confidence
        confidence = min(1.0, adx_val / 40.0) * 0.6 + min(1.0, range_pct / 5.0) * 0.4

        # t1-mtf-regime: if higher-TF disagrees, downgrade
        if self._mtf_enabled:
            htf = self._htf_state.get(symbol)
            if htf and htf["bar_count"] >= self._adx_period:
                htf_regime = self._classify_regime(htf["adx"])
                htf_direction = self._determine_direction(
                    htf_regime, htf["plus_di"], htf["minus_di"],
                )
                conflict = self._detect_regime_conflict(
                    regime, direction, htf_regime, htf_direction,
                )
                if conflict:
                    confidence *= self._mtf_conflict_confidence_mult
                    direction = GridDirection.NEUTRAL
                    leverage = self._min_leverage
                    logger.info(
                        "mtf_conflict_downgrade",
                        symbol=symbol,
                        five_m=f"{regime.value}/{direction.value}",
                        higher_tf=f"{htf_regime.value}/{htf_direction.value}",
                    )

        assessment = MarketAssessment(
            symbol=symbol,
            direction=direction,
            regime=regime,
            upper_price=round(upper_price, 2),
            lower_price=round(lower_price, 2),
            current_price=current_price,
            adx=round(adx_val, 2),
            atr=round(atr, 4),
            confidence=round(confidence, 3),
            suggested_grid_count=grid_count,
            suggested_leverage=leverage,
        )

        logger.debug(
            "market_assessment",
            symbol=symbol,
            direction=direction.value,
            regime=regime.value,
            adx=round(adx_val, 1),
            leverage=leverage,
            grids=grid_count,
            range_pct=f"{range_pct:.2f}%",
        )

        return assessment

    # ── ADX calculation (Wilder smoothing, ported from cry2) ──────────

    def _update_adx(
        self,
        symbol: str,
        high: float,
        low: float,
        close: float,
        s: dict | None = None,
    ) -> None:
        if s is None:
            s = self._adx_state[symbol]
        s["bar_count"] += 1
        period = self._adx_period

        if s["prev_high"] is None:
            s["prev_high"] = high
            s["prev_low"] = low
            s["prev_close"] = close
            return

        # True Range
        tr = max(high - low, abs(high - s["prev_close"]), abs(low - s["prev_close"]))

        # Directional Movement
        up_move = high - s["prev_high"]
        down_move = s["prev_low"] - low
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        s["prev_high"] = high
        s["prev_low"] = low
        s["prev_close"] = close

        if s["bar_count"] <= period:
            s["smooth_tr"] += tr
            s["smooth_plus_dm"] += plus_dm
            s["smooth_minus_dm"] += minus_dm
            if s["bar_count"] == period:
                self._compute_di(s)
            return

        # Wilder smoothing
        s["smooth_tr"] = s["smooth_tr"] - s["smooth_tr"] / period + tr
        s["smooth_plus_dm"] = s["smooth_plus_dm"] - s["smooth_plus_dm"] / period + plus_dm
        s["smooth_minus_dm"] = s["smooth_minus_dm"] - s["smooth_minus_dm"] / period + minus_dm

        self._compute_di(s)

        # DX → ADX
        di_sum = s["plus_di"] + s["minus_di"]
        dx = 100 * abs(s["plus_di"] - s["minus_di"]) / di_sum if di_sum > 0 else 0.0

        if s["adx"] == 0.0:
            s["adx"] = dx
        else:
            s["adx"] = (s["adx"] * (period - 1) + dx) / period

    def _compute_di(self, s: dict) -> None:
        if s["smooth_tr"] > 0:
            s["plus_di"] = 100 * s["smooth_plus_dm"] / s["smooth_tr"]
            s["minus_di"] = 100 * s["smooth_minus_dm"] / s["smooth_tr"]

    # ── Bollinger Bands ──────────────────────────────────────────────

    def _calc_bb(self, closes: list[float]) -> tuple[float, float, float]:
        window = closes[-self._bb_period:]
        mean = sum(window) / len(window)
        variance = sum((x - mean) ** 2 for x in window) / len(window)
        std = variance ** 0.5
        upper = mean + self._bb_std * std
        lower = mean - self._bb_std * std
        return upper, mean, lower

    # ── ATR ───────────────────────────────────────────────────────────

    def _calc_atr(self, highs: list[float], lows: list[float], closes: list[float]) -> float:
        period = self._atr_period
        n = len(closes)
        if n < period + 1:
            return max(highs[-1] - lows[-1], 0.001)

        trs: list[float] = []
        for i in range(1, n):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)

        # Simple moving average of last `period` TRs
        return sum(trs[-period:]) / period

    # ── Decision helpers ─────────────────────────────────────────────

    def _detect_regime_conflict(
        self,
        five_m_regime: Regime,
        five_m_direction: GridDirection,
        htf_regime: Regime,
        htf_direction: GridDirection,
    ) -> bool:
        """True when the higher-TF context contradicts the 5m signal.

        Two flagged cases:
          1. 5m says TRENDING but higher-TF says RANGING → likely false breakout.
          2. 5m and higher-TF point to opposing directions (LONG vs SHORT).
        """
        if five_m_regime == Regime.TRENDING and htf_regime == Regime.RANGING:
            return True
        if (
            five_m_direction == GridDirection.LONG
            and htf_direction == GridDirection.SHORT
        ) or (
            five_m_direction == GridDirection.SHORT
            and htf_direction == GridDirection.LONG
        ):
            return True
        return False

    def _classify_regime(self, adx: float) -> Regime:
        if adx >= self._trending_threshold:
            return Regime.TRENDING
        if adx <= self._ranging_threshold:
            return Regime.RANGING
        return Regime.NEUTRAL

    def _determine_direction(self, regime: Regime, plus_di: float, minus_di: float) -> GridDirection:
        if regime == Regime.RANGING:
            return GridDirection.NEUTRAL

        if regime == Regime.TRENDING:
            if plus_di > minus_di * 1.2:
                return GridDirection.LONG
            elif minus_di > plus_di * 1.2:
                return GridDirection.SHORT
            return GridDirection.NEUTRAL

        # NEUTRAL regime → lean direction slightly if clear DI spread
        if plus_di > minus_di * 1.3:
            return GridDirection.LONG
        if minus_di > plus_di * 1.3:
            return GridDirection.SHORT
        return GridDirection.NEUTRAL

    def _calc_leverage(self, atr: float, price: float) -> int:
        """Higher volatility → lower leverage to avoid liquidation.

        For isolated margin with grid trading:
        - ATR/price > 2% → min leverage (5x)
        - ATR/price < 0.5% → max leverage (20x)
        - Linear interpolation between
        """
        if price <= 0:
            return self._min_leverage

        vol_pct = atr / price * 100

        if vol_pct >= 2.0:
            return self._min_leverage
        if vol_pct <= 0.5:
            return self._max_leverage

        # Linear interpolation: high vol → low lev
        ratio = (vol_pct - 0.5) / (2.0 - 0.5)  # 0 at 0.5%, 1 at 2%
        leverage = self._max_leverage - ratio * (self._max_leverage - self._min_leverage)
        return max(self._min_leverage, min(self._max_leverage, int(leverage)))

    def _calc_grid_count(self, range_pct: float) -> int:
        """More range → more grids, scaled off default_grid_count."""
        base_min = max(5, int(self._default_grid_count * 0.6))
        base_max = min(30, int(self._default_grid_count * 2.0))
        
        if range_pct <= 1.0:
            return base_min
        if range_pct >= 6.0:
            return base_max
            
        count = int(base_min + (range_pct - 1.0) / (6.0 - 1.0) * (base_max - base_min))
        return max(base_min, min(base_max, count))
