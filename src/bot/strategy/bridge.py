"""Strategy bridge: adapts VBT backtest strategies for live event-driven trading.

The backtest system (backtest_tool/strategies/) uses vectorized VBT strategies
that process entire DataFrames at once. The live system (src/bot/strategy/)
uses event-driven strategies that process one bar at a time.

This bridge:
1. Accumulates bars into a DataFrame as they arrive
2. Runs the VBT strategy on the accumulated data
3. Compares the latest signal state with previous state
4. Emits entry/exit signals when state changes

Usage in live system:
    from bot.strategy.bridge import create_bridged_strategy

    strategy = create_bridged_strategy(
        "momentum_ranking",   # backtest strategy name
        params={...},
        symbol="ETHUSDT",
        timeframe="1d",
    )
    # strategy now implements BaseStrategy interface
"""

from __future__ import annotations

from collections import deque
from typing import Any

import numpy as np
import pandas as pd
import structlog

from bot.core.constants import OrderSide, PositionSide
from bot.core.events import MarketEvent, SignalEvent
from bot.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)

# Import backtest strategy registry
try:
    from backtest_tool.strategies import STRATEGY_MAP as VBT_STRATEGY_MAP
except ImportError:
    VBT_STRATEGY_MAP = {}
    logger.warning("backtest_tool not importable, bridge disabled")


