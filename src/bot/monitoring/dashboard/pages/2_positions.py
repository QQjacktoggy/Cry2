"""Positions page - current open positions."""

try:
    import streamlit as st

    st.title("📋 Positions")
    st.markdown("Current open positions across all strategies.")
    st.info("No open positions.")

except ImportError:
    pass
