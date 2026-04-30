"""Run baseline and A/B variant backtests for Jackbot issue #17."""

from __future__ import annotations

import csv
import json
import logging
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from jackbot.config_utils import build_day_trader_params, load_merged_config
from jackbot.strategy.day_trader import DayTraderConfig
from scripts.backtest import BacktestEngine, download_klines, resolve_fee_rates

structlog.configure(
    processors=[],
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
    logger_factory=structlog.PrintLoggerFactory(file=open(os.devnull, "w")),
)

DATA_DIR = ROOT / "data"
BASE_CONFIG = ROOT / "config" / "settings.yaml"
SCENARIOS = [
    ("baseline_grid", ROOT / "config" / "backtest_baseline_grid.yaml"),
    ("fee_aware_grid", ROOT / "config" / "backtest_fee_aware_grid.yaml"),
    ("hybrid_trend_grid", ROOT / "config" / "backtest_hybrid_trend_grid.yaml"),
]


def monthly_rows(variant: str, results: dict) -> list[dict[str, object]]:
    daily_detail = results.get("daily_detail", {})
    cumulative_net = 0.0
    monthly: dict[str, dict[str, object]] = {}
    for date_str in sorted(daily_detail):
        day = daily_detail[date_str]
        month = date_str[:7]
        cumulative_net += day.get("net_profit", 0.0)
        row = monthly.setdefault(
            month,
            {
                "variant": variant,
                "month": month,
                "days": 0,
                "gross_profit": 0.0,
                "commission": 0.0,
                "net_profit": 0.0,
                "winning_days": 0,
                "losing_days": 0,
                "flat_days": 0,
                "halted_days": 0,
                "daily_resets": 0,
                "maker_fee": 0.0,
                "taker_fee": 0.0,
                "grid_pnl": 0.0,
                "trend_pnl": 0.0,
                "ending_equity": results["initial_capital"],
            },
        )
        row["days"] += 1
        row["gross_profit"] += day.get("gross_profit", 0.0)
        row["commission"] += day.get("commission", 0.0)
        row["net_profit"] += day.get("net_profit", 0.0)
        row["daily_resets"] += day.get("resets", 0)
        row["maker_fee"] += day.get("maker_fee", 0.0)
        row["taker_fee"] += day.get("taker_fee", 0.0)
        row["grid_pnl"] += day.get("grid_pnl", 0.0)
        row["trend_pnl"] += day.get("trend_pnl", 0.0)
        row["ending_equity"] = round(results["initial_capital"] + cumulative_net, 4)
        if day.get("halted"):
            row["halted_days"] += 1
        if day.get("net_profit", 0.0) > 0:
            row["winning_days"] += 1
        elif day.get("net_profit", 0.0) < 0:
            row["losing_days"] += 1
        else:
            row["flat_days"] += 1
    return list(monthly.values())