class VBTBridgeStrategy(BaseStrategy):
    """Bridges a VBT backtest strategy to live event-driven interface.

    Accumulates OHLCV bars, runs the VBT strategy periodically,
    and emits SignalEvents when entry/exit signals change.
    """

    def __init__(
        self,
        vbt_strategy_name: str,
        params: dict[str, Any],
        symbol: str,
        timeframe: str = "4h",
        warmup: int = 200,
        allocation_usd: float = 15.0,
    ) -> None:
        self.name = f"bridge_{vbt_strategy_name}_{symbol.replace('USDT', '').lower()}"
        super().__init__({
            "symbols": [symbol],
            "timeframe": timeframe,
            "leverage": params.get("leverage", 1),
        })

        self.vbt_strategy_name = vbt_strategy_name
        self.vbt_params = params
        self.symbol = symbol
        self._warmup = warmup
        self._allocation_usd = allocation_usd

        # Bar accumulator (max 2000 bars ≈ 333 days at 4h)
        self._bars: deque[dict] = deque(maxlen=2000)
        self._bar_count = 0

        # Signal state tracking
        self._prev_long_signal = False
        self._prev_short_signal = False
        self._in_position: str = "flat"  # "flat", "long", "short"

        # VBT strategy instance (lazy init)
        self._vbt_strategy = None

    def _init_vbt_strategy(self):
        """Lazily initialize the VBT strategy."""
        if self._vbt_strategy is None:
            cls = VBT_STRATEGY_MAP.get(self.vbt_strategy_name)
            if cls is None:
                raise ValueError(f"Unknown VBT strategy: {self.vbt_strategy_name}")
            self._vbt_strategy = cls(self.vbt_params)

    def warmup_bars(self) -> int:
        return self._warmup

    def _bars_to_df(self) -> pd.DataFrame:
        """Convert accumulated bars to DataFrame."""
        records = list(self._bars)
        df = pd.DataFrame(records)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df

    def on_bar(self, event: MarketEvent) -> list[SignalEvent]:
        """Process new bar: accumulate, run VBT strategy, emit signals."""
        self._record_bar(event)
        self._bar_count += 1

        # Accumulate bar data
        self._bars.append({
            "timestamp": event.timestamp,
            "open": event.open,
            "high": event.high,
            "low": event.low,
            "close": event.close,
            "volume": event.volume,
        })

        # Need enough bars for warmup
        if len(self._bars) < self._warmup:
            return []

        # Run VBT strategy on accumulated data
        try:
            self._init_vbt_strategy()
            ohlcv = self._bars_to_df()

            entries = self._vbt_strategy.generate_entries(ohlcv)
            exits = self._vbt_strategy.generate_exits(ohlcv)

            has_short = hasattr(self._vbt_strategy, "generate_short_entries")
            if has_short:
                short_entries = self._vbt_strategy.generate_short_entries(ohlcv)
                short_exits = self._vbt_strategy.generate_short_exits(ohlcv)
            else:
                short_entries = pd.Series(False, index=ohlcv.index)
                short_exits = pd.Series(False, index=ohlcv.index)

            # Get latest signals
            curr_long_entry = bool(entries.iloc[-1]) if len(entries) > 0 else False
            curr_long_exit = bool(exits.iloc[-1]) if len(exits) > 0 else False
            curr_short_entry = bool(short_entries.iloc[-1]) if len(short_entries) > 0 else False
            curr_short_exit = bool(short_exits.iloc[-1]) if len(short_exits) > 0 else False

        except Exception as e:
            logger.error("vbt_bridge_error", strategy=self.vbt_strategy_name, error=str(e))
            return []

        signals: list[SignalEvent] = []
        price = event.close

        # Calculate position size
        quantity = self._allocation_usd * self.leverage / price

        # Signal state machine
        if self._in_position == "flat":
            if curr_long_entry:
                signals.append(self._create_signal(
                    symbol=self.symbol,
                    side=OrderSide.BUY,
                    quantity=quantity,
                    timestamp=event.timestamp,
                    reason=f"bridge_long_entry_{self.vbt_strategy_name}",
                ))
                self._in_position = "long"
                logger.info("bridge_long_entry",
                            strategy=self.vbt_strategy_name,
                            symbol=self.symbol, price=price)

            elif curr_short_entry:
                signals.append(self._create_signal(
                    symbol=self.symbol,
                    side=OrderSide.SELL,
                    quantity=quantity,
                    timestamp=event.timestamp,
                    reason=f"bridge_short_entry_{self.vbt_strategy_name}",
                ))
                self._in_position = "short"
                logger.info("bridge_short_entry",
                            strategy=self.vbt_strategy_name,
                            symbol=self.symbol, price=price)

        elif self._in_position == "long":
            if curr_long_exit or curr_short_entry:
                # Close long
                pos = self._get_position(self.symbol)
                close_qty = pos.quantity if pos.quantity > 0 else quantity
                signals.append(self._create_signal(
                    symbol=self.symbol,
                    side=OrderSide.SELL,
                    quantity=close_qty,
                    timestamp=event.timestamp,
                    reduce_only=True,
                    reason=f"bridge_long_exit_{self.vbt_strategy_name}",
                ))
                logger.info("bridge_long_exit",
                            strategy=self.vbt_strategy_name,
                            symbol=self.symbol, price=price)

                if curr_short_entry:
                    signals.append(self._create_signal(
                        symbol=self.symbol,
                        side=OrderSide.SELL,
                        quantity=quantity,
                        timestamp=event.timestamp,
                        reason=f"bridge_short_entry_{self.vbt_strategy_name}",
                    ))
                    self._in_position = "short"
                else:
                    self._in_position = "flat"

        elif self._in_position == "short":
            if curr_short_exit or curr_long_entry:
                # Close short
                pos = self._get_position(self.symbol)
                close_qty = pos.quantity if pos.quantity > 0 else quantity
                signals.append(self._create_signal(
                    symbol=self.symbol,
                    side=OrderSide.BUY,
                    quantity=close_qty,
                    timestamp=event.timestamp,
                    reduce_only=True,
                    reason=f"bridge_short_exit_{self.vbt_strategy_name}",
                ))
                logger.info("bridge_short_exit",
                            strategy=self.vbt_strategy_name,
                            symbol=self.symbol, price=price)

                if curr_long_entry:
                    signals.append(self._create_signal(
                        symbol=self.symbol,
                        side=OrderSide.BUY,
                        quantity=quantity,
                        timestamp=event.timestamp,
                        reason=f"bridge_long_entry_{self.vbt_strategy_name}",
                    ))
                    self._in_position = "long"
                else:
                    self._in_position = "flat"

        return signals


