#!/usr/bin/env python3
"""Paper run review pipeline.

Feeds a ``trade journal`` (fills / trades / runtime_events / equity_snapshots)
into four analyst-style sections:

1. Per-strategy PnL and win rate
2. Reject-reason summary
3. Reconcile anomalies (latency spikes, reconcile delta)
4. Runtime incidents (WS drops, kill switch, halts)

Scope is deliberately narrow: the goal is to surface **outliers** that
warrant engineer eyeballs, not to replace a human analyst.

Usage::

    python scripts/paper_review.py --journal ./data/paper_trades.db
    python scripts/paper_review.py --journal ./data/paper_trades.db --run-id <uuid>
    python scripts/paper_review.py --journal ./data/paper_trades.db --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pandas as pd  # noqa: E402

from bot.portfolio.trade_journal import TradeJournal  # noqa: E402


def _per_strategy_pnl(journal: TradeJournal, run_id: str | None) -> dict[str, Any]:
    trades = journal.export_trades()
    if trades.empty:
        return {"strategies": [], "totals": {}}

    if run_id and "run_id" in trades.columns:
        trades = trades[trades["run_id"] == run_id]

    if trades.empty:
        return {"strategies": [], "totals": {}}

    grouped = trades.groupby("strategy")
    rows = []
    for name, g in grouped:
        pnl = g["PnL"]
        wins = pnl[pnl > 0]
        rows.append(
            {
                "strategy": name,
                "trades": int(len(g)),
                "net_pnl": float(pnl.sum()),
                "avg_pnl": float(pnl.mean()),
                "win_rate": float(len(wins) / len(g)) if len(g) else 0.0,
                "worst_trade": float(pnl.min()) if len(g) else 0.0,
                "best_trade": float(pnl.max()) if len(g) else 0.0,
            }
        )

    rows.sort(key=lambda r: r["net_pnl"])
    totals = {
        "trades": int(len(trades)),
        "net_pnl": float(trades["PnL"].sum()),
        "win_rate": (
            float((trades["PnL"] > 0).mean()) if len(trades) else 0.0
        ),
    }
    return {"strategies": rows, "totals": totals}


def _reject_summary(journal: TradeJournal, run_id: str | None) -> list[dict[str, Any]]:
    events = journal.export_runtime_events(event_type="signal_rejected", run_id=run_id)
    if events.empty:
        return []

    reasons: dict[str, int] = {}
    for raw in events["details"].fillna("{}"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        key = str(parsed.get("reason", "unknown"))[:120]
        reasons[key] = reasons.get(key, 0) + 1

    return sorted(
        ({"reason": k, "count": v} for k, v in reasons.items()),
        key=lambda r: -r["count"],
    )


def _reconcile_anomalies(
    journal: TradeJournal,
    run_id: str | None,
    latency_threshold_ms: float = 500.0,
) -> dict[str, Any]:
    events = journal.export_runtime_events(event_type="reconcile", run_id=run_id)
    if events.empty:
        return {"count": 0, "latency_spikes": []}

    spikes: list[dict[str, Any]] = []
    for ts, raw in zip(events["timestamp"], events["details"].fillna("{}")):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        latency = float(parsed.get("api_latency_ms", 0.0))
        if latency >= latency_threshold_ms:
            spikes.append(
                {"timestamp": ts, "api_latency_ms": round(latency, 1)}
            )

    return {"count": int(len(events)), "latency_spikes": spikes}


def _runtime_incidents(journal: TradeJournal, run_id: str | None) -> list[dict[str, Any]]:
    events = journal.export_runtime_events(run_id=run_id)
    if events.empty:
        return []

    interesting = events[
        events["event_type"].isin(["ws_disconnect", "kill_switch"])
        | (events["severity"].isin(["warning", "critical"]))
    ]
    out = []
    for _, row in interesting.iterrows():
        try:
            detail = json.loads(row["details"])
        except Exception:
            detail = {}
        out.append(
            {
                "timestamp": row["timestamp"],
                "event_type": row["event_type"],
                "severity": row["severity"],
                "strategy": row["strategy"],
                "symbol": row["symbol"],
                "details": detail,
            }
        )
    return out


def build_report(journal_db: str | Path, run_id: str | None = None) -> dict[str, Any]:
    journal = TradeJournal(journal_db)
    try:
        return {
            "journal": str(journal_db),
            "run_id": run_id,
            "per_strategy_pnl": _per_strategy_pnl(journal, run_id),
            "rejects": _reject_summary(journal, run_id),
            "reconcile": _reconcile_anomalies(journal, run_id),
            "incidents": _runtime_incidents(journal, run_id),
        }
    finally:
        journal.close()


def render_text(report: dict[str, Any]) -> str:
    lines = ["=" * 60, "📑 Paper Review", "=" * 60]
    lines.append(f"journal: {report['journal']}")
    if report.get("run_id"):
        lines.append(f"run_id: {report['run_id']}")

    lines.append("\n── Per-strategy PnL ──")
    per = report["per_strategy_pnl"]
    if not per["strategies"]:
        lines.append("  (no trades)")
    else:
        lines.append(
            f"  {'strategy':<35} {'trades':>6} {'net_pnl':>10} "
            f"{'win%':>6} {'worst':>10} {'best':>10}"
        )
        for r in per["strategies"]:
            lines.append(
                f"  {r['strategy']:<35} {r['trades']:>6} {r['net_pnl']:>10.2f} "
                f"{r['win_rate'] * 100:>5.1f}% {r['worst_trade']:>10.2f} "
                f"{r['best_trade']:>10.2f}"
            )
        totals = per["totals"]
        lines.append(
            f"  → total trades={totals['trades']} net_pnl={totals['net_pnl']:.2f} "
            f"win_rate={totals['win_rate'] * 100:.1f}%"
        )

    lines.append("\n── Reject reasons ──")
    if not report["rejects"]:
        lines.append("  (none)")
    else:
        for r in report["rejects"]:
            lines.append(f"  {r['count']:>4}× {r['reason']}")

    lines.append("\n── Reconcile anomalies ──")
    rec = report["reconcile"]
    lines.append(f"  total reconciles: {rec['count']}")
    if rec["latency_spikes"]:
        lines.append("  latency spikes (>=500ms):")
        for s in rec["latency_spikes"][:20]:
            lines.append(f"    {s['timestamp']}  {s['api_latency_ms']}ms")

    lines.append("\n── Runtime incidents ──")
    if not report["incidents"]:
        lines.append("  (none)")
    else:
        for inc in report["incidents"][:50]:
            lines.append(
                f"  [{inc['severity']}] {inc['timestamp']} {inc['event_type']} "
                f"{inc['strategy']} {inc['symbol']} {json.dumps(inc['details'])[:120]}"
            )

    lines.append("=" * 60)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise a paper run journal")
    parser.add_argument("--journal", required=True, help="SQLite journal path")
    parser.add_argument("--run-id", default=None, help="Filter to a single run")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    args = parser.parse_args(argv)

    report = build_report(args.journal, run_id=args.run_id)
    if args.json:
        def _default(o: Any) -> Any:
            if hasattr(o, "isoformat"):
                return o.isoformat()
            if isinstance(o, pd.Timestamp):
                return o.isoformat()
            return str(o)

        print(json.dumps(report, indent=2, default=_default))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
