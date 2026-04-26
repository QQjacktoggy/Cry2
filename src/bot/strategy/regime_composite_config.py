"""Configuration dataclass for the regime-aware composite futures strategy."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RegimeCompositeConfig:
    """Parameters for RegimeCompositeLiveStrategy.

    Aggressive mode is active until daily realized PnL reaches
    daily_profit_target_usd, after which conservative mode kicks in for the
    remainder of the UTC day.
    """

    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    timeframe: str = "15m"

    # Daily profit target (0 = disabled)
    daily_profit_target_usd: float = 20.0

    # Conservative mode parameters (same strategy, safer profile)
    conservative_size_factor: float = 0.25
    conservative_leverage: int = 1

    # Aggressive leverage per regime
    aggressive_leverage_trending: int = 7
    aggressive_leverage_ranging: int = 3
    aggressive_leverage_neutral: int = 2

    # Skip opening new entries in NEUTRAL if True; still allow exits
    skip_entries_in_volatile: bool = True

    # Hard per-trade stop-loss percentage (1.5 = 1.5%)
    max_sl_pct: float = 1.5

    # Total capital allocated to this strategy across all symbols
    allocation_usd: float = 110.0

    # Bars needed before generating live signals
    warmup: int = 200

    @classmethod
    def from_dict(cls, d: dict) -> "RegimeCompositeConfig":
        """Build config from a plain dict (e.g. from YAML strategies block)."""
        return cls(
            symbols=d.get("symbols", ["BTCUSDT", "ETHUSDT"]),
            timeframe=d.get("timeframe", "15m"),
            daily_profit_target_usd=float(d.get("daily_profit_target_usd", 20.0)),
            conservative_size_factor=float(d.get("conservative_size_factor", 0.25)),
            conservative_leverage=int(d.get("conservative_leverage", 1)),
            aggressive_leverage_trending=int(d.get("aggressive_leverage_trending", 7)),
            aggressive_leverage_ranging=int(d.get("aggressive_leverage_ranging", 3)),
            aggressive_leverage_neutral=int(d.get("aggressive_leverage_neutral", 2)),
            skip_entries_in_volatile=bool(d.get("skip_entries_in_volatile", True)),
            max_sl_pct=float(d.get("max_sl_pct", 1.5)),
            allocation_usd=float(d.get("allocation_usd", 110.0)),
            warmup=int(d.get("warmup", 200)),
        )
