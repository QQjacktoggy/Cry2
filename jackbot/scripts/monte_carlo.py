"""monte_carlo.py — Bootstrap robustness for Jackbot_V1.

Two complementary modes:

  --mode=trade            Trade-level MC: shuffles per-grid PnL list 10000×
                          to assess equity-curve / drawdown / ruin distribution
                          assuming trades are IID.

  --mode=walk_forward     Window-level bootstrap: resamples the 21 OOS windows
                          from data/walk_forward_results.json with replacement
                          to build a CI for "next 6 months total profit".

  --mode=both             Run both and write a unified JSON report.

Outputs:
  data/monte_carlo_trade.json
  data/monte_carlo_walk_forward.json
  data/monte_carlo_combined.json   (both mode)

Reuses MonteCarloSimulator from the parent project's src/bot/backtest/monte_carlo.py
to keep MC algorithm in a single source of truth.

Usage:
  python scripts/monte_carlo.py --mode=trade --sims=10000 --seed=42
  python scripts/monte_carlo.py --mode=walk_forward --sims=10000 --seed=42
  python scripts/monte_carlo.py --mode=both --sims=10000 --days=180
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent       # jackbot/
sys.path.insert(0, str(ROOT / "src"))               # jackbot.* package
sys.path.insert(0, str(ROOT))                       # scripts.backtest
sys.path.insert(0, str(ROOT.parent / "src"))        # bot.backtest.monte_carlo

# Fix Windows console encoding for Chinese + emoji
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bot.backtest.monte_carlo import MonteCarloSimulator
from jackbot.strategy.day_trader import DayTraderConfig
from scripts.backtest import BacktestEngine, download_klines


# ── Trade-level MC ────────────────────────────────────────────────────


def run_trade_mc(
    days: int,
    capital: float,
    sims: int,
    seed: int,
    maker_fee: float,
    taker_fee: float,
) -> dict:
    """Backtest the last `days` of BTC/ETH klines, then MC-shuffle the trade PnL list."""
    end_dt = datetime.now(UTC)
    start_dt = end_dt - timedelta(days=days)
    start_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)

    symbols = ["BTCUSDC", "ETHUSDC"]
    print(f"\n📥 下載 {days} 天 K 線 ({start_dt.date()} → {end_dt.date()})")
    klines_by_symbol: dict[str, list[dict]] = {}
    for symbol in symbols:
        print(f"   {symbol}...", end=" ", flush=True)
        klines = download_klines(symbol, "5m", start_ts, end_ts)
        klines_by_symbol[symbol] = klines
        print(f"{len(klines):,} 根")

    config = DayTraderConfig(
        symbols=symbols,
        timeframe="5m",
        total_capital_usd=capital,
        per_symbol_alloc_pct=50.0,
        max_leverage=10,
    )

    print(f"\n⚙️  跑 1 次基準 backtest 取得 trade PnL list")
    engine = BacktestEngine(config, maker_rate=maker_fee, taker_rate=taker_fee)
    base_results = engine.run(klines_by_symbol)
    trade_pnls = list(engine._all_profits)

    if not trade_pnls:
        print("❌ 沒有產生任何 trade — backtest 區間或參數有問題")
        return {"mode": "trade", "error": "no_trades"}

    print(f"   總交易筆數: {len(trade_pnls)}")
    print(f"   實際總獲利: {sum(trade_pnls):.2f}")

    print(f"\n🎲 蒙地卡羅模擬 {sims} 次 (seed={seed})...")
    mc = MonteCarloSimulator(num_simulations=sims, seed=seed)
    mc_stats = mc.run(trade_pnls=trade_pnls, initial_capital=capital)

    return {
        "mode": "trade",
        "config": {
            "days": days,
            "capital": capital,
            "sims": sims,
            "seed": seed,
            "maker_fee": maker_fee,
            "taker_fee": taker_fee,
        },
        "baseline": {
            "num_trades": len(trade_pnls),
            "actual_total_profit": sum(trade_pnls),
            "actual_final_equity": capital + sum(trade_pnls),
        },
        "monte_carlo": mc_stats,
        "caveats": [
            "Shuffling breaks path dependency — MC max_drawdown 通常比實際歷史樂觀。",
            "假設 trades 為 IID。Grid 同日多筆 trade 之間有 regime 相關性, 此假設只是近似。",
            "ruin = final_equity < 50% 起始資本; profit = final_equity > 起始資本",
        ],
    }


# ── Walk-forward window-level bootstrap ───────────────────────────────


def run_walk_forward_bootstrap(sims: int, seed: int) -> dict:
    """Bootstrap the 21 OOS windows from walk_forward_results.json with replacement."""
    wf_path = ROOT / "data" / "walk_forward_results.json"
    if not wf_path.exists():
        print(f"❌ {wf_path} 不存在 — 請先跑 walk_forward.py")
        return {"mode": "walk_forward", "error": "no_walk_forward_results"}

    with open(wf_path, encoding="utf-8") as f:
        wf = json.load(f)

    windows = wf["windows"]
    n_windows = len(windows)
    if n_windows == 0:
        return {"mode": "walk_forward", "error": "no_windows"}

    net_profits = np.array([w["net_profit"] for w in windows])
    roi_pcts = np.array([w["roi_pct"] for w in windows])

    print(f"\n📊 讀取 {n_windows} 個 OOS window")
    print(f"   實際總 net_profit: {net_profits.sum():.2f}")
    print(f"   實際平均 ROI: {roi_pcts.mean():.2f}%")
    print(f"   正報酬窗口: {(net_profits > 0).sum()}/{n_windows}")

    rng = np.random.default_rng(seed)

    print(f"\n🎲 Bootstrap {sims} 次 (有放回, 每次抽 {n_windows} 個 window)...")
    sim_total_profits = np.empty(sims)
    sim_avg_rois = np.empty(sims)
    sim_pos_rates = np.empty(sims)
    for i in range(sims):
        idx = rng.integers(0, n_windows, size=n_windows)
        sim_total_profits[i] = net_profits[idx].sum()
        sim_avg_rois[i] = roi_pcts[idx].mean()
        sim_pos_rates[i] = (net_profits[idx] > 0).mean()

    def pct_summary(arr: np.ndarray) -> dict:
        return {
            "mean": float(arr.mean()),
            "median": float(np.percentile(arr, 50)),
            "std": float(arr.std()),
            "p5": float(np.percentile(arr, 5)),
            "p25": float(np.percentile(arr, 25)),
            "p75": float(np.percentile(arr, 75)),
            "p95": float(np.percentile(arr, 95)),
            "min": float(arr.min()),
            "max": float(arr.max()),
        }

    return {
        "mode": "walk_forward",
        "config": {
            "n_windows": n_windows,
            "sims": sims,
            "seed": seed,
        },
        "actual": {
            "total_net_profit": float(net_profits.sum()),
            "avg_roi_pct": float(roi_pcts.mean()),
            "positive_window_rate": float((net_profits > 0).mean()),
        },
        "bootstrap_total_profit": pct_summary(sim_total_profits),
        "bootstrap_avg_roi_pct": pct_summary(sim_avg_rois),
        "bootstrap_positive_rate": pct_summary(sim_pos_rates),
        "loss_probability": float((sim_total_profits < 0).mean()),
        "caveats": [
            "Bootstrap 重抽 21 個 window 估計「再跑一次同樣 6 個月」的不確定性。",
            "假設未來市場 regime 與這 21 個 window 的混合分布相似 — 黑天鵝不在此分布內。",
            "21 個樣本對長尾估計能力有限; p5/p95 是粗估,不是嚴格 95% CI。",
        ],
    }


# ── Pretty-print ─────────────────────────────────────────────────────


def print_trade_report(r: dict) -> None:
    print(f"\n{'='*62}")
    print(f"  Trade-Level Monte Carlo")
    print(f"{'='*62}")
    if "error" in r:
        print(f"  ❌ {r['error']}")
        return
    base = r["baseline"]
    mc = r["monte_carlo"]
    cap = r["config"]["capital"]
    fe = mc["final_equity"]
    dd = mc["max_drawdown_pct"]
    print(f"  基準回測: {base['num_trades']} trades, 實際總獲利 {base['actual_total_profit']:+.2f}")
    print(f"  起始資本: {cap}")
    print(f"  MC final_equity (USD):")
    print(f"    p5    = {fe['p5']:.2f}    p25 = {fe['p25']:.2f}")
    print(f"    median= {fe['median']:.2f}    mean = {fe['mean']:.2f}")
    print(f"    p75   = {fe['p75']:.2f}    p95 = {fe['p95']:.2f}")
    print(f"  MC max_drawdown_pct:")
    print(f"    median = {dd['median']*100:.2f}%   p5(最壞5%) = {dd['p5']*100:.2f}%   worst = {dd['worst']*100:.2f}%")
    print(f"  獲利機率 (final > 起始): {mc['profit_probability']*100:.1f}%")
    print(f"  腰斬機率 (final < 50%): {mc['ruin_probability']*100:.1f}%")


def print_wf_report(r: dict) -> None:
    print(f"\n{'='*62}")
    print(f"  Walk-Forward Window-Level Bootstrap")
    print(f"{'='*62}")
    if "error" in r:
        print(f"  ❌ {r['error']}")
        return
    a = r["actual"]
    tp = r["bootstrap_total_profit"]
    pr = r["bootstrap_positive_rate"]
    print(f"  實際 21 windows: total_net_profit={a['total_net_profit']:+.2f}, avg_roi={a['avg_roi_pct']:.2f}%, positive_rate={a['positive_window_rate']*100:.1f}%")
    print(f"  Bootstrap total_profit (6-month total):")
    print(f"    p5={tp['p5']:+.2f}    median={tp['median']:+.2f}    p95={tp['p95']:+.2f}")
    print(f"  Bootstrap positive_window_rate:")
    print(f"    p5={pr['p5']*100:.1f}%   median={pr['median']*100:.1f}%   p95={pr['p95']*100:.1f}%")
    print(f"  虧損機率 (6-month total < 0): {r['loss_probability']*100:.2f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description="Monte Carlo / bootstrap robustness for Jackbot_V1")
    parser.add_argument("--mode", choices=["trade", "walk_forward", "both"], default="trade")
    parser.add_argument("--sims", type=int, default=10000, help="Number of MC simulations (default: 10000)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=180, help="Backtest history window for trade mode (default: 180)")
    parser.add_argument("--capital", type=float, default=150.0)
    parser.add_argument("--maker-fee", type=float, default=0.0)
    parser.add_argument("--taker-fee", type=float, default=0.0004)
    args = parser.parse_args()

    out_dir = ROOT / "data"
    out_dir.mkdir(exist_ok=True)
    combined: dict = {}

    if args.mode in ("trade", "both"):
        r = run_trade_mc(
            days=args.days,
            capital=args.capital,
            sims=args.sims,
            seed=args.seed,
            maker_fee=args.maker_fee,
            taker_fee=args.taker_fee,
        )
        print_trade_report(r)
        with open(out_dir / "monte_carlo_trade.json", "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2, ensure_ascii=False)
        print(f"  💾 已儲存至 data/monte_carlo_trade.json")
        combined["trade"] = r

    if args.mode in ("walk_forward", "both"):
        r = run_walk_forward_bootstrap(sims=args.sims, seed=args.seed)
        print_wf_report(r)
        with open(out_dir / "monte_carlo_walk_forward.json", "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2, ensure_ascii=False)
        print(f"  💾 已儲存至 data/monte_carlo_walk_forward.json")
        combined["walk_forward"] = r

    if args.mode == "both":
        with open(out_dir / "monte_carlo_combined.json", "w", encoding="utf-8") as f:
            json.dump(combined, f, indent=2, ensure_ascii=False)
        print(f"\n  💾 整合報告已儲存至 data/monte_carlo_combined.json")


if __name__ == "__main__":
    main()
