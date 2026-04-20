"""VBT strategy implementations — baseline 4 + 50 new variants."""

from backtest_tool.strategies.arb_family import (
    FundingArbRelaxed,
    LongHorizonETH,
    LongHorizonSOL,
    SpotPerpBasis,
    WalkForwardTrend,
)
from backtest_tool.strategies.base_vbt import BaseVBTStrategy
from backtest_tool.strategies.funding_arb_vbt import FundingArbVBT
from backtest_tool.strategies.funding_reversal_vbt import FundingReversalVBT
from backtest_tool.strategies.grid_family import (
    GridATRAdaptive,
    GridBollingerBands,
    GridFundingAware,
    GridHedged,
    GridRangingOnly,
    GridStopOut,
    GridTrendBias,
)
from backtest_tool.strategies.grid_futures_vbt import GridFuturesVBT
from backtest_tool.strategies.mean_reversion_bb_vbt import MeanReversionBBVBT
from backtest_tool.strategies.mean_reversion_family import (
    MRBBATRStop,
    MRBBDual,
    MRBBMiddleExit,
    MRBBPartialTP,
    MRBBRegimeFiltered,
    MRBBRSIDivergence,
    MRBBVWAP,
    MRBBZScore,
)
from backtest_tool.strategies.momentum_family import (
    BreakoutSqueeze,
    HeikinAshiTrend,
    IchimokuCloudBreak,
    MACDTrendFollow,
    MomentumRanking,
    MomentumROC,
    OpeningRangeBreakout,
    SupertrendFollow,
    VolatilityExpansion,
)
from backtest_tool.strategies.regime_family import (
    CorrelationPruner,
    DrawdownThrottle,
    EnsembleVote,
    KellyPositionSizer,
    RegimeSwitcher,
    RiskParityPortfolio,
)
from backtest_tool.strategies.reversal_family import (
    GapFade,
    MeanReversionKalman,
    PinBarReversal,
    RSI2Connors,
    StochasticReversal,
)
from backtest_tool.strategies.phase7_family import (
    DualChannelBreakout,
    FundingContrarian,
    GammaScalpingGrid,
    MomentumRotation,
    PairsSpreadMR,
    PriceVolumeDivergence,
    TailRiskHedge,
    TimeOfDayFilter,
    TrendStrengthSizing,
    VolatilityMeanReversion,
)
from backtest_tool.strategies.trend_donchian_vbt import TrendDonchianVBT
from backtest_tool.strategies.trend_family import (
    TrendDonchianADXSlope,
    TrendDonchianAdaptive,
    TrendDonchianATRTrail,
    TrendDonchianChandelier,
    TrendDonchianKeltner,
    TrendDonchianMTF,
    TrendDonchianPyramid,
    TrendDonchianTurtle,
    TrendDonchianV2,
    TrendDonchianVolumeConfirm,
)

STRATEGY_MAP: dict[str, type[BaseVBTStrategy]] = {
    # Baseline 4
    "trend_donchian": TrendDonchianVBT,
    "mean_reversion_bb": MeanReversionBBVBT,
    "grid_futures": GridFuturesVBT,
    "funding_arb": FundingArbVBT,
    # A: Trend family (10)
    "trend_donchian_v2": TrendDonchianV2,
    "trend_donchian_mtf": TrendDonchianMTF,
    "trend_donchian_atr_trail": TrendDonchianATRTrail,
    "trend_donchian_chandelier": TrendDonchianChandelier,
    "trend_donchian_volume": TrendDonchianVolumeConfirm,
    "trend_donchian_pyramid": TrendDonchianPyramid,
    "trend_donchian_adx_slope": TrendDonchianADXSlope,
    "trend_donchian_turtle": TrendDonchianTurtle,
    "trend_donchian_keltner": TrendDonchianKeltner,
    "trend_donchian_adaptive": TrendDonchianAdaptive,
    # B: Grid family (7)
    "grid_ranging_only": GridRangingOnly,
    "grid_atr_adaptive": GridATRAdaptive,
    "grid_bollinger": GridBollingerBands,
    "grid_hedged": GridHedged,
    "grid_trend_bias": GridTrendBias,
    "grid_funding_aware": GridFundingAware,
    "grid_stopout": GridStopOut,
    # C: Mean-reversion family (8)
    "mrbb_regime": MRBBRegimeFiltered,
    "mrbb_atr_stop": MRBBATRStop,
    "mrbb_middle_exit": MRBBMiddleExit,
    "mrbb_rsi_div": MRBBRSIDivergence,
    "mrbb_vwap": MRBBVWAP,
    "mrbb_zscore": MRBBZScore,
    "mrbb_dual": MRBBDual,
    "mrbb_partial_tp": MRBBPartialTP,
    # D: Momentum / breakout (9)
    "momentum_roc": MomentumROC,
    "momentum_ranking": MomentumRanking,
    "breakout_squeeze": BreakoutSqueeze,
    "opening_range_breakout": OpeningRangeBreakout,
    "volatility_expansion": VolatilityExpansion,
    "macd_trend": MACDTrendFollow,
    "supertrend_follow": SupertrendFollow,
    "ichimoku_cloud": IchimokuCloudBreak,
    "heikin_ashi_trend": HeikinAshiTrend,
    # E: Reversal (5)
    "rsi2_connors": RSI2Connors,
    "pin_bar_reversal": PinBarReversal,
    "stochastic_reversal": StochasticReversal,
    "mr_kalman": MeanReversionKalman,
    "gap_fade": GapFade,
    # F: Regime / overlay (6)
    "regime_switcher": RegimeSwitcher,
    "ensemble_vote": EnsembleVote,
    "risk_parity": RiskParityPortfolio,
    "kelly_sizer": KellyPositionSizer,
    "drawdown_throttle": DrawdownThrottle,
    "correlation_pruner": CorrelationPruner,
    # G: Arb / cross-asset / walk-forward (5)
    "funding_arb_relaxed": FundingArbRelaxed,
    "spot_perp_basis": SpotPerpBasis,
    "long_horizon_eth": LongHorizonETH,
    "long_horizon_sol": LongHorizonSOL,
    "walk_forward_trend": WalkForwardTrend,
    # G4: Funding reversal (1)
    "funding_reversal": FundingReversalVBT,
    # H: Phase 7 — New strategy exploration (10)
    "momentum_rotation": MomentumRotation,
    "trend_strength_sizing": TrendStrengthSizing,
    "dual_channel_breakout": DualChannelBreakout,
    "vol_mean_reversion": VolatilityMeanReversion,
    "gamma_scalping": GammaScalpingGrid,
    "funding_contrarian": FundingContrarian,
    "pv_divergence": PriceVolumeDivergence,
    "time_of_day_filter": TimeOfDayFilter,
    "pairs_spread_mr": PairsSpreadMR,
    "tail_risk_hedge": TailRiskHedge,
}

__all__ = [
    "BaseVBTStrategy",
    "STRATEGY_MAP",
    "TrendDonchianVBT",
    "MeanReversionBBVBT",
    "GridFuturesVBT",
    "FundingArbVBT",
]
