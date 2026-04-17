"""Overview page - account summary and equity curve."""

try:
    import streamlit as st

    st.title("📊 Overview")
    st.markdown("Account summary, equity curve, and PnL breakdown.")

    st.subheader("Equity Curve")
    st.info("No data available. Start the bot to see live equity data.")

    st.subheader("Strategy Allocation")
    st.info("No strategy data available.")

except ImportError:
    pass
