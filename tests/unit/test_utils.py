"""Tests for utility modules."""

from datetime import UTC, datetime

from bot.utils.id_generator import (
    extract_strategy_from_client_oid,
    generate_client_order_id,
    generate_run_id,
)
from bot.utils.math_utils import (
    clamp,
    pct_change,
    round_to_step,
    round_to_tick,
    safe_divide,
)
from bot.utils.time_utils import datetime_to_ms, ms_to_datetime


class TestMathUtils:
    def test_round_to_tick(self):
        assert round_to_tick(42123.456, 0.10) == 42123.4

    def test_round_to_step(self):
        assert round_to_step(0.12345, 0.001) == 0.123

    def test_safe_divide(self):
        assert safe_divide(10, 2) == 5.0
        assert safe_divide(10, 0) == 0.0
        assert safe_divide(10, 0, default=-1.0) == -1.0

    def test_pct_change(self):
        assert abs(pct_change(100, 110) - 10.0) < 0.001
        assert pct_change(0, 100) == 0.0

    def test_clamp(self):
        assert clamp(5, 0, 10) == 5
        assert clamp(-5, 0, 10) == 0
        assert clamp(15, 0, 10) == 10


class TestTimeUtils:
    def test_ms_to_datetime(self):
        dt = ms_to_datetime(1704067200000)
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1

    def test_datetime_to_ms(self):
        dt = datetime(2024, 1, 1, tzinfo=UTC)
        ms = datetime_to_ms(dt)
        assert ms == 1704067200000

    def test_roundtrip(self):
        original_ms = 1704067200000
        dt = ms_to_datetime(original_ms)
        result_ms = datetime_to_ms(dt)
        assert result_ms == original_ms


class TestIdGenerator:
    def test_client_order_id_format(self):
        oid = generate_client_order_id("test_strat")
        assert oid.startswith("bot_test_strat")
        assert len(oid) <= 36

    def test_extract_strategy_uses_unambiguous_prefix_match(self):
        oid = generate_client_order_id("bridge_tail_risk_hedge_sol")

        strategy = extract_strategy_from_client_oid(
            oid,
            [
                "bridge_trend_donchian_mtf_btc",
                "bridge_tail_risk_hedge_sol",
            ],
        )

        assert strategy == "bridge_tail_risk_hedge_sol"

    def test_extract_strategy_returns_raw_prefix_when_ambiguous(self):
        strategy = extract_strategy_from_client_oid(
            "bot_bridge_t_1234567890_abcd12",
            [
                "bridge_trend_donchian_mtf_btc",
                "bridge_tail_risk_hedge_sol",
            ],
        )

        assert strategy == "bridge_t"

    def test_run_id_format(self):
        rid = generate_run_id()
        assert rid.startswith("run_")

    def test_unique_ids(self):
        ids = {generate_client_order_id() for _ in range(100)}
        assert len(ids) == 100
