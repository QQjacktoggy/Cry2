# Jackbot A/B Strategy Backtest Summary

- Period: `2025-04-29 00:00 UTC` to `2026-04-29 23:55 UTC`
- Symbol: `ETHUSDC`
- Starting capital: `150 USDC`
- Fixed seed: `42`

## Comparison

| Variant | Net Profit | Ending Equity | ROI | Gross Profit | Commission | Comm/Gross | Max DD | Fills | Matched | Forced Closes | Stop Losses | Breakouts | Review Closes | Grid PnL | Trend PnL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline_grid | 7751.4258 | 7901.4258 | 5167.62% | 12108.0582 | 4356.6324 | 0.3598 | 902.9975 | 13761 | 3062 | 3091 | 186 | 1806 | 1099 | 9161.9422 | 0.0000 |
| fee_aware_grid | 1795.7243 | 1945.7243 | 1197.15% | 3379.5032 | 1583.7789 | 0.4686 | 407.7681 | 9192 | 2078 | 1855 | 141 | 1037 | 677 | 2293.6003 | 0.0000 |
| hybrid_trend_grid | 250.6654 | 400.6654 | 167.11% | 1274.1591 | 1023.4937 | 0.8033 | 693.1418 | 7903 | 1769 | 1820 | 153 | 1092 | 575 | 616.3715 | -55.0801 |

## Outputs

- JSON: `data\baseline_grid_result.json`, `data\fee_aware_grid_result.json`, `data\hybrid_trend_grid_result.json`
- Monthly CSV: `data\baseline_grid_monthly.csv`, `data\fee_aware_grid_monthly.csv`, `data\hybrid_trend_grid_monthly.csv`
- Comparison CSV: `data\strategy_variant_comparison.csv`
- Summary Report: `data\strategy_variant_summary.md`

## Assumptions and Limitations

- Dedicated backtest overlays set `per_symbol_alloc_pct=100` because this comparison runs only `ETHUSDC`.
- Variant A and Variant B both retain maker-first grid fills; trend-rider entries/exits in Variant B use taker assumptions.
- Public Binance Futures klines are used without exchange credentials or live/testnet order access.
