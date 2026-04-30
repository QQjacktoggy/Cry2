# Jackbot A/B Strategy Backtest Summary

- Period: `2025-04-30 02:48 UTC` to `2026-04-30 02:48 UTC`
- Symbol: `ETHUSDC`
- Starting capital: `150 USDC`
- Fixed seed: `42`

## Comparison

| Variant | Net Profit | Ending Equity | ROI | Gross Profit | Commission | Comm/Gross | Max DD | Fills | Matched | Forced Closes | Stop Losses | Breakouts | Review Closes | Grid PnL | Trend PnL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline_grid | 5803.8051 | 5953.8051 | 3869.20% | 9854.9598 | 4051.1547 | 0.4111 | 1547.0847 | 13644 | 2974 | 3132 | 182 | 1848 | 1102 | 7126.2373 | 0.0000 |
| fee_aware_grid | 1687.3559 | 1837.3559 | 1124.90% | 3371.3440 | 1683.9881 | 0.4995 | 605.0629 | 9280 | 2125 | 1850 | 131 | 1034 | 685 | 2217.9114 | 0.0000 |
| hybrid_trend_grid | 671.3451 | 821.3451 | 447.56% | 1693.0140 | 1021.6689 | 0.6035 | 632.6942 | 8143 | 1845 | 1815 | 166 | 1073 | 576 | 1003.1757 | -20.1120 |

## Outputs

- JSON: `data\baseline_grid_result.json`, `data\fee_aware_grid_result.json`, `data\hybrid_trend_grid_result.json`
- Monthly CSV: `data\baseline_grid_monthly.csv`, `data\fee_aware_grid_monthly.csv`, `data\hybrid_trend_grid_monthly.csv`
- Comparison CSV: `data\strategy_variant_comparison.csv`
- Summary Report: `data\strategy_variant_summary.md`

## Assumptions and Limitations

- Dedicated backtest overlays set `per_symbol_alloc_pct=100` because this comparison runs only `ETHUSDC`.
- Variant A and Variant B both retain maker-first grid fills; trend-rider entries/exits in Variant B use taker assumptions.
- Public Binance Futures klines are used without exchange credentials or live/testnet order access.
