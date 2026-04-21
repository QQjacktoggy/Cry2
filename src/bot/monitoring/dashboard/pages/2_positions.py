"""Positions page - current open positions."""

try:
    import streamlit as st

    from bot.monitoring.dashboard.components.journal_loader import open_positions

    st.title("📋 Open Positions")
    st.markdown("Current open positions across all strategies.")

    df = open_positions()

    if df.empty:
        st.info("No open positions.")
    else:
        st.metric("Open Positions", len(df))
        display_cols = [c for c in
            ["strategy", "symbol", "direction", "entry_time", "entry_price", "size"]
            if c in df.columns]
        st.dataframe(df[display_cols], use_container_width=True)

except ImportError:
    pass
