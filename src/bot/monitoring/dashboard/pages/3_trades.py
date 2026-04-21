"""Trades page - recent trade history and cumulative PnL chart."""

try:
    import streamlit as st

    from bot.monitoring.dashboard.components.journal_loader import trade_history

    st.title("📜 Trade History")

    show_open = st.checkbox("Include open (unpaired) trades", value=False)
    df = trade_history(closed_only=not show_open)

    if df.empty:
        st.info("No trade history available.")
    else:
        # Summary row
        c1, c2, c3 = st.columns(3)
        wins = df[df["PnL"] > 0] if "PnL" in df.columns else df.iloc[:0]
        c1.metric("Total Trades", len(df))
        c2.metric("Win Rate",
                  f"{len(wins) / len(df) * 100:.1f}%" if len(df) > 0 else "—")
        c3.metric("Net PnL",
                  f"{df['PnL'].sum():+.2f} USDT" if "PnL" in df.columns else "—")

        # Cumulative PnL chart
        if "PnL" in df.columns and not df["PnL"].isna().all():
            st.subheader("Cumulative PnL")
            cum_pnl = df["PnL"].cumsum().reset_index(drop=True)
            st.line_chart(cum_pnl, height=250)

        # Trade table
        st.subheader("Trades")
        display_cols = [c for c in
            ["Direction", "Entry Timestamp", "Entry Price",
             "Exit Timestamp", "Exit Price", "Size", "PnL", "Fees"]
            if c in df.columns]
        st.dataframe(df[display_cols], use_container_width=True)

except ImportError:
    pass
