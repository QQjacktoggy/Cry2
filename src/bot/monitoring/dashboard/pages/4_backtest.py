"""Backtest / Analytics page - Monte Carlo simulation and strategy health."""

try:
    import streamlit as st

    from bot.monitoring.dashboard.components.journal_loader import load_journal

    st.title("🔬 Analytics")

    journal = load_journal()
    if journal is None:
        st.info("No trade data found. Start the bot to see live data.")
    else:
        try:
            from bot.portfolio.trade_analyzer import TradeAnalyzer
            import pandas as pd

            analyzer = TradeAnalyzer(journal)
            curves = analyzer.build_equity_curves()

            if not curves:
                st.info("No fills recorded yet. Run the bot to generate trade data.")
            else:
                strategy_names = list(curves.keys())

                # Strategy health report
                st.subheader("Strategy Health Report")
                health = analyzer.health_report()
                if health:
                    rows = []
                    for strat, metrics in health.items():
                        rows.append({
                            "Strategy": strat,
                            "Sharpe (30d)": round(metrics.get("sharpe_30", 0) or 0, 3),
                            "Sharpe (90d)": round(metrics.get("sharpe_90", 0) or 0, 3),
                            "In Decay": "⚠️ Yes" if metrics.get("in_decay") else "✅ No",
                        })
                    st.dataframe(pd.DataFrame(rows).set_index("Strategy"),
                                 use_container_width=True)

                st.markdown("---")

                # Monte Carlo simulation
                st.subheader("Monte Carlo Simulation")
                selected = st.selectbox("Select strategy", strategy_names)

                if st.button("Run Monte Carlo (1000 simulations)"):
                    with st.spinner("Simulating…"):
                        result = analyzer.monte_carlo(selected, n_simulations=1000)

                    if "error" in result:
                        st.warning(result["error"])
                    else:
                        c1, c2, c3 = st.columns(3)
                        c1.metric("5th Percentile", f"{result.get('p5', 0):,.0f} USDT")
                        c2.metric("Median", f"{result.get('p50', 0):,.0f} USDT")
                        c3.metric("95th Percentile", f"{result.get('p95', 0):,.0f} USDT")

                        if "final_equity_dist" in result:
                            st.subheader("Final Equity Distribution")
                            dist = pd.Series(result["final_equity_dist"])
                            st.bar_chart(dist.value_counts(bins=30).sort_index(),
                                        height=200)
        finally:
            journal.close()

except ImportError:
    pass
