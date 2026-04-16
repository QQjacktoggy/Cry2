"""HTML report generation for backtest results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ReportGenerator:
    """Generates HTML backtest reports."""

    def generate(
        self,
        metrics: dict[str, Any],
        run_id: str,
        output_dir: str = "./data/backtest_results",
    ) -> str:
        """Generate an HTML report.

        Args:
            metrics: Calculated performance metrics.
            run_id: Backtest run identifier.
            output_dir: Output directory.

        Returns:
            Path to generated HTML file.
        """
        report_dir = Path(output_dir) / run_id
        report_dir.mkdir(parents=True, exist_ok=True)

        # Save metrics as JSON
        metrics_path = report_dir / "metrics.json"
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2, default=str)

        # Generate HTML
        html = self._build_html(metrics, run_id)
        html_path = report_dir / "report.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info("report_generated", path=str(html_path))
        return str(html_path)

    def _build_html(self, metrics: dict[str, Any], run_id: str) -> str:
        """Build HTML report content."""
        total_return = metrics.get("total_return", 0) * 100
        annual_return = metrics.get("annualized_return", 0) * 100
        max_dd = metrics.get("max_drawdown_pct", 0) * 100
        win_rate = metrics.get("win_rate", 0) * 100

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Backtest Report - {run_id}</title>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        .container {{ max-width: 900px; margin: auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }}
        h2 {{ color: #555; margin-top: 30px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 10px 15px; text-align: left; }}
        th {{ background: #4CAF50; color: white; }}
        tr:nth-child(even) {{ background: #f9f9f9; }}
        .positive {{ color: #4CAF50; font-weight: bold; }}
        .negative {{ color: #f44336; font-weight: bold; }}
        .metric-value {{ font-size: 1.1em; }}
    </style>
</head>
<body>
<div class="container">
    <h1>Backtest Report</h1>
    <p><strong>Run ID:</strong> {run_id}</p>

    <h2>Performance Summary</h2>
    <table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Total Return</td><td class="{'positive' if total_return >= 0 else 'negative'} metric-value">{total_return:.2f}%</td></tr>
        <tr><td>Annualized Return</td><td class="metric-value">{annual_return:.2f}%</td></tr>
        <tr><td>Sharpe Ratio</td><td class="metric-value">{metrics.get('sharpe_ratio', 0):.2f}</td></tr>
        <tr><td>Sortino Ratio</td><td class="metric-value">{metrics.get('sortino_ratio', 0):.2f}</td></tr>
        <tr><td>Max Drawdown</td><td class="negative metric-value">{max_dd:.2f}%</td></tr>
        <tr><td>Calmar Ratio</td><td class="metric-value">{metrics.get('calmar_ratio', 0):.2f}</td></tr>
    </table>

    <h2>Trade Statistics</h2>
    <table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Total Trades</td><td>{metrics.get('total_trades', 0)}</td></tr>
        <tr><td>Win Rate</td><td>{win_rate:.1f}%</td></tr>
        <tr><td>Profit Factor</td><td>{metrics.get('profit_factor', 0):.2f}</td></tr>
        <tr><td>Avg Win</td><td class="positive">{metrics.get('avg_win', 0):.2f}</td></tr>
        <tr><td>Avg Loss</td><td class="negative">{metrics.get('avg_loss', 0):.2f}</td></tr>
        <tr><td>Payoff Ratio</td><td>{metrics.get('payoff_ratio', 0):.2f}</td></tr>
        <tr><td>Total Fees</td><td>{metrics.get('total_fees', 0):.2f}</td></tr>
    </table>

    <h2>Equity</h2>
    <table>
        <tr><th>Metric</th><th>Value</th></tr>
        <tr><td>Initial Capital</td><td>{metrics.get('initial_capital', 10000):.2f} USDT</td></tr>
        <tr><td>Final Equity</td><td class="metric-value">{metrics.get('final_equity', 0):.2f} USDT</td></tr>
    </table>
</div>
</body>
</html>"""
