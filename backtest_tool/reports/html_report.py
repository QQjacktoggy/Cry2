"""HTML Report Generator: Assembles charts, tables, and metrics into self-contained HTML.

Produces single-file HTML reports that embed all charts (via Plotly CDN)
and styled tables for offline viewing.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from backtest_tool.reports.charts import (
    daily_pnl_bar,
    drawdown_chart,
    equity_curve_chart,
    equity_overlay,
    monthly_heatmap,
    trade_pnl_histogram,
)
from backtest_tool.reports.tearsheet import render_monthly_table, render_yearly_table
from backtest_tool.reports.trade_log import render_trade_log

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _format_number(value: float | int, decimals: int = 2) -> str:
    """Format number with thousands separator."""
    try:
        return f"{float(value):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


def _format_pct(value: float) -> str:
    """Format value as percentage."""
    try:
        return f"{float(value) * 100:.1f}%"
    except (ValueError, TypeError):
        return str(value)


def _get_jinja_env() -> Environment:
    """Create Jinja2 environment with custom filters."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["format_number"] = _format_number
    env.filters["format_number_4"] = lambda v: _format_number(v, 4)
    env.filters["format_pct"] = _format_pct
    return env


class HTMLReportGenerator:
    """Generates self-contained HTML backtest reports.

    Supports two modes:
    - Single strategy report (generate_single)
    - Multi-strategy comparison report (generate_comparison)
    """

    def __init__(self, output_dir: str | Path = None):
        """Initialize report generator.

        Args:
            output_dir: Output directory for HTML files. Defaults to reports/output/.
        """
        self.output_dir = Path(output_dir) if output_dir else (Path(__file__).parent / "output")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.env = _get_jinja_env()

    def generate_single(self, result, filename: str = None) -> Path:
        """Generate single strategy backtest report.

        Args:
            result: BacktestResult dataclass from engine.runner.
            filename: Output filename (without .html). Auto-generated if None.

        Returns:
            Path to the generated HTML file.
        """
        template = self.env.get_template("summary.html")

        equity = result.portfolio.value()

        # Build metrics cards
        metrics = result.metrics
        metrics_cards = _build_metrics_cards(metrics)

        # Generate charts
        equity_chart_html = equity_curve_chart(equity)
        drawdown_chart_html = drawdown_chart(equity)
        monthly_heatmap_html = monthly_heatmap(equity)
        daily_pnl_html = daily_pnl_bar(equity)

        # Trade log and histogram
        trades = result.trades
        pnl_histogram_html = trade_pnl_histogram(trades)
        trade_log_html = render_trade_log(trades, symbol=result.symbol)

        # Tearsheets
        monthly_table_html = render_monthly_table(equity)
        yearly_table_html = render_yearly_table(equity)

        # Config display
        params_yaml = yaml.dump(result.params, default_flow_style=False, allow_unicode=True)
        config_yaml = f"Maker Fee: {metrics.get('maker_fee', 'N/A')}\n" \
                      f"Taker Fee: {metrics.get('taker_fee', 'N/A')}\n" \
                      f"Slippage: {metrics.get('slippage_bps', 'N/A')} bps"

        html = template.render(
            title=f"{result.strategy_name} 回測報告",
            strategy_name=result.strategy_name,
            symbol=result.symbol,
            timeframe=result.timeframe,
            start_date=str(equity.index[0].date()) if len(equity) > 0 else "",
            end_date=str(equity.index[-1].date()) if len(equity) > 0 else "",
            initial_capital=metrics.get("initial_capital", 10000),
            leverage=metrics.get("leverage", 1),
            fee_rate=metrics.get("taker_fee", 0.04),
            slippage_bps=metrics.get("slippage_bps", 2),
            metrics_cards=metrics_cards,
            equity_chart=equity_chart_html,
            drawdown_chart=drawdown_chart_html,
            monthly_heatmap=monthly_heatmap_html,
            daily_pnl_chart=daily_pnl_html,
            pnl_histogram=pnl_histogram_html,
            monthly_table=monthly_table_html,
            yearly_table=yearly_table_html,
            trade_log=trade_log_html,
            params_yaml=params_yaml,
            config_yaml=config_yaml,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        if filename is None:
            filename = f"{result.strategy_name}_{result.symbol}_{datetime.now():%Y%m%d_%H%M%S}"

        output_path = self.output_dir / f"{filename}.html"
        output_path.write_text(html, encoding="utf-8")

        return output_path

    def generate_comparison(self, results: list, filename: str = None, optimization_result: dict = None) -> Path:
        """Generate multi-strategy comparison report.

        Args:
            results: List of BacktestResult dataclasses.
            filename: Output filename (without .html).
            optimization_result: Optional portfolio optimization result dict.

        Returns:
            Path to the generated HTML file.
        """
        template = self.env.get_template("comparison.html")

        strategy_names = [r.strategy_name for r in results]
        symbols = list(set(r.symbol for r in results))

        # Equity curves for overlay
        equities = {}
        for r in results:
            eq = r.portfolio.value()
            equities[r.strategy_name] = eq
        overlay_html = equity_overlay(equities)

        # Comparison table rows
        comparison_metrics = [
            ("總收益率", "total_return", "%"),
            ("年化收益率", "annual_return", "%"),
            ("Sharpe Ratio", "sharpe_ratio", ""),
            ("Sortino Ratio", "sortino_ratio", ""),
            ("Calmar Ratio", "calmar_ratio", ""),
            ("最大回撤", "max_drawdown", "%"),
            ("勝率", "win_rate", "%"),
            ("盈虧比", "profit_factor", ""),
            ("總交易次數", "total_trades", ""),
        ]

        comparison_rows = []
        for label, key, fmt in comparison_metrics:
            values = []
            raw_vals = [r.metrics.get(key, 0) for r in results]
            best_idx = _find_best_idx(raw_vals, key)

            for i, r in enumerate(results):
                val = r.metrics.get(key, 0)
                display = _format_metric_value(val, fmt)
                css_class = ""
                if key in ("max_drawdown",):
                    css_class = "loss-text"
                elif val > 0 and fmt == "%":
                    css_class = "profit-text"
                elif val < 0 and fmt == "%":
                    css_class = "loss-text"
                if i == best_idx:
                    css_class += " best"
                values.append({"display": display, "css_class": css_class.strip()})
            comparison_rows.append({"label": label, "values": values})

        # Per-strategy sections
        strategy_sections = {}
        for r in results:
            strategy_sections[r.strategy_name] = {
                "metrics_cards": _build_metrics_cards(r.metrics),
            }

        # First equity for date range
        first_eq = results[0].portfolio.value()

        html = template.render(
            title="多策略比較報告",
            strategy_names=strategy_names,
            symbols=symbols,
            start_date=str(first_eq.index[0].date()) if len(first_eq) > 0 else "",
            end_date=str(first_eq.index[-1].date()) if len(first_eq) > 0 else "",
            initial_capital=results[0].metrics.get("initial_capital", 10000),
            comparison_rows=comparison_rows,
            equity_overlay=overlay_html,
            strategy_sections=strategy_sections,
            optimization_result=optimization_result,
            optimization_target=optimization_result.get("target", "") if optimization_result else "",
            top_allocations=optimization_result.get("top_allocations", []) if optimization_result else [],
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        if filename is None:
            filename = f"comparison_{datetime.now():%Y%m%d_%H%M%S}"

        output_path = self.output_dir / f"{filename}.html"
        output_path.write_text(html, encoding="utf-8")

        return output_path


def _build_metrics_cards(metrics: dict) -> list[dict]:
    """Build metrics card data for template rendering."""
    card_defs = [
        ("總收益率", "total_return", "%"),
        ("年化收益率", "annual_return", "%"),
        ("最大回撤", "max_drawdown", "%"),
        ("Sharpe Ratio", "sharpe_ratio", ""),
        ("Sortino Ratio", "sortino_ratio", ""),
        ("Calmar Ratio", "calmar_ratio", ""),
        ("勝率", "win_rate", "%"),
        ("盈虧比", "profit_factor", "x"),
        ("總交易次數", "total_trades", ""),
        ("平均持倉天數", "avg_holding_days", "d"),
        ("最終價值", "final_value", "$"),
        ("總淨利", "total_pnl", "$"),
    ]

    cards = []
    for label, key, fmt in card_defs:
        val = metrics.get(key)
        if val is None:
            continue

        display = _format_metric_value(val, fmt)
        css_class = ""
        if fmt == "%" and key not in ("max_drawdown", "win_rate"):
            css_class = "profit-text" if val >= 0 else "loss-text"
        elif key == "max_drawdown":
            css_class = "loss-text"
        elif key == "total_pnl":
            css_class = "profit-text" if val >= 0 else "loss-text"
        elif key in ("sharpe_ratio", "sortino_ratio", "calmar_ratio"):
            css_class = "profit-text" if val > 0 else "loss-text"

        cards.append({"label": label, "value": display, "css_class": css_class})

    return cards


def _format_metric_value(val: Any, fmt: str) -> str:
    """Format a metric value based on format hint."""
    try:
        fval = float(val)
    except (ValueError, TypeError):
        return str(val)

    if fmt == "%":
        return f"{fval:+.2f}%"
    elif fmt == "$":
        return f"${fval:,.2f}"
    elif fmt == "x":
        return f"{fval:.2f}x"
    elif fmt == "d":
        return f"{fval:.1f}"
    else:
        if isinstance(val, int) or (isinstance(val, float) and val == int(val)):
            return f"{int(val):,}"
        return f"{fval:.4f}"


def _find_best_idx(values: list, key: str) -> int:
    """Find index of the best value for a given metric."""
    try:
        if key in ("max_drawdown",):
            return int(max(range(len(values)), key=lambda i: values[i]))
        return int(max(range(len(values)), key=lambda i: values[i]))
    except (ValueError, TypeError):
        return 0
