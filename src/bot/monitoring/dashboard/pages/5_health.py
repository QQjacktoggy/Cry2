"""System health monitoring page.

Shows live metrics from the TradeJournal, circuit breaker status,
kill switch state, and reconcile info read from a shared state file
written by run_live.py / run_paper.py.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Allow importing bot package when running via `streamlit run`
_src = Path(__file__).parent.parent.parent.parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

try:
    import streamlit as st
    from components.journal_loader import open_positions, summary_metrics

    st.title("System Health")

    # ── Runtime state file ─────────────────────────────────────────────────
    # run_live.py / run_paper.py can write a JSON snapshot to data/health.json
    # so the dashboard can read live bot state without importing live modules.
    _STATE_CANDIDATES = [
        Path(__file__).parents[7] / "data" / "health.json",
        Path("data/health.json"),
        Path("./data/health.json"),
    ]

    def _load_health_state() -> dict:
        for p in _STATE_CANDIDATES:
            if p.exists():
                try:
                    return json.loads(p.read_text())
                except Exception:
                    pass
        return {}

    state = _load_health_state()
    has_state = bool(state)

    # ── Trade stats from journal ───────────────────────────────────────────
    st.subheader("Trade Statistics")
    metrics = summary_metrics()
    positions = open_positions()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Fills", metrics.get("total_fills", 0))
    col2.metric("Closed Trades", metrics.get("total_trades", 0))
    col3.metric(
        "Win Rate",
        f"{metrics.get('win_rate', 0.0) * 100:.1f}%",
    )
    col4.metric("Open Positions", len(positions))

    col5, col6 = st.columns(2)
    col5.metric("Net PnL (USDT)", f"{metrics.get('net_pnl', 0.0):.2f}")
    col6.metric("Total Fees (USDT)", f"{metrics.get('total_fees', 0.0):.4f}")

    st.divider()

    # ── Bot runtime status ─────────────────────────────────────────────────
    st.subheader("Bot Runtime Status")

    if not has_state:
        st.info("No health.json found — bot may not be running or has not written a snapshot yet.")
    else:
        # WebSocket / connection
        ws_ok = state.get("ws_connected", False)
        uds_ok = state.get("user_data_stream_ok", False)
        last_bar = state.get("last_bar_ts", "—")
        last_reconcile = state.get("last_reconcile_ts", "—")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Market WebSocket", "Connected" if ws_ok else "Disconnected",
                  delta_color="normal" if ws_ok else "inverse")
        c2.metric("User Data Stream", "OK" if uds_ok else "Down",
                  delta_color="normal" if uds_ok else "inverse")
        c3.metric("Last Bar", last_bar)
        c4.metric("Last Reconcile", last_reconcile)

        st.divider()

        # Kill switch
        st.subheader("Kill Switch & Circuit Breaker")
        ks_triggered = state.get("kill_switch_triggered", False)
        cb_tripped = state.get("circuit_breaker_tripped", False)
        cb_symbol = state.get("circuit_breaker_symbol", "")
        api_latency_ms = state.get("api_latency_ms", None)

        d1, d2, d3 = st.columns(3)
        d1.metric(
            "Kill Switch",
            "TRIGGERED" if ks_triggered else "Armed",
        )
        d2.metric(
            "Circuit Breaker",
            f"TRIPPED ({cb_symbol})" if cb_tripped else "Normal",
        )
        if api_latency_ms is not None:
            d3.metric("API Latency", f"{api_latency_ms:.0f} ms")
        else:
            d3.metric("API Latency", "—")

        st.divider()

        # Risk manager
        st.subheader("Risk Manager")
        risk = state.get("risk_manager", {})
        if risk:
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Daily PnL", f"{risk.get('daily_pnl', 0.0):.2f}")
            r2.metric("Weekly PnL", f"{risk.get('weekly_pnl', 0.0):.2f}")
            r3.metric("Drawdown %", f"{risk.get('drawdown_pct', 0.0):.1f}%")
            r4.metric("Halted", "YES" if risk.get("is_halted", False) else "No")

            cooldowns = risk.get("strategy_cooldowns", {})
            if cooldowns:
                st.warning(f"Strategy cooldowns active: {cooldowns}")
        else:
            st.info("Risk manager state not available in snapshot.")

        st.divider()

        # Regime / ATR
        st.subheader("Market Regime & Adaptive Leverage")
        regime_info = state.get("regime", {})
        if regime_info:
            reg1, reg2, reg3 = st.columns(3)
            reg1.metric("Regime", regime_info.get("regime", "—").upper())
            reg2.metric("ADX", f"{regime_info.get('adx', 0.0):.1f}")
            reg3.metric(
                "Effective Leverage",
                f"{regime_info.get('effective_leverage', '—')}x",
            )
        else:
            st.info("Regime data not available in snapshot.")

        # Raw state expander
        with st.expander("Raw health.json"):
            st.json(state)

    # ── Refresh ────────────────────────────────────────────────────────────
    st.divider()
    if st.button("Refresh"):
        st.rerun()

    # Auto-refresh every 30 s
    try:
        st_autorefresh = st.components.v1.html
        st.caption("Page refreshes automatically every 30 seconds.")
        import time as _time
        _time  # suppress unused import
    except Exception:
        pass

except ImportError:
    pass