def create_bridged_strategy(
    strategy_name: str,
    params: dict[str, Any],
    symbol: str,
    timeframe: str = "4h",
    allocation_usd: float = 15.0,
) -> VBTBridgeStrategy:
    """Factory function to create a bridged VBT strategy."""
    return VBTBridgeStrategy(
        vbt_strategy_name=strategy_name,
        params=params,
        symbol=symbol,
        timeframe=timeframe,
        allocation_usd=allocation_usd,
    )


def create_v6_strategies(initial_capital: float = 150.0) -> list[VBTBridgeStrategy]:
    """Create all V6 portfolio strategies as bridged live strategies (legacy)."""
    V6_CONFIG = [
        ("momentum_ranking", "ETHUSDT", "1d", 0.14, {"roc_period": 60, "lookback": 180, "upper_threshold": 80, "lower_threshold": 40, "leverage": 1.5}),
        ("momentum_ranking", "BNBUSDT", "1d", 0.08, {"roc_period": 60, "lookback": 180, "upper_threshold": 80, "lower_threshold": 40, "leverage": 1.5}),
        ("trend_donchian_mtf", "BTCUSDT", "4h", 0.15, {"entry_period": 15, "exit_period": 10, "adx_threshold": 20, "htf_period": 200, "leverage": 2}),
        ("trend_donchian_adx_slope", "BTCUSDT", "4h", 0.05, {"entry_period": 20, "exit_period": 10, "adx_slope_bars": 5, "adx_slope_min": 0.3, "leverage": 2}),
        ("grid_trend_bias", "ETHUSDT", "4h", 0.18, {"bb_period": 20, "bb_std": 2.0, "ema_period": 50, "leverage": 2}),
        ("breakout_squeeze", "BTCUSDT", "4h", 0.06, {"bb_period": 30, "bb_std": 2.5, "kc_ema_period": 15, "kc_atr_period": 7, "kc_mult": 2.0, "leverage": 2}),
        ("tail_risk_hedge", "BTCUSDT", "1d", 0.07, {"consec_up_threshold": 14, "consec_down_threshold": 5, "exit_bars": 10, "leverage": 1}),
        ("tail_risk_hedge", "BNBUSDT", "1d", 0.05, {"consec_up_threshold": 14, "consec_down_threshold": 5, "exit_bars": 10, "leverage": 1}),
        ("dual_channel_breakout", "ETHUSDT", "4h", 0.10, {"dc_period": 30, "kc_ema": 15, "kc_atr": 14, "kc_mult": 2.0, "adx_period": 14, "adx_threshold": 20, "leverage": 2}),
        ("grid_trend_bias", "XRPUSDT", "4h", 0.05, {"bb_period": 20, "bb_std": 2.0, "ema_period": 50, "leverage": 2}),
        ("breakout_squeeze", "SOLUSDT", "4h", 0.04, {"bb_period": 30, "bb_std": 2.0, "kc_ema_period": 20, "kc_atr_period": 10, "kc_mult": 1.5, "leverage": 1}),
        ("grid_trend_bias", "SOLUSDT", "4h", 0.03, {"bb_period": 15, "bb_std": 2.0, "ema_period": 100, "leverage": 1}),
    ]

    strategies = []
    for strat_name, symbol, tf, alloc, params in V6_CONFIG:
        alloc_usd = initial_capital * alloc
        strategy = create_bridged_strategy(
            strategy_name=strat_name,
            params=params,
            symbol=symbol,
            timeframe=tf,
            allocation_usd=alloc_usd,
        )
        strategies.append(strategy)
        logger.info("v6_strategy_created",
                     name=strategy.name,
                     symbol=symbol,
                     alloc_usd=f"${alloc_usd:.1f}",
                     leverage=params.get("leverage", 1))

    return strategies


