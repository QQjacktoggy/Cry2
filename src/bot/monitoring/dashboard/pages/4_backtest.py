"""Backtest results page."""

try:
    import streamlit as st

    st.title("🔬 Backtest Results")
    st.markdown("View and compare backtest results.")
    st.info("No backtest results found. Run a backtest first.")

except ImportError:
    pass
