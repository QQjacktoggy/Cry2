"""Charts: Plotly chart generation for HTML reports.

Generates interactive Plotly charts as HTML fragments for embedding
in the self-contained HTML report.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def equity_curve_chart(equity: pd.Series, title: str = "權益曲線 (Equity Curve)") -> str:
    """Generate interactive equity curve chart.

    Args:
        equity: Portfolio value Series with DatetimeIndex.
        title: Chart title.

    Returns:
        Plotly HTML fragment string.
    """
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=equity.index,
        y=equity.values,
        mode="lines",
        name="Portfolio Value",
        line=dict(color="#00d4aa", width=2),
        hovertemplate="Date: %{x}<br>Value: $%{y:,.2f}<extra></extra>",
    ))

    fig.update_layout(
        title=title,
        xaxis_title="日期",
        yaxis_title="組合價值 (USDT)",
        template="plotly_dark",
        hovermode="x unified",
        height=450,
    )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def drawdown_chart(equity: pd.Series, title: str = "回撤曲線 (Drawdown)") -> str:
    """Generate drawdown chart.

    Args:
        equity: Portfolio value Series with DatetimeIndex.
        title: Chart title.

    Returns:
        Plotly HTML fragment string.
    """
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax * 100  # as percentage

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=drawdown.index,
        y=drawdown.values,
        fill="tozeroy",
        fillcolor="rgba(255, 65, 54, 0.3)",
        line=dict(color="#ff4136", width=1),
        name="Drawdown",
        hovertemplate="Date: %{x}<br>Drawdown: %{y:.2f}%<extra></extra>",
    ))

    fig.update_layout(
        title=title,
        xaxis_title="日期",
        yaxis_title="回撤 (%)",
        template="plotly_dark",
        height=350,
    )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def monthly_heatmap(equity: pd.Series, title: str = "月度收益熱力圖") -> str:
    """Generate monthly returns heatmap (Year × Month).

    Args:
        equity: Portfolio value Series with DatetimeIndex.
        title: Chart title.

    Returns:
        Plotly HTML fragment string.
    """
    # Calculate monthly returns
    monthly = equity.resample("ME").last()
    monthly_returns = monthly.pct_change() * 100  # percentage

    # Create pivot table: rows = year, cols = month
    df = pd.DataFrame({
        "year": monthly_returns.index.year,
        "month": monthly_returns.index.month,
        "return": monthly_returns.values,
    })

    pivot = df.pivot_table(values="return", index="year", columns="month", aggfunc="first")
    pivot.columns = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][:len(pivot.columns)]

    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=pivot.columns.tolist(),
        y=[str(y) for y in pivot.index.tolist()],
        colorscale=[[0, "#ff4136"], [0.5, "#1a1a2e"], [1, "#00d4aa"]],
        zmid=0,
        text=np.where(np.isnan(pivot.values), "", np.vectorize(lambda x: f"{x:.1f}%")(pivot.values)),
        texttemplate="%{text}",
        textfont=dict(size=11),
        hovertemplate="Year: %{y}<br>Month: %{x}<br>Return: %{z:.2f}%<extra></extra>",
    ))

    fig.update_layout(
        title=title,
        xaxis_title="月份",
        yaxis_title="年份",
        template="plotly_dark",
        height=max(250, 60 * len(pivot)),
    )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def daily_pnl_bar(equity: pd.Series, title: str = "每日損益 (Daily PnL)") -> str:
    """Generate daily PnL bar chart.

    Args:
        equity: Portfolio value Series with DatetimeIndex.
        title: Chart title.

    Returns:
        Plotly HTML fragment string.
    """
    daily = equity.resample("D").last().dropna()
    daily_pnl = daily.diff().dropna()

    colors = ["#00d4aa" if v >= 0 else "#ff4136" for v in daily_pnl.values]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=daily_pnl.index,
        y=daily_pnl.values,
        marker_color=colors,
        name="Daily PnL",
        hovertemplate="Date: %{x}<br>PnL: $%{y:,.2f}<extra></extra>",
    ))

    fig.update_layout(
        title=title,
        xaxis_title="日期",
        yaxis_title="損益 (USDT)",
        template="plotly_dark",
        height=350,
    )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def trade_pnl_histogram(trades: pd.DataFrame, title: str = "交易損益分佈") -> str:
    """Generate trade PnL distribution histogram.

    Args:
        trades: DataFrame with 'PnL' column.
        title: Chart title.

    Returns:
        Plotly HTML fragment string.
    """
    pnl_col = "PnL" if "PnL" in trades.columns else "pnl"
    if pnl_col not in trades.columns or trades.empty:
        fig = go.Figure()
        fig.update_layout(title=title, template="plotly_dark", height=350)
        return fig.to_html(full_html=False, include_plotlyjs="cdn")

    pnl = trades[pnl_col].dropna()

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=pnl.values,
        nbinsx=50,
        marker_color="#00d4aa",
        opacity=0.75,
        name="PnL Distribution",
        hovertemplate="PnL: $%{x:,.2f}<br>Count: %{y}<extra></extra>",
    ))

    # Add mean line
    mean_pnl = pnl.mean()
    fig.add_vline(x=mean_pnl, line_dash="dash", line_color="yellow",
                  annotation_text=f"Mean: ${mean_pnl:.2f}")

    fig.update_layout(
        title=title,
        xaxis_title="交易損益 (USDT)",
        yaxis_title="次數",
        template="plotly_dark",
        height=350,
    )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def equity_overlay(
    equities: dict[str, pd.Series],
    title: str = "策略比較 (Strategy Comparison)",
) -> str:
    """Generate overlaid equity curves for multiple strategies.

    Args:
        equities: Dict mapping strategy name to equity Series.
        title: Chart title.

    Returns:
        Plotly HTML fragment string.
    """
    colors = ["#00d4aa", "#ff6b6b", "#4ecdc4", "#ffa502", "#a29bfe", "#fd79a8"]

    fig = go.Figure()
    for i, (name, equity) in enumerate(equities.items()):
        color = colors[i % len(colors)]
        # Normalize to percentage return
        normalized = (equity / equity.iloc[0] - 1) * 100

        fig.add_trace(go.Scatter(
            x=normalized.index,
            y=normalized.values,
            mode="lines",
            name=name,
            line=dict(color=color, width=2),
            hovertemplate=f"{name}<br>Date: %{{x}}<br>Return: %{{y:.2f}}%<extra></extra>",
        ))

    fig.update_layout(
        title=title,
        xaxis_title="日期",
        yaxis_title="累積收益 (%)",
        template="plotly_dark",
        hovermode="x unified",
        height=500,
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
    )

    return fig.to_html(full_html=False, include_plotlyjs="cdn")