def create_v72_strategies(initial_capital: float = 150.0) -> list[VBTBridgeStrategy]:
    """Create all V7.2 portfolio strategies as bridged live strategies.

    V7.2: Three-tier architecture (Robust 40% / Moderate 48% / Fragile 12%)
    16 strategy positions across 5 coins (BTC, ETH, BNB, XRP, SOL).
    Sharpe 2.355, MaxDD -10.6%, Calmar 4.27.
    """
    V72_CONFIG = [
        # ⭐ ROBUST TIER — 40% (parameter stability >50%)
        ("trend_donchian_mtf", "BTCUSDT", "4h", 0.16, {"entry_period": 10, "exit_period": 10, "adx_threshold": 15, "htf_period": 150, "leverage": 2}),
        ("trend_donchian_adx_slope", "ETHUSDT", "4h", 0.10, {"entry_period": 20, "exit_period": 5, "adx_slope_bars": 5, "adx_slope_min": 0.2, "leverage": 2}),
        ("trend_donchian_adx_slope", "BTCUSDT", "4h", 0.08, {"entry_period": 30, "exit_period": 7, "adx_slope_bars": 3, "adx_slope_min": 0.2, "leverage": 2}),
        ("trend_donchian_mtf", "XRPUSDT", "4h", 0.04, {"entry_period": 10, "exit_period": 5, "adx_threshold": 15, "htf_period": 100, "leverage": 2}),
        ("trend_donchian_mtf", "BNBUSDT", "4h", 0.02, {"entry_period": 10, "exit_period": 7, "adx_threshold": 15, "htf_period": 150, "leverage": 2}),
        # 🔵 MODERATE TIER — 48% (25-50% viable combos)
        ("momentum_ranking", "ETHUSDT", "1d", 0.12, {"roc_period": 60, "lookback": 240, "upper_threshold": 70, "lower_threshold": 30, "leverage": 1.5}),
        ("momentum_ranking", "BNBUSDT", "1d", 0.08, {"roc_period": 90, "lookback": 120, "upper_threshold": 70, "lower_threshold": 30, "leverage": 1.5}),
        ("momentum_ranking", "SOLUSDT", "1d", 0.03, {"roc_period": 20, "lookback": 240, "upper_threshold": 70, "lower_threshold": 30, "leverage": 1.5}),
        ("grid_trend_bias", "XRPUSDT", "4h", 0.06, {"bb_period": 15, "bb_std": 2.0, "ema_period": 50, "leverage": 2}),
        ("tail_risk_hedge", "BNBUSDT", "1d", 0.05, {"consec_up_threshold": 10, "consec_down_threshold": 5, "exit_bars": 15, "leverage": 1}),
        ("tail_risk_hedge", "SOLUSDT", "1d", 0.04, {"consec_up_threshold": 10, "consec_down_threshold": 3, "exit_bars": 10, "leverage": 1}),
        ("grid_trend_bias", "BTCUSDT", "4h", 0.04, {"bb_period": 30, "bb_std": 3.0, "ema_period": 200, "leverage": 1}),
        ("grid_trend_bias", "SOLUSDT", "4h", 0.03, {"bb_period": 15, "bb_std": 2.0, "ema_period": 100, "leverage": 1}),
        ("tail_risk_hedge", "BTCUSDT", "1d", 0.03, {"consec_up_threshold": 10, "consec_down_threshold": 5, "exit_bars": 10, "leverage": 1}),
        # ⚠️ FRAGILE TIER — 12% (high Sharpe but <15% viable)
        ("grid_trend_bias", "ETHUSDT", "4h", 0.09, {"bb_period": 20, "bb_std": 2.0, "ema_period": 100, "leverage": 2}),
        ("breakout_squeeze", "BTCUSDT", "4h", 0.03, {"bb_period": 30, "bb_std": 3.0, "kc_ema_period": 10, "kc_atr_period": 7, "kc_mult": 2.0, "leverage": 2}),
    ]

    strategies = []
    for strat_name, symbol, tf, alloc, params in V72_CONFIG:
        alloc_usd = initial_capital * alloc
        strategy = create_bridged_strategy(
            strategy_name=strat_name,
            params=params,
            symbol=symbol,
            timeframe=tf,
            allocation_usd=alloc_usd,
        )
        strategies.append(strategy)
        logger.info("v72_strategy_created",
                     name=strategy.name,
                     symbol=symbol,
                     alloc_usd=f"${alloc_usd:.1f}",
                     leverage=params.get("leverage", 1))

    return strategies
