#!/usr/bin/env python3
"""Run an apples-to-apples V7.4 vs V8 phase-1 comparison on the same runner."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import structlog

from bot.backtest.engine import BacktestEngine
from bot.backtest.metrics import MetricsCalculator
from bot.backtest.report import ReportGenerator
from bot.config.env import load_env
from bot.config.loader import load_config
from bot.core.logger import setup_logging
from bot.data.storage import ParquetStorage
from bot.execution.fee_model import FeeModel
from bot.execution.slippage import SlippageModel
from bot.utils.time_utils import datetime_to_ms, parse_date
from strategies_lab.integration import (
    V8_PROFILE_PRESETS,
    build_custom_strategy_suite,
    get_profile_preset,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ScenarioSpec:
    name: str
    kind: str
    label: str
    core_fraction: float
    lab_allocations: dict[str, float]


def _build_profile_scenarios(profiles: list[str]) -> list[ScenarioSpec]:
    scenarios: list[ScenarioSpec] = []
    for profile in profiles:
        preset = get_profile_preset(profile)
        scenarios.append(
            ScenarioSpec(
                name=profile,
                kind="profile",
                label=preset["label"],
                core_fraction=float(preset["core_fraction"]),
                lab_allocations=dict(preset["lab_allocations"]),
            )
        )
    return scenarios


def _build_single_sleeve_scenarios() -> list[ScenarioSpec]:
    unique_allocations: dict[str, set[float]] = {}
    for profile, preset in V8_PROFILE_PRESETS.items():
        if profile == "baseline":
            continue
        for sleeve_name, allocation in preset["lab_allocations"].items():
            unique_allocations.setdefault(sleeve_name, set()).add(float(allocation))

    scenarios: list[ScenarioSpec] = []
    for sleeve_name in sorted(unique_allocations):
        for allocation in sorted(unique_allocations[sleeve_name]):
            pct_label = f"{allocation:.0%}".replace("%", "pct")
            scenarios.append(
                ScenarioSpec(
                    name=f"{sleeve_name}_{pct_label}",
                    kind="sleeve",
                    label=f"{sleeve_name} @ {allocation:.0%}",
                    core_fraction=1.0 - allocation,
                    lab_allocations={sleeve_name: allocation},
                )
            )
    return scenarios


def _build_execution_models(config: dict[str, Any]) -> tuple[SlippageModel, FeeModel]:
    exec_cfg = config.get("execution", {})
    slippage = SlippageModel(
        model_type=exec_cfg.get("slippage_model", "fixed_bps"),
        fixed_bps=exec_cfg.get("slippage_bps", 2),
        bps_base=exec_cfg.get("slippage_bps_base", 1),
    )
    fees = FeeModel(
        maker_rate=exec_cfg.get("fee_rate_maker", 0.0002),
        taker_rate=exec_cfg.get("fee_rate_taker", 0.0004),
    )
    return slippage, fees


def _run_scenario(
    scenario: ScenarioSpec,
    *,
    capital: float,
    start_ms: int,
    end_ms: int,
    data_dir: str,
    output_root: Path,
    slippage: SlippageModel,
    fees: FeeModel,
) -> dict[str, Any]:
    strategies = build_custom_strategy_suite(
        initial_capital=capital,
        core_fraction=scenario.core_fraction,
        lab_allocations=scenario.lab_allocations,
    )
    all_symbols = sorted({symbol for strategy in strategies for symbol in strategy.symbols})
    all_timeframes = sorted({strategy.timeframe for strategy in strategies})

    engine = BacktestEngine(
        strategies=strategies,
        symbols=all_symbols,
        timeframes=all_timeframes,
        start_ms=start_ms,
        end_ms=end_ms,
        initial_capital=capital,
        storage=ParquetStorage(data_dir),
        slippage_model=slippage,
        fee_model=fees,
    )
    results = engine.run()

    calc = MetricsCalculator(
        equity_curve=results["equity_curve"],
        fills=results["fills"],
        initial_capital=capital,
    )
    metrics = calc.calculate_all()

    report_dir = output_root / "scenario_reports" / scenario.name
    report_path = ReportGenerator().generate(
        metrics=metrics,
        run_id=results["run_id"],
        output_dir=str(report_dir),
    )

    return {
        "scenario": scenario.name,
        "kind": scenario.kind,
        "label": scenario.label,
        "core_fraction": scenario.core_fraction,
        "lab_allocations": json.dumps(scenario.lab_allocations, sort_keys=True),
        "strategy_count": len(strategies),
        "lab_strategy_count": len(scenario.lab_allocations),
        "symbols": ",".join(all_symbols),
        "timeframes": ",".join(all_timeframes),
        "run_id": results["run_id"],
        "report_path": report_path,
        "final_equity": float(metrics["final_equity"]),
        "total_return": float(metrics["total_return"]),
        "annualized_return": float(metrics["annualized_return"]),
        "sharpe_ratio": float(metrics["sharpe_ratio"]),
        "sortino_ratio": float(metrics["sortino_ratio"]),
        "max_drawdown_pct": float(metrics["max_drawdown_pct"]),
        "calmar_ratio": float(metrics["calmar_ratio"]),
        "win_rate": float(metrics["win_rate"]),
        "profit_factor": float(metrics["profit_factor"]),
        "total_trades": int(metrics["total_trades"]),
        "total_fees": float(metrics["total_fees"]),
    }


def _add_baseline_deltas(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    baseline = next(row for row in rows if row["scenario"] == "baseline")
    metric_names = [
        "final_equity",
        "total_return",
        "annualized_return",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown_pct",
        "calmar_ratio",
        "win_rate",
        "profit_factor",
        "total_trades",
        "total_fees",
    ]

    enriched: list[dict[str, Any]] = []
    for row in rows:
        current = dict(row)
        for metric_name in metric_names:
            current[f"delta_{metric_name}"] = current[metric_name] - baseline[metric_name]
        enriched.append(current)
    return enriched, baseline


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _print_table(title: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    print(
        f"{'Scenario':<34}"
        f"{'AnnRet':>10}"
        f"{'Delta':>10}"
        f"{'MaxDD':>10}"
        f"{'Sharpe':>10}"
        f"{'FinalEq':>12}"
    )
    for row in rows:
        print(
            f"{row['scenario']:<34}"
            f"{row['annualized_return']:>9.2%}"
            f"{row['delta_annualized_return']:>9.2%}"
            f"{row['max_drawdown_pct']:>9.2%}"
            f"{row['sharpe_ratio']:>10.2f}"
            f"{row['final_equity']:>12.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare V7.4 and V8 phase-1 on one runner")
    parser.add_argument(
        "--profiles",
        nargs="+",
        default=["baseline", "v8a", "v8b", "v8c"],
        choices=sorted(V8_PROFILE_PRESETS),
        help="Profiles to include in the comparison",
    )
    parser.add_argument("--env", default="backtest", help="Environment (default: backtest)")
    parser.add_argument("--config", default="config\\config.yaml", help="Config file path")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=150.0, help="Initial capital")
    args = parser.parse_args()

    load_env()
    config = load_config(args.config, environment=args.env)
    setup_logging(config.get("logging", {}).get("level", "INFO"), json_format=False)

    start_date = args.start or config.get("start_date", "2024-01-01")
    end_date = args.end or config.get("end_date", "2025-12-31")
    start_ms = datetime_to_ms(parse_date(start_date))
    end_ms = datetime_to_ms(parse_date(end_date))

    data_dir = config.get("paths", {}).get("data_dir", ".\\data")
    results_dir = Path(config.get("paths", {}).get("results_dir", ".\\data\\backtest_results"))
    comparison_root = results_dir / "v8_profiles" / "comparison" / datetime.now(UTC).strftime(
        "run_%Y%m%d_%H%M%S"
    )
    comparison_root.mkdir(parents=True, exist_ok=True)

    slippage, fees = _build_execution_models(config)
    scenarios = _build_profile_scenarios(args.profiles) + _build_single_sleeve_scenarios()

    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        print(
            f"\nRunning {scenario.kind:<7} {scenario.name} "
            f"(core={scenario.core_fraction:.0%}, lab={scenario.lab_allocations or {}})"
        )
        rows.append(
            _run_scenario(
                scenario,
                capital=args.capital,
                start_ms=start_ms,
                end_ms=end_ms,
                data_dir=data_dir,
                output_root=comparison_root,
                slippage=slippage,
                fees=fees,
            )
        )

    summary_rows, baseline = _add_baseline_deltas(rows)
    profile_rows = sorted(
        [row for row in summary_rows if row["kind"] == "profile"],
        key=lambda row: row["annualized_return"],
        reverse=True,
    )
    sleeve_rows = sorted(
        [row for row in summary_rows if row["kind"] == "sleeve"],
        key=lambda row: row["delta_annualized_return"],
    )

    _write_csv(comparison_root / "comparison_summary.csv", summary_rows)
    _write_csv(comparison_root / "profile_ranking.csv", profile_rows)
    _write_csv(comparison_root / "single_sleeve_impact.csv", sleeve_rows)

    analysis = {
        "baseline": baseline,
        "best_profile": profile_rows[0] if profile_rows else None,
        "worst_profile": profile_rows[-1] if profile_rows else None,
        "worst_single_sleeve": sleeve_rows[0] if sleeve_rows else None,
        "best_single_sleeve": sleeve_rows[-1] if sleeve_rows else None,
    }
    with (comparison_root / "analysis.json").open("w", encoding="utf-8") as handle:
        json.dump(analysis, handle, indent=2)

    print("\n" + "=" * 80)
    print("UNIFIED V8 COMPARISON")
    print("=" * 80)
    print(f"Period:         {start_date} -> {end_date}")
    print(f"Capital:        {args.capital:.2f} USDT")
    print(f"Baseline AnnRet:{baseline['annualized_return']:.2%}")
    print(f"Output Dir:     {comparison_root}")

    _print_table("Profile ranking", profile_rows)
    _print_table("Single-sleeve impact vs baseline", sleeve_rows)

    logger.info(
        "v8_profile_comparison_complete",
        scenario_count=len(summary_rows),
        best_profile=analysis["best_profile"]["scenario"] if analysis["best_profile"] else None,
        worst_single_sleeve=analysis["worst_single_sleeve"]["scenario"] if analysis["worst_single_sleeve"] else None,
        output_dir=str(comparison_root),
    )


if __name__ == "__main__":
    main()
