# Jackbot A/B Strategy Backtest Summary

- Period: `2025-04-30 05:27 UTC` to `2026-04-30 05:27 UTC`
- Symbol: `ETHUSDC`
- Starting capital: `150 USDC`
- Fixed seed: `42`
- Compounding: `compound_pct=50.0%`

## Comparison

| Variant | Net Profit | Ending Equity | ROI | Gross Profit | Commission | Comm/Gross | Max DD | Fills | Matched | Forced Closes | Stop Losses | Breakouts | Review Closes | Grid PnL | Trend PnL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline_grid | 3903.4498 | 4053.4498 | 2602.30% | 8462.2161 | 4558.7663 | 0.5387 | 872.2840 | 13525 | 2967 | 3137 | 168 | 1882 | 1087 | 3903.4498 | 0.0000 |
| fee_aware_grid | 1210.0354 | 1360.0354 | 806.69% | 3374.0305 | 2163.9951 | 0.6414 | 356.3470 | 9453 | 2160 | 1860 | 140 | 1062 | 658 | 1210.0354 | 0.0000 |
| hybrid_trend_grid | 74.4909 | 224.4909 | 49.66% | 1234.6875 | 1160.1966 | 0.9397 | 498.8860 | 7988 | 1798 | 1828 | 143 | 1117 | 568 | 81.0278 | -6.5360 |

## Outputs

- JSON: `data\baseline_grid_result.json`, `data\fee_aware_grid_result.json`, `data\hybrid_trend_grid_result.json`
- Monthly CSV: `data\baseline_grid_monthly.csv`, `data\fee_aware_grid_monthly.csv`, `data\hybrid_trend_grid_monthly.csv`
- Comparison CSV: `data\strategy_variant_comparison.csv`
- Summary Report: `data\strategy_variant_summary.md`

## Observations

- `baseline_grid` still leads raw profit (net=3903.4498, dd=872.2840)
- `fee_aware_grid` materially reduces churn and drawdown versus baseline (fills=9453, dd=356.3470)
- `hybrid_trend_grid` remains profitable overall, but its trend sleeve is still negative (trend_pnl=-6.5360), so the trend sleeve still needs tuning.

## Assumptions and Limitations

- Dedicated backtest overlays set `per_symbol_alloc_pct=100` because this comparison runs only `ETHUSDC`.
- Variant A and Variant B both retain maker-first grid fills; risk exits such as stop-loss, breakout, and margin protection use taker assumptions.
- Results include configured compounding (`compound_pct=50.0%`), so ROI reflects reinvestment rather than a flat `150 USDC` stake throughout the year.
- Public Binance Futures klines are used without exchange credentials or live/testnet order access.
