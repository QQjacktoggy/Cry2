"""Overview page - equity curves, rolling Sharpe, and correlation matrix."""

try:
    import streamlit as st

    from bot.monitoring.dashboard.components.journal_loader import load_journal

    st.title("📊 Overview")

    journal = load_journal()
    if journal is None:
        st.info("No trade data found. Start the bot to see live data.")
    else:
        try:
            from bot.portfolio.trade_analyzer import TradeAnalyzer

            analyzer = TradeAnalyzer(journal)
            curves = analyzer.build_equity_curves()

            if not curves:
                st.info("No fills recorded yet.")
            else:
                # Equity curves
                st.subheader("Equity Curves by Strategy")
                import pandas as pd
                eq_df = pd.DataFrame(curves)
                st.line_chart(eq_df, height=300)

                # Rolling Sharpe
                st.subheader("Rolling Sharpe (30-bar window)")
                sharpe_data = {}
                for strat in curves:
                    s = analyzer.rolling_sharpe(strat)
                    if not s.empty:
                        sharpe_data[strat] = s
                if sharpe_data:
                    st.line_chart(pd.DataFrame(sharpe_data), height=250)

                # Correlation matrix
                if len(curves) >= 2:
                    st.subheader("Strategy Correlation Matrix")
                    corr = analyzer.correlation_matrix()
                    st.dataframe(corr.style.background_gradient(
                        cmap="RdYlGn", vmin=-1, vmax=1
                    ), use_container_width=True)
        finally:
            journal.close()

except ImportError:
    pass
