"""VBT strategy implementations."""

from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.funding_arb_vbt import FundingArbVBT
from backtest_tool.strategies.grid_futures_vbt import GridFuturesVBT
from backtest_tool.strategies.mean_reversion_bb_vbt import MeanReversionBBVBT
from backtest_tool.strategies.momentum_reversal_vbt import MomentumReversalVBT
from backtest_tool.strategies.trend_donchian_vbt import TrendDonchianVBT

STRATEGY_MAP: dict[str, type[BaseVBTStrategy]] = {
    "trend_donchian": TrendDonchianVBT,
    "mean_reversion_bb": MeanReversionBBVBT,
    "grid_futures": GridFuturesVBT,
    "funding_arb": FundingArbVBT,
    "momentum_reversal": MomentumReversalVBT,
}

__all__ = [
    "BaseVBTStrategy",
    "TrendDonchianVBT",
    "MeanReversionBBVBT",
    "GridFuturesVBT",
    "FundingArbVBT",
    "MomentumReversalVBT",
    "STRATEGY_MAP",
]