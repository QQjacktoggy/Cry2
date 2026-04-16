"""Tests for technical indicators."""

from bot.strategy.indicators.atr import calculate_atr
from bot.strategy.indicators.bollinger import calculate_bollinger, calculate_rsi, calculate_ema
from bot.strategy.indicators.donchian import calculate_donchian


class TestATR:
    def test_basic(self):
        highs = [10, 11, 12, 11, 13, 12, 14, 13, 15, 14, 12, 13, 14, 15, 16]
        lows = [8, 9, 10, 9, 11, 10, 12, 11, 13, 12, 10, 11, 12, 13, 14]
        closes = [9, 10, 11, 10, 12, 11, 13, 12, 14, 13, 11, 12, 13, 14, 15]
        atr = calculate_atr(highs, lows, closes, period=5)
        assert atr > 0

    def test_insufficient_data(self):
        assert calculate_atr([10, 11], [8, 9], [9, 10], period=14) == 0.0


class TestBollinger:
    def test_basic(self):
        closes = list(range(100, 121))  # 21 values
        upper, middle, lower = calculate_bollinger(closes, period=20, num_std=2.0)
        assert upper > middle > lower
        assert abs(middle - 110.5) < 0.01

    def test_insufficient_data(self):
        upper, middle, lower = calculate_bollinger([1, 2, 3], period=20)
        assert upper == middle == lower == 0.0


class TestRSI:
    def test_rising_prices(self):
        closes = list(range(50, 66))  # 16 values, all up
        rsi = calculate_rsi(closes, period=14)
        assert rsi > 80  # Should be very high

    def test_falling_prices(self):
        closes = list(range(65, 49, -1))  # 16 values, all down
        rsi = calculate_rsi(closes, period=14)
        assert rsi < 20  # Should be very low


class TestDonchian:
    def test_basic(self):
        highs = [10, 12, 11, 15, 13, 14, 16, 12, 11, 10]
        lows = [8, 9, 7, 10, 9, 11, 13, 10, 8, 7]
        upper, lower, middle = calculate_donchian(highs, lows, period=5)
        assert upper == 16.0  # max of last 5 highs
        assert lower == 7.0   # min of last 5 lows
        assert middle == (16.0 + 7.0) / 2

    def test_insufficient_data(self):
        upper, lower, middle = calculate_donchian([10], [8], period=5)
        assert upper == lower == middle == 0.0


class TestEMA:
    def test_basic(self):
        closes = [10.0, 11.0, 12.0, 11.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
        ema = calculate_ema(closes, period=5)
        assert ema > 0

    def test_insufficient_data(self):
        ema = calculate_ema([10.0, 11.0], period=5)
        assert ema == 0.0
