"""Tearsheet: Monthly and yearly performance tables.

Generates HTML tables showing monthly and yearly returns,
similar to QuantStats-style tearsheets.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def render_monthly_table(equity: pd.Series) -> str:
    """Render monthly returns table (Year × Month).

    Args:
        equity: Portfolio value Series with DatetimeIndex.

    Returns:
        HTML string with monthly returns table.
    """
    if equity.empty or len(equity) < 2:
        return '<p class="no-data">資料不足，無法產生月度表</p>'

    monthly = equity.resample("ME").last()
    monthly_returns = monthly.pct_change() * 100

    df = pd.DataFrame({
        "year": monthly_returns.index.year,
        "month": monthly_returns.index.month,
        "return": monthly_returns.values,
    })

    pivot = df.pivot_table(values="return", index="year", columns="month", aggfunc="first")

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    # Build HTML
    header_cells = "".join(f"<th>{m}</th>" for m in month_names)
    header = f"<tr><th>年份</th>{header_cells}<th>年度</th></tr>"

    rows = []
    for year in pivot.index:
        cells = []
        year_total = 0.0
        for m in range(1, 13):
            val = pivot.loc[year, m] if m in pivot.columns and not np.isnan(pivot.loc[year].get(m, np.nan)) else None
            if val is not None:
                cls = "profit-text" if val >= 0 else "loss-text"
                cells.append(f'<td class="{cls}">{val:+.1f}%</td>')
                year_total += val
            else:
                cells.append("<td>—</td>")

        # Annual total (compound)
        cls = "profit-text" if year_total >= 0 else "loss-text"
        cells.append(f'<td class="{cls}"><strong>{year_total:+.1f}%</strong></td>')
        rows.append(f"<tr><td><strong>{year}</strong></td>{''.join(cells)}</tr>")

    return f"""
    <div class="tearsheet">
        <h3>📅 月度收益明細表</h3>
        <table class="tearsheet-table">
            <thead>{header}</thead>
            <tbody>{''.join(rows)}</tbody>
        </table>
    </div>
    """


def render_yearly_table(equity: pd.Series) -> str:
    """Render yearly performance summary table.

    Args:
        equity: Portfolio value Series with DatetimeIndex.

    Returns:
        HTML string with yearly performance table.
    """
    if equity.empty or len(equity) < 2:
        return '<p class="no-data">資料不足，無法產生年度表</p>'

    rows = []
    years = sorted(equity.index.year.unique())

    for year in years:
        year_eq = equity[equity.index.year == year]
        if len(year_eq) < 2:
            continue

        start_val = year_eq.iloc[0]
        end_val = year_eq.iloc[-1]
        ret = ((end_val / start_val) - 1) * 100 if start_val > 0 else 0.0
        cls = "profit-text" if ret >= 0 else "loss-text"

        # Max drawdown for this year
        cummax = year_eq.cummax()
        dd = ((year_eq - cummax) / cummax * 100).min()

        rows.append(f"""
        <tr>
            <td><strong>{year}</strong></td>
            <td class="{cls}">{ret:+.2f}%</td>
            <td>${start_val:,.2f}</td>
            <td>${end_val:,.2f}</td>
            <td class="loss-text">{dd:.2f}%</td>
        </tr>""")

    return f"""
    <div class="tearsheet">
        <h3>📊 年度績效摘要</h3>
        <table class="tearsheet-table">
            <thead>
                <tr>
                    <th>年份</th>
                    <th>收益率</th>
                    <th>年初價值</th>
                    <th>年末價值</th>
                    <th>最大回撤</th>
                </tr>
            </thead>
            <tbody>{''.join(rows)}</tbody>
        </table>
    </div>
    """
