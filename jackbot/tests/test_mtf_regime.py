"""Unit tests for t1-mtf-regime.

Verifies the higher-TF ADX state populates from 5m bars, and that conflict
detection downgrades direction/leverage/confidence as specified. With
mtf_enabled=False, behaviour is identical to legacy MarketAssessor.
"""

from __future__ import annotations

from jackbot.core.constants import GridDirection, Regime
from jackbot.strategy.market_assessor import MarketAssessor


def _feed_bars(assessor: MarketAssessor, symbol: str, n: int, builder) -> None:
    for i in range(n):
        h, l, c = builder(i)
        assessor.update(symbol, h, l, c)


class TestMTFAccumulation:
    def test_disabled_does_not_advance_htf_state(self):
        a = MarketAssessor(mtf_enabled=False, mtf_higher_tf_bars=12)
        _feed_bars(a, "BTCUSDT", 60, lambda i: (95000 + i, 94900 + i, 94950 + i))
        # No higher-TF bars consumed
        assert a._htf_state.get("BTCUSDT", {}).get("bar_count", 0) == 0

    def test_enabled_rolls_up_every_n_bars(self):
        a = MarketAssessor(mtf_enabled=True, mtf_higher_tf_bars=12)
        _feed_bars(a, "BTCUSDT", 60, lambda i: (95000 + i, 94900 + i, 94950 + i))
        # 60 / 12 = 5 higher-TF bars consumed
        assert a._htf_state["BTCUSDT"]["bar_count"] == 5

    def test_buffer_resets_after_emit(self):
        a = MarketAssessor(mtf_enabled=True, mtf_higher_tf_bars=4)
        _feed_bars(a, "BTCUSDT", 4, lambda i: (95000 + i, 94900 + i, 94950 + i))
        # Buffer just emitted; count is 0
        assert a._htf_buffer["BTCUSDT"]["count"] == 0
        # Higher-TF state advanced
        assert a._htf_state["BTCUSDT"]["bar_count"] == 1


class TestMTFConflict:
    def test_no_conflict_when_aligned(self):
        a = MarketAssessor(mtf_enabled=True)
        assert a._detect_regime_conflict(
            Regime.TRENDING, GridDirection.LONG,
            Regime.TRENDING, GridDirection.LONG,
        ) is False

    def test_conflict_5m_trending_htf_ranging(self):
        a = MarketAssessor(mtf_enabled=True)
        assert a._detect_regime_conflict(
            Regime.TRENDING, GridDirection.LONG,
            Regime.RANGING, GridDirection.NEUTRAL,
        ) is True

    def test_conflict_opposite_directions(self):
        a = MarketAssessor(mtf_enabled=True)
        assert a._detect_regime_conflict(
            Regime.NEUTRAL, GridDirection.LONG,
            Regime.NEUTRAL, GridDirection.SHORT,
        ) is True

    def test_no_conflict_when_either_neutral(self):
        a = MarketAssessor(mtf_enabled=True)
        # 5m neutral, htf trending — no conflict (5m is just unsure)
        assert a._detect_regime_conflict(
            Regime.NEUTRAL, GridDirection.NEUTRAL,
            Regime.TRENDING, GridDirection.LONG,
        ) is False