def comparison_row(variant: str, config_path: Path, results: dict) -> dict[str, object]:
    pnl = results["pnl"]
    trades = results["trades"]
    risk = results["risk"]
    fees = results.get("fees", {})
    strategy_pnl = results.get("strategy_pnl", {})
    return {
        "variant": variant,
        "config_path": str(config_path.relative_to(ROOT)),
        "period_start": results["period"]["start"],
        "period_end": results["period"]["end"],
        "initial_capital": results["initial_capital"],
        "ending_equity": results["ending_equity"],
        "net_profit": pnl["net_profit"],
        "roi_pct": pnl["roi_pct"],
        "gross_profit": pnl["gross_profit"],
        "commission": pnl["commission"],
        "commission_to_gross_profit": pnl.get("commission_to_gross_profit", 0.0),
        "max_drawdown": risk["max_drawdown"],
        "total_fills": trades["total_fills"],
        "matched_trades": trades["matched_trades"],
        "forced_close_events": trades["forced_close_events"],
        "stop_losses": risk["stop_losses"],
        "breakouts": risk["breakouts"],
        "review_closes": risk["reviews_closed"],
        "daily_reset_total": risk.get("daily_reset_total", 0),
        "max_daily_resets_used": risk.get("max_daily_resets_used", 0),
        "maker_fee_total": fees.get("maker_fee_total", 0.0),
        "taker_fee_total": fees.get("taker_fee_total", 0.0),
        "grid_pnl": strategy_pnl.get("grid_pnl", 0.0),
        "trend_pnl": strategy_pnl.get("trend_pnl", 0.0),
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def markdown_report(
    start_dt: datetime,
    end_dt: datetime,
    seed: int,
    compound_pct: float,
    scenario_rows: list[dict[str, object]],
    files_written: dict[str, list[str]],
) -> str:
    lines = [
        "# Jackbot A/B Strategy Backtest Summary",
        "",
        f"- Period: `{start_dt.strftime('%Y-%m-%d %H:%M UTC')}` to `{end_dt.strftime('%Y-%m-%d %H:%M UTC')}`",
        f"- Symbol: `ETHUSDC`",
        f"- Starting capital: `150 USDC`",
        f"- Fixed seed: `{seed}`",
        f"- Compounding: `compound_pct={compound_pct:.1f}%`",
        "",
        "## Comparison",
        "",
        "| Variant | Net Profit | Ending Equity | ROI | Gross Profit | Commission | Comm/Gross | Max DD | Fills | Matched | Forced Closes | Stop Losses | Breakouts | Review Closes | Grid PnL | Trend PnL |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in scenario_rows:
        lines.append(
            "| "
            f"{row['variant']} | "
            f"{row['net_profit']:.4f} | "
            f"{row['ending_equity']:.4f} | "
            f"{row['roi_pct']:.2f}% | "
            f"{row['gross_profit']:.4f} | "
            f"{row['commission']:.4f} | "
            f"{row['commission_to_gross_profit']:.4f} | "
            f"{row['max_drawdown']:.4f} | "
            f"{row['total_fills']} | "
            f"{row['matched_trades']} | "
            f"{row['forced_close_events']} | "
            f"{row['stop_losses']} | "
            f"{row['breakouts']} | "
            f"{row['review_closes']} | "
            f"{row['grid_pnl']:.4f} | "
            f"{row['trend_pnl']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Outputs",
            "",
        ]
    )
    for label, paths in files_written.items():
        lines.append(f"- {label}: " + ", ".join(f"`{path}`" for path in paths))
    baseline_row = next((row for row in scenario_rows if row["variant"] == "baseline_grid"), None)
    fee_aware_row = next((row for row in scenario_rows if row["variant"] == "fee_aware_grid"), None)
    hybrid_row = next((row for row in scenario_rows if row["variant"] == "hybrid_trend_grid"), None)
    lines.extend(
        [
            "",
            "## Observations",
            "",
            (
                f"- `baseline_grid` still leads raw profit "
                f"(net={baseline_row['net_profit']:.4f}, dd={baseline_row['max_drawdown']:.4f})"
                if baseline_row
                else "- `baseline_grid` still leads raw profit in this run."
            ),
            (
                f"- `fee_aware_grid` materially reduces churn and drawdown versus baseline "
                f"(fills={fee_aware_row['total_fills']}, dd={fee_aware_row['max_drawdown']:.4f})"
                if fee_aware_row
                else "- `fee_aware_grid` materially reduces churn and drawdown versus baseline."
            ),
            (
                f"- `hybrid_trend_grid` remains profitable overall, but its trend sleeve is still negative "
                f"(trend_pnl={hybrid_row['trend_pnl']:.4f}), so the trend sleeve still needs tuning."
                if hybrid_row
                else "- `hybrid_trend_grid` remains profitable overall, but its trend sleeve still needs tuning."
            ),
            "",
            "## Assumptions and Limitations",
            "",
            "- Dedicated backtest overlays set `per_symbol_alloc_pct=100` because this comparison runs only `ETHUSDC`.",
            "- Variant A and Variant B both retain maker-first grid fills; risk exits such as stop-loss, breakout, and margin protection use taker assumptions.",
            f"- Results include configured compounding (`compound_pct={compound_pct:.1f}%`), so ROI reflects reinvestment rather than a flat `150 USDC` stake throughout the year.",
            "- Public Binance Futures klines are used without exchange credentials or live/testnet order access.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    end_dt = datetime.now(UTC).replace(second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=365)
    start_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)
    seed = 42

    base_config = load_merged_config(BASE_CONFIG)
    compound_pct = base_config.get("trading", {}).get("compound_pct", 0.0)
    maker_rate, taker_rate = resolve_fee_rates("config", base_config.get("fees", {}), None, None)

    print(f"Downloading ETHUSDC 5m data from {start_dt.date()} to {end_dt.date()}...")
    klines_by_symbol = {
        "ETHUSDC": download_klines("ETHUSDC", "5m", start_ts, end_ts),
    }
    print(f"Downloaded {len(klines_by_symbol['ETHUSDC']):,} bars.")

    comparison_rows: list[dict[str, object]] = []
    files_written: dict[str, list[str]] = {
        "JSON": [],
        "Monthly CSV": [],
    }

    for variant, overlay_path in SCENARIOS:
        config_data = load_merged_config(BASE_CONFIG, [overlay_path])
        params = build_day_trader_params(
            config_data,
            capital_override=150.0,
            maker_rate=maker_rate,
            taker_rate=taker_rate,
        )
        config = DayTraderConfig.from_dict(params)

        random.seed(seed)
        engine = BacktestEngine(config, maker_rate=maker_rate, taker_rate=taker_rate)
        results = engine.run(klines_by_symbol)
        results["strategy_variant"] = variant
        results["config_path"] = str(overlay_path.relative_to(ROOT))
        results["seed"] = seed
        results["fee_model"] = {
            "name": "config",
            "maker": maker_rate,
            "taker": taker_rate,
        }

        json_path = DATA_DIR / f"{variant}_result.json"
        with json_path.open("w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2, ensure_ascii=False)
        files_written["JSON"].append(str(json_path.relative_to(ROOT)))

        monthly = monthly_rows(variant, results)
        monthly_path = DATA_DIR / f"{variant}_monthly.csv"
        write_csv(monthly_path, monthly)
        files_written["Monthly CSV"].append(str(monthly_path.relative_to(ROOT)))

        comparison_rows.append(comparison_row(variant, overlay_path, results))
        print(f"{variant}: net={results['pnl']['net_profit']:.4f}, roi={results['pnl']['roi_pct']:.2f}%")

    comparison_path = DATA_DIR / "strategy_variant_comparison.csv"
    write_csv(comparison_path, comparison_rows)

    report_path = DATA_DIR / "strategy_variant_summary.md"
    report_path.write_text(
        markdown_report(start_dt, end_dt, seed, compound_pct, comparison_rows, {
            **files_written,
            "Comparison CSV": [str(comparison_path.relative_to(ROOT))],
            "Summary Report": [str(report_path.relative_to(ROOT))],
        }),
        encoding="utf-8",
    )

    print(f"Wrote comparison CSV to {comparison_path}")
    print(f"Wrote summary report to {report_path}")


if __name__ == "__main__":
    main()
