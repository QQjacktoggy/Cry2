"""Streamlit Dashboard - main entry point.

Run with: streamlit run src/bot/monitoring/dashboard/app.py
"""

from __future__ import annotations

try:
    import streamlit as st

    from bot.monitoring.dashboard.components.journal_loader import summary_metrics

    st.set_page_config(
        page_title="Binance Futures Bot",
        page_icon="🤖",
        layout="wide",
    )

    st.title("🤖 Binance Futures Bot Dashboard")
    st.markdown("---")

    metrics = summary_metrics()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        pnl = metrics.get("net_pnl", 0.0)
        st.metric("Net PnL", f"{pnl:+.2f} USDT")
    with col2:
        win_rate = metrics.get("win_rate", 0.0)
        st.metric("Win Rate", f"{win_rate * 100:.1f}%")
    with col3:
        st.metric("Total Trades", metrics.get("total_trades", 0))
    with col4:
        total_fees = metrics.get("total_fees", 0.0)
        st.metric("Total Fees", f"{total_fees:.2f} USDT")

    if metrics.get("total_trades", 0) > 0:
        st.markdown("---")
        extra = st.columns(3)
        with extra[0]:
            st.metric("Avg PnL / Trade", f"{metrics.get('avg_pnl', 0.0):+.2f} USDT")
        with extra[1]:
            st.metric("Best Trade", f"{metrics.get('best_trade', 0.0):+.2f} USDT")
        with extra[2]:
            st.metric("Worst Trade", f"{metrics.get('worst_trade', 0.0):+.2f} USDT")

    st.markdown("---")
    st.markdown(
        "Navigate to specific pages using the sidebar:\n"
        "- **Overview**: Account summary and equity curve\n"
        "- **Positions**: Current open positions\n"
        "- **Trades**: Recent trade history\n"
        "- **Backtest**: Backtest results viewer\n"
        "- **Health**: System health monitoring"
    )

    if metrics.get("total_fills", 0) == 0:
        st.info("No trade data found. Start the bot to see live data.")

except ImportError:
    print("Streamlit not installed. Run: pip install streamlit")
