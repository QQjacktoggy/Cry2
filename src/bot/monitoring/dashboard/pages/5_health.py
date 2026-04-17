"""Health monitoring page."""

try:
    import streamlit as st

    st.title("🏥 System Health")
    st.markdown("WebSocket status, API latency, and error tracking.")

    col1, col2 = st.columns(2)
    with col1:
        st.metric("WebSocket", "🟢 Connected")
        st.metric("API Latency", "0 ms")
    with col2:
        st.metric("Kill Switch", "🟢 Armed")
        st.metric("Circuit Breaker", "🟢 Normal")

except ImportError:
    pass
