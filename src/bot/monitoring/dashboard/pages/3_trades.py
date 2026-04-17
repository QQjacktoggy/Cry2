"""Trades page - recent trade history."""

try:
    import streamlit as st

    st.title("📜 Trade History")
    st.markdown("Recent trades with filtering options.")
    st.info("No trade history available.")

except ImportError:
    pass
