"""One-shot: regenerate per-strategy daily curves + nested WF + block bootstrap.

Loads V7.2 strategies, persists per-strategy daily equity CSV, then runs:
  - walk_forward_nested_analysis (equal + sharpe_pos weighting)
  - block_bootstrap_simulation (block_size=5)

Output numbers are printed as JSON to stdout so we can paste into the report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from backtest_tool.engine.robustness import (
    block_bootstrap_simulation,
    monte_carlo_simulation,
    walk_forward_nested_analysis,
)
from backtest_tool.engine.runner import BacktestRunner
from backtest_tool.scripts.full_portfolio_backtest import (
    PORTFOLIO,
    load_data,
    run_single_strategy,
)

OUT = ROOT / "backtest_tool" / "reports" / "output"


def main() -> None:
    runner = BacktestRunner()
    daily_curves: dict[str, pd.Series] = {}
    trades: dict[str, int] = {}
    print("Running 16 V7.2 strategies ...")
    for name, cfg in PORTFOLIO.items():
        r = run_single_strategy(name, cfg, runner)
        if r is None or r.get("equity") is None:
            print(f"  skip {name}")
            continue
        eq = r["equity"]
        if hasattr(eq.index, "date"):
            daily = eq.resample("1D").last().dropna()
            daily_curves[name] = daily
        trades[name] = int(r.get("trades", 0))
        print(f"  {name:<30} trades={r.get('trades',0):>4} final={r.get('final',0):.2f}")

    # Persist per-strategy daily curves
    df = pd.DataFrame(daily_curves).ffill().bfill()
    df.to_csv(OUT / "portfolio_per_strategy_daily.csv")
    print(f"\nSaved per-strategy daily curves: {OUT/'portfolio_per_strategy_daily.csv'} shape={df.shape}")

    # Combined equity (for bootstrap)
    combined = df.sum(axis=1).dropna()
    combined_series = combined.rename("equity")
    combined_series.to_frame().to_csv(OUT / "portfolio_combined_equity.csv", index_label="timestamp")

    # Nested WF (equal)
    wf_eq = walk_forward_nested_analysis(
        daily_curves, train_bars=365, test_bars=90, step_bars=90,
        annualization_factor=365.0, weighting="equal",
    )
    # Nested WF (sharpe_pos)
    wf_sp = walk_forward_nested_analysis(
        daily_curves, train_bars=365, test_bars=90, step_bars=90,
        annualization_factor=365.0, weighting="sharpe_pos",
    )
    # Block bootstrap
    bb = block_bootstrap_simulation(
        combined, n_simulations=1000, block_size=5, seed=42, annualization_factor=365.0,
    )
    # i.i.d. bootstrap (re-run to get fresh numbers aligned with this equity)
    mc = monte_carlo_simulation(
        combined, n_simulations=1000, seed=42, annualization_factor=365.0, method="bootstrap",
    )

    payload = {
        "trades": trades,
        "nested_wf_equal": wf_eq,
        "nested_wf_sharpe_pos": wf_sp,
        "block_bootstrap": bb,
        "iid_bootstrap": mc,
    }
    out_path = OUT / "robustness_v72_refresh.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nSaved summary JSON: {out_path}")
    print(json.dumps({
        "nested_wf_equal_summary": wf_eq["summary"],
        "nested_wf_sharpe_pos_summary": wf_sp["summary"],
        "block_bootstrap_sim": bb["simulation"],
        "iid_bootstrap_sim": mc["simulation"],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
