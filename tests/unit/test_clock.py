"""Tests for clock implementations."""

from datetime import datetime, timezone

from bot.core.clock import SimClock, RealClock


class TestSimClock:
    def test_initial_time(self, sim_clock):
        assert sim_clock.now_ms() == 1704067200000

    def test_set_time(self, sim_clock):
        sim_clock.set_time(1704153600000)
        assert sim_clock.now_ms() == 1704153600000

    def test_advance(self, sim_clock):
        initial = sim_clock.now_ms()
        sim_clock.advance(60000)  # 1 minute
        assert sim_clock.now_ms() == initial + 60000

    def test_sleep_advances(self, sim_clock):
        initial = sim_clock.now_ms()
        sim_clock.sleep(5.0)  # 5 seconds
        assert sim_clock.now_ms() == initial + 5000

    def test_now_returns_datetime(self, sim_clock):
        dt = sim_clock.now()
        assert isinstance(dt, datetime)
        assert dt.tzinfo == timezone.utc


class TestRealClock:
    def test_now_returns_utc(self, real_clock):
        dt = real_clock.now()
        assert dt.tzinfo == timezone.utc

    def test_now_ms_is_positive(self, real_clock):
        assert real_clock.now_ms() > 0
