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
        fills: list[Any] = None,
    ) -> str:
        """Generate an HTML report.

        Args:
            metrics: Calculated performance metrics.
            run_id: Backtest run identifier.
            output_dir: Output directory.
            fills: List of execution fills.

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
        html = self._build_html(metrics, run_id, fills)
        html_path = report_dir / "report.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info("report_generated", path=str(html_path))
        return str(html_path)

    def _build_html(self, metrics: dict[str, Any], run_id: str, fills: list[Any] = None) -> str:
        """Build HTML report content."""
        total_return = metrics.get("total_return", 0) * 100
        annual_return = metrics.get("annualized_return", 0) * 100
        max_dd = metrics.get("max_drawdown_pct", 0) * 100
        win_rate = metrics.get("win_rate", 0) * 100

        trades_html = ""
        if fills and len(fills) > 0:
            trades_html = """
            <div class="row mt-4">
                <div class="col-12">
                    <div class="card">
                        <div class="card-header">
                            <i class="fas fa-list me-2 text-primary"></i> 交易明細 (Trade Details)
                        </div>
                        <div class="card-body p-0">
                            <div class="table-responsive" style="max-height: 500px; overflow-y: auto;">
                                <table class="table table-hover table-striped mb-0 text-center">
                                    <thead style="position: sticky; top: 0; background: #fff; z-index: 1;">
                                        <tr>
                                            <th>時間 (Time)</th>
                                            <th>標的 (Symbol)</th>
                                            <th>方向 (Side)</th>
                                            <th>數量 (Qty)</th>
                                            <th>價格 (Price)</th>
                                            <th>手續費 (Fee)</th>
                                        </tr>
                                    </thead>
                                    <tbody>
            """
            for fill in fills:
                side_color = "text-success" if fill.side.value == "BUY" else "text-danger"
                # Use getattr because it's a FillEvent dataclass object
                time_str = getattr(fill, "timestamp", "").strftime("%Y-%m-%d %H:%M:%S") if hasattr(fill, "timestamp") else ""
                sym = getattr(fill, "symbol", "")
                side_val = getattr(fill.side, "value", str(fill.side))
                qty = getattr(fill, "quantity", 0)
                px = getattr(fill, "price", 0)
                fee = getattr(fill, "fee", 0)

                trades_html += f"""
                                        <tr>
                                            <td>{time_str}</td>
                                            <td>{sym}</td>
                                            <td class="font-weight-bold {side_color}">{side_val}</td>
                                            <td>{qty:.4f}</td>
                                            <td>{px:.2f}</td>
                                            <td>{fee:.4f}</td>
                                        </tr>
                """
            trades_html += """
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            """

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Advanced Backtest Analysis - {run_id}</title>
    <!-- Include Bootstrap for modern, beautiful styling -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background: #f8f9fa; padding-top: 30px; padding-bottom: 50px; }}
        .card {{ border: none; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); margin-bottom: 25px; }}
        .card-header {{ background-color: #ffffff; border-bottom: 1px solid #edf2f9; font-weight: 600; border-radius: 12px 12px 0 0 !important; padding: 15px 25px; }}
        .metric-box {{ text-align: center; padding: 20px; border-radius: 8px; background: #ffffff; box-shadow: 0 2px 10px rgba(0,0,0,0.03); }}
        .metric-title {{ font-size: 0.85rem; color: #6c757d; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }}
        .metric-value {{ font-size: 1.8rem; font-weight: 700; }}
        .positive {{ color: #28a745; }}
        .negative {{ color: #dc3545; }}
        .neutral {{ color: #495057; }}
        .page-header {{ margin-bottom: 40px; text-align: center; }}
        .page-header h1 {{ font-weight: 700; color: #2c3e50; }}
        .page-header p {{ color: #6c757d; font-size: 1.1rem; }}
        .table th {{ font-weight: 600; color: #495057; background-color: #f8f9fa; border-top: none; }}
        .table td {{ vertical-align: middle; font-size: 0.95rem; }}
        .icon-metric {{ font-size: 2rem; margin-bottom: 15px; opacity: 0.8; }}
        .bg-primary-light {{ background-color: #e8f4fd; color: #0d6efd; }}
        .bg-success-light {{ background-color: #e6f6ea; color: #198754; }}
        .bg-danger-light {{ background-color: #fbeded; color: #dc3545; }}
        .bg-warning-light {{ background-color: #fff8e6; color: #ffc107; }}
    </style>
</head>
<body>
<div class="container">
    <div class="page-header">
        <h1><i class="fas fa-chart-line text-primary me-2"></i> 交易策略回測分析報告</h1>
        <p>Detailed Backtest Analysis | Run ID: {run_id}</p>
    </div>

    <div class="row mb-4">
        <div class="col-md-3">
            <div class="metric-box">
                <i class="fas fa-wallet icon-metric text-primary"></i>
                <div class="metric-title">Initial Capital</div>
                <div class="metric-value neutral">{metrics.get('initial_capital', 10000):.2f} <span style="font-size:1rem">USDT</span></div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="metric-box">
                <i class="fas fa-coins icon-metric text-success"></i>
                <div class="metric-title">Final Equity</div>
                <div class="metric-value neutral">{metrics.get('final_equity', 0):.2f} <span style="font-size:1rem">USDT</span></div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="metric-box">
                <i class="fas fa-percentage icon-metric {'text-success' if total_return >= 0 else 'text-danger'}"></i>
                <div class="metric-title">Total Return</div>
                <div class="metric-value {'positive' if total_return >= 0 else 'negative'}">{total_return:.2f}%</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="metric-box">
                <i class="fas fa-arrow-trend-down icon-metric text-danger"></i>
                <div class="metric-title">Max Drawdown</div>
                <div class="metric-value negative">{max_dd:.2f}%</div>
            </div>
        </div>
    </div>

    <div class="row">
        <div class="col-lg-6">
            <div class="card h-100">
                <div class="card-header">
                    <i class="fas fa-tachometer-alt me-2 text-primary"></i> 績效指標 (Performance Metrics)
                </div>
                <div class="card-body p-0">
                    <table class="table table-hover mb-0">
                        <tbody>
                            <tr>
                                <td class="ps-4">總報酬率 (Total Return)</td>
                                <td class="text-end pe-4 font-weight-bold {'positive' if total_return >= 0 else 'negative'}">{total_return:.2f}%</td>
                            </tr>
                            <tr>
                                <td class="ps-4">年化報酬率 (Annualized Return)</td>
                                <td class="text-end pe-4 font-weight-bold {'positive' if annual_return >= 0 else 'negative'}">{annual_return:.2f}%</td>
                            </tr>
                            <tr>
                                <td class="ps-4">夏普比率 (Sharpe Ratio)</td>
                                <td class="text-end pe-4 font-weight-bold">{metrics.get('sharpe_ratio', 0):.2f}</td>
                            </tr>
                            <tr>
                                <td class="ps-4">索提諾比率 (Sortino Ratio)</td>
                                <td class="text-end pe-4 font-weight-bold">{metrics.get('sortino_ratio', 0):.2f}</td>
                            </tr>
                            <tr>
                                <td class="ps-4">最大回撤 (Max Drawdown)</td>
                                <td class="text-end pe-4 font-weight-bold negative">{max_dd:.2f}%</td>
                            </tr>
                            <tr>
                                <td class="ps-4">卡瑪比率 (Calmar Ratio)</td>
                                <td class="text-end pe-4 font-weight-bold">{metrics.get('calmar_ratio', 0):.2f}</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        <div class="col-lg-6">
            <div class="card h-100">
                <div class="card-header">
                    <i class="fas fa-exchange-alt me-2 text-success"></i> 交易統計 (Trade Statistics)
                </div>
                <div class="card-body p-0">
                    <table class="table table-hover mb-0">
                        <tbody>
                            <tr>
                                <td class="ps-4">總交易次數 (Total Trades)</td>
                                <td class="text-end pe-4 font-weight-bold">{metrics.get('total_trades', 0)}</td>
                            </tr>
                            <tr>
                                <td class="ps-4">勝率 (Win Rate)</td>
                                <td class="text-end pe-4 font-weight-bold { 'positive' if win_rate > 50 else 'neutral' }">{win_rate:.1f}%</td>
                            </tr>
                            <tr>
                                <td class="ps-4">獲利因子 (Profit Factor)</td>
                                <td class="text-end pe-4 font-weight-bold { 'positive' if metrics.get('profit_factor', 0) > 1 else 'negative' }">{metrics.get('profit_factor', 0):.2f}</td>
                            </tr>
                            <tr>
                                <td class="ps-4">平均獲利 (Avg Win)</td>
                                <td class="text-end pe-4 font-weight-bold positive">{metrics.get('avg_win', 0):.2f}</td>
                            </tr>
                            <tr>
                                <td class="ps-4">平均虧損 (Avg Loss)</td>
                                <td class="text-end pe-4 font-weight-bold negative">{metrics.get('avg_loss', 0):.2f}</td>
                            </tr>
                            <tr>
                                <td class="ps-4">盈虧比 (Payoff Ratio)</td>
                                <td class="text-end pe-4 font-weight-bold">{metrics.get('payoff_ratio', 0):.2f}</td>
                            </tr>
                            <tr>
                                <td class="ps-4">總手續費 (Total Fees)</td>
                                <td class="text-end pe-4 font-weight-bold neutral">{metrics.get('total_fees', 0):.2f} USDT</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    {trades_html}
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>"""
