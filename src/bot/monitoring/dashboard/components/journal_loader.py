"""Shared helper for loading TradeJournal data in dashboard pages."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# Allow importing bot package when running via `streamlit run`
_src = Path(__file__).parent.parent.parent.parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

_DEFAULT_DB = Path(__file__).parents[7] / "data" / "trades.db"


def _find_db() -> Path | None:
    candidates = [
        _DEFAULT_DB,
        Path("data/trades.db"),
        Path("./data/trades.db"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def load_journal():
    """Return a TradeJournal connected to the default DB, or None if not found."""
    try:
        from bot.portfolio.trade_journal import TradeJournal
        db = _find_db()
        if db is None:
            return None
        return TradeJournal(db)
    except Exception:
        return None


def summary_metrics() -> dict:
    """Return TradeJournal summary dict, or zeros if DB not available."""
    j = load_journal()
    if j is None:
        return {"total_fills": 0, "total_trades": 0, "win_rate": 0.0,
                "net_pnl": 0.0, "total_fees": 0.0}
    try:
        return j.summary()
    finally:
        j.close()


def open_positions() -> pd.DataFrame:
    """Return open_trades DataFrame, or empty DataFrame."""
    j = load_journal()
    if j is None:
        return pd.DataFrame()
    try:
        return j.open_trades()
    finally:
        j.close()


def trade_history(closed_only: bool = True) -> pd.DataFrame:
    """Return export_trades DataFrame, or empty DataFrame."""
    j = load_journal()
    if j is None:
        return pd.DataFrame()
    try:
        return j.export_trades(closed_only=closed_only)
    finally:
        j.close()
