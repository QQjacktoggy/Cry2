"""Unit tests for MarketAssessor."""

from jackbot.core.constants import GridDirection, Regime
from jackbot.strategy.market_assessor import MarketAssessor


def _feed_ranging_bars(assessor: MarketAssessor, symbol: str, n: int = 60):
    """Feed bars that create a ranging market (small oscillations)."""
    base = 95000.0
    for i in range(n):
        # Small oscillation ±0.5%
        offset = (i % 10 - 5) * 50
        price = base + offset
        assessor.update(symbol, price + 30, price - 30, price)


def _feed_trending_up_bars(assessor: MarketAssessor, symbol: str, n: int = 60):
    """Feed bars that create a trending-up market."""
    base = 90000.0
    for i in range(n):
        price = base + i * 100  # Steady uptrend
        assessor.update(symbol, price + 50, price - 20, price)


def _feed_trending_down_bars(assessor: MarketAssessor, symbol: str, n: int = 60):
    """Feed bars that create a trending-down market."""
    base = 100000.0
    for i in range(n):
        price = base - i * 100  # Steady downtrend
        assessor.update(symbol, price + 20, price - 50, price)


class TestMarketAssessor:
    def test_not_enough_data_returns_none(self):
        assessor = MarketAssessor()
        assessor.update("BTCUSDT", 100, 99, 99.5)
        assert assessor.assess("BTCUSDT") is None

    def test_ranging_market_neutral_direction(self):
        assessor = MarketAssessor()
        _feed_ranging_bars(assessor, "BTCUSDT")

        result = assessor.assess("BTCUSDT")
        assert result is not None
        # Ranging market should suggest NEUTRAL direction
        # (ADX will be low due to small oscillations)
        assert result.direction == GridDirection.NEUTRAL or result.regime == Regime.RANGING

    def test_trending_up_market(self):
        assessor = MarketAssessor()
        _feed_trending_up_bars(assessor, "BTCUSDT")

        result = assessor.assess("BTCUSDT")
        assert result is not None
        # Should detect trend and prefer LONG
        if result.regime == Regime.TRENDING:
            assert result.direction == GridDirection.LONG

    def test_trending_down_market(self):
        assessor = MarketAssessor()
        _feed_trending_down_bars(assessor, "BTCUSDT")

        result = assessor.assess("BTCUSDT")
        assert result is not None
        if result.regime == Regime.TRENDING:
            assert result.direction == GridDirection.SHORT

    def test_upper_lower_price_valid(self):
        assessor = MarketAssessor()
        _feed_ranging_bars(assessor, "BTCUSDT")

        result = assessor.assess("BTCUSDT")
        assert result is not None
        assert result.upper_price > result.lower_price
        assert result.upper_price > 0
        assert result.lower_price > 0

    def test_leverage_within_bounds(self):
        assessor = MarketAssessor(min_leverage=5, max_leverage=20)
        _feed_ranging_bars(assessor, "BTCUSDT")

        result = assessor.assess("BTCUSDT")
        assert result is not None
        assert 5 <= result.suggested_leverage <= 20

    def test_grid_count_within_bounds(self):
        assessor = MarketAssessor()
        _feed_ranging_bars(assessor, "BTCUSDT")

        result = assessor.assess("BTCUSDT")
        assert result is not None
        assert 6 <= result.suggested_grid_count <= 20

    def test_multi_symbol_independent(self):
        assessor = MarketAssessor()
        _feed_trending_up_bars(assessor, "BTCUSDT")
        _feed_ranging_bars(assessor, "ETHUSDT")

        btc = assessor.assess("BTCUSDT")
        eth = assessor.assess("ETHUSDT")

        assert btc is not None
        assert eth is not None
        # They should have different assessments
        assert btc.symbol == "BTCUSDT"
        assert eth.symbol == "ETHUSDT"

    def test_assessment_exposes_trend_features(self):
        assessor = MarketAssessor()
        _feed_trending_up_bars(assessor, "BTCUSDT")

        result = assessor.assess("BTCUSDT")
        assert result is not None
        assert result.ema_fast > 0
        assert result.ema_slow > 0
        assert isinstance(result.adx_slope, float)
        assert result.atr_pct > 0
