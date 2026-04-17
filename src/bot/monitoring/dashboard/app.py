"""Streamlit Dashboard - main entry point.

Run with: streamlit run src/bot/monitoring/dashboard/app.py
"""

from __future__ import annotations

try:
    import streamlit as st

    st.set_page_config(
        page_title="Binance Futures Bot",
        page_icon="🤖",
        layout="wide",
    )

    st.title("🤖 Binance Futures Bot Dashboard")
    st.markdown("---")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Equity", "10,000.00 USDT", "+0.00%")
    with col2:
        st.metric("Daily PnL", "+0.00 USDT", "0.00%")
    with col3:
        st.metric("Open Positions", "0")
    with col4:
        st.metric("System Status", "🟢 Healthy")

    st.markdown("---")
    st.info("Connect to a running bot instance to see live data.")
    st.markdown(
        "Navigate to specific pages using the sidebar:\n"
        "- **Overview**: Account summary and equity curve\n"
        "- **Positions**: Current open positions\n"
        "- **Trades**: Recent trade history\n"
        "- **Backtest**: Backtest results viewer\n"
        "- **Health**: System health monitoring"
    )

except ImportError:
    print("Streamlit not installed. Run: pip install streamlit")
