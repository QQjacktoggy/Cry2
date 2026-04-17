"""Trade log renderer: Generates HTML trade detail tables.

Produces styled HTML tables with per-trade details including
entry/exit times, prices, PnL, fees, and holding duration.
"""

from __future__ import annotations

import pandas as pd


def render_trade_log(trades: pd.DataFrame, symbol: str = "") -> str:
    """Render trade log as an HTML table.

    Args:
        trades: DataFrame from vbt.Portfolio.trades.records_readable.
        symbol: Trading symbol for display.

    Returns:
        HTML string with styled trade table.
    """
    if trades.empty:
        return '<p class="no-data">無交易紀錄</p>'

    # Standardize column names
    col_map = _detect_columns(trades)

    rows_html = []
    total_pnl = 0.0
    total_fees = 0.0

    for idx, row in trades.iterrows():
        pnl = _safe_float(row, col_map.get("pnl"))
        fees = _safe_float(row, col_map.get("fees"))
        entry_price = _safe_float(row, col_map.get("entry_price"))
        exit_price = _safe_float(row, col_map.get("exit_price"))

        total_pnl += pnl
        total_fees += fees

        # PnL percentage
        pnl_pct = (pnl / (entry_price * _safe_float(row, col_map.get("size"), 1.0))) * 100 if entry_price > 0 else 0.0

        # Direction
        direction = _get_direction(row, col_map)

        # Holding duration
        entry_time = _safe_str(row, col_map.get("entry_time"))
        exit_time = _safe_str(row, col_map.get("exit_time"))

        # Row color
        row_class = "profit" if pnl >= 0 else "loss"

        row_num = idx + 1 if isinstance(idx, int) else idx

        rows_html.append(f"""
        <tr class="{row_class}">
            <td>{row_num}</td>
            <td>{direction}</td>
            <td>{symbol}</td>
            <td>{entry_time}</td>
            <td>{entry_price:,.4f}</td>
            <td>{exit_time}</td>
            <td>{exit_price:,.4f}</td>
            <td>{_safe_float(row, col_map.get('size')):.4f}</td>
            <td class="{'profit-text' if pnl >= 0 else 'loss-text'}">{pnl:+,.2f}</td>
            <td class="{'profit-text' if pnl_pct >= 0 else 'loss-text'}">{pnl_pct:+.2f}%</td>
            <td>{fees:,.4f}</td>
        </tr>""")

    avg_pnl = total_pnl / len(trades) if len(trades) > 0 else 0.0

    html = f"""
    <div class="trade-log">
        <table class="trades-table">
            <thead>
                <tr>
                    <th>#</th>
                    <th>方向</th>
                    <th>幣種</th>
                    <th>進場時間</th>
                    <th>進場價格</th>
                    <th>出場時間</th>
                    <th>出場價格</th>
                    <th>數量</th>
                    <th>PnL (USDT)</th>
                    <th>PnL (%)</th>
                    <th>手續費</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows_html)}
            </tbody>
            <tfoot>
                <tr class="summary-row">
                    <td colspan="8"><strong>合計</strong></td>
                    <td class="{'profit-text' if total_pnl >= 0 else 'loss-text'}"><strong>{total_pnl:+,.2f}</strong></td>
                    <td></td>
                    <td><strong>{total_fees:,.4f}</strong></td>
                </tr>
                <tr class="summary-row">
                    <td colspan="8"><strong>平均每筆</strong></td>
                    <td class="{'profit-text' if avg_pnl >= 0 else 'loss-text'}"><strong>{avg_pnl:+,.2f}</strong></td>
                    <td></td>
                    <td></td>
                </tr>
            </tfoot>
        </table>
    </div>
    """
    return html


def _detect_columns(df: pd.DataFrame) -> dict[str, str]:
    """Detect VBT trade record column names (may vary across versions).

    Args:
        df: Trades DataFrame.

    Returns:
        Dict mapping our keys to actual column names.
    """
    col_map = {}
    cols_lower = {c.lower(): c for c in df.columns}

    mappings = {
        "pnl": ["pnl", "profit"],
        "fees": ["fees", "fee"],
        "entry_price": ["entry price", "avg entry price", "entry_price"],
        "exit_price": ["exit price", "avg exit price", "exit_price"],
        "size": ["size", "quantity", "amount"],
        "entry_time": ["entry timestamp", "entry_time", "entry idx"],
        "exit_time": ["exit timestamp", "exit_time", "exit idx"],
        "direction": ["direction", "side", "type"],
    }

    for key, candidates in mappings.items():
        for candidate in candidates:
            if candidate in cols_lower:
                col_map[key] = cols_lower[candidate]
                break

    return col_map


def _safe_float(row: pd.Series, col: str | None, default: float = 0.0) -> float:
    """Safely extract float value from a row."""
    if col is None or col not in row.index:
        return default
    try:
        return float(row[col])
    except (ValueError, TypeError):
        return default


def _safe_str(row: pd.Series, col: str | None, default: str = "") -> str:
    """Safely extract string value from a row."""
    if col is None or col not in row.index:
        return default
    return str(row[col])


def _get_direction(row: pd.Series, col_map: dict) -> str:
    """Extract trade direction from row."""
    col = col_map.get("direction")
    if col and col in row.index:
        val = str(row[col]).lower()
        if "short" in val:
            return "🔴 Short"
        return "🟢 Long"
    return "Long"
