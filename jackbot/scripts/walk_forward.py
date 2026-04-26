"""Walk-Forward Validation for Jackbot_V1

Downloads 6 months of BTCUSDC/ETHUSDC 5m klines (cached to data/historical/).
Rolls a 30-day training / 7-day OOS test window across the full period.
Each train window runs a grid search; best params are applied to the OOS week.

Usage:
  python scripts/walk_forward.py
  python scripts/walk_forward.py --months 6   # default
  python scripts/walk_forward.py --months 12
"""

from __future__ import annotations

import itertools
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import structlog
structlog.configure(
    processors=[],
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
    logger_factory=structlog.PrintLoggerFactory(file=open("NUL", "w")),
)

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from jackbot.strategy.day_trader import DayTraderConfig
from scripts.backtest import BacktestEngine, download_klines

HISTORICAL_DIR = ROOT / "data" / "historical"
SYMBOLS = ["BTCUSDC", "ETHUSDC"]
TRAIN_DAYS = 30
TEST_DAYS = 7

GRID_COUNTS = [10, 15, 20]
STOP_LOSSES = [2.0, 3.0, 4.0]


def _load_or_download(symbol: str, start_ts: int, end_ts: int, months: int) -> list[dict]:
    cache = HISTORICAL_DIR / f"{symbol.lower()}_5m_{months}mo.json"
    if cache.exists():
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
        filtered = [k for k in data if start_ts <= k["timestamp"] <= end_ts]
        if len(filtered) > 100:
            return filtered

    print(f"   📥 {symbol}...", end=" ", flush=True)
    klines = download_klines(symbol, "5m", start_ts, end_ts)
    print(f"{len(klines):,} 根")
    HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(klines, f)
    return klines


def _make_config(grid_count: int, stop_loss: float) -> DayTraderConfig:
    return DayTraderConfig(
        symbols=SYMBOLS,
        timeframe="5m",
        total_capital_usd=150.0,
        per_symbol_alloc_pct=50.0,
        default_grid_count=grid_count,
        grid_stop_loss_pct=stop_loss,
        max_leverage=10,
        min_leverage=5,
        daily_profit_target_usd=10.0,
        daily_loss_limit_pct=10.0,
        conservative_size_factor=0.25,
        conservative_grid_spacing_mult=2.0,
        conservative_leverage=3,
        max_concurrent_grids=2,
        max_daily_resets=10,
        hourly_review_interval_bars=12,
        warmup_bars=50,
    )


def _slice(all_klines: dict, start_ts: int, end_ts: int) -> dict:
    return {
        sym: [k for k in klines if start_ts <= k["timestamp"] < end_ts]
        for sym, klines in all_klines.items()
    }


def _run(klines_by_symbol: dict, grid_count: int, stop_loss: float,
         maker_fee: float, taker_fee: float) -> dict:
    config = _make_config(grid_count, stop_loss)
    engine = BacktestEngine(config, maker_rate=maker_fee, taker_rate=taker_fee)
    return engine.run(klines_by_symbol)


def _best_on_train(train_klines: dict, maker_fee: float, taker_fee: float) -> tuple[int, float]:
    best_profit = -9999.0
    best_gc, best_sl = GRID_COUNTS[0], STOP_LOSSES[0]
    for gc, sl in itertools.product(GRID_COUNTS, STOP_LOSSES):
        res = _run(train_klines, gc, sl, maker_fee, taker_fee)
        net = res["pnl"]["net_profit"]
        if net > best_profit:
            best_profit = net
            best_gc, best_sl = gc, sl
    return best_gc, best_sl


def main(months: int = 6) -> None:
    cfg_path = ROOT / "config" / "settings.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    maker_fee = settings["fees"]["maker"]
    taker_fee = settings["fees"]["taker"]

    end_dt = datetime.now(UTC)
    start_dt = end_dt - timedelta(days=months * 30)
    start_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)

    print(f"\n{'='*62}")
    print(f"  Walk-Forward 驗證 — Jackbot_V1")
    print(f"  期間: {start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')} ({months} 個月)")
    print(f"  訓練: {TRAIN_DAYS}天  |  測試: {TEST_DAYS}天  |  步進: {TEST_DAYS}天")
    print(f"  費率: Maker {maker_fee*100:.4f}% / Taker {taker_fee*100:.4f}%")
    print(f"{'='*62}\n")

    print("📥 載入歷史資料...")
    all_klines: dict[str, list[dict]] = {}
    for sym in SYMBOLS:
        all_klines[sym] = _load_or_download(sym, start_ts, end_ts, months)
    total = sum(len(v) for v in all_klines.values())
    print(f"   總計 {total:,} 根\n")

    # Build rolling windows
    windows: list[tuple[datetime, datetime, datetime, datetime]] = []
    cursor = start_dt
    while True:
        train_start = cursor
        train_end = cursor + timedelta(days=TRAIN_DAYS)
        test_start = train_end
        test_end = test_start + timedelta(days=TEST_DAYS)
        if test_end > end_dt:
            break
        windows.append((train_start, train_end, test_start, test_end))
        cursor += timedelta(days=TEST_DAYS)

    n = len(windows)
    print(f"🔄 共 {n} 個滾動窗口 ({len(GRID_COUNTS)*len(STOP_LOSSES)} 組參數每次)\n")
    print(f"  {'窗口':>3}  {'測試期':>13}  {'最佳參數':>12}  {'OOS ROI':>8}  {'PF':>6}  {'最大DD':>7}  {'淨利':>9}  {'判定'}")
    print(f"  {'-'*3}  {'-'*13}  {'-'*12}  {'-'*8}  {'-'*6}  {'-'*7}  {'-'*9}  {'-'*4}")

    oos_results: list[dict] = []

    for i, (train_start, train_end, test_start, test_end) in enumerate(windows, 1):
        ts = lambda dt: int(dt.timestamp() * 1000)

        train_klines = _slice(all_klines, ts(train_start), ts(train_end))
        test_klines = _slice(all_klines, ts(test_start), ts(test_end))

        if not any(len(v) > 50 for v in test_klines.values()):
            continue

        best_gc, best_sl = _best_on_train(train_klines, maker_fee, taker_fee)
        res = _run(test_klines, best_gc, best_sl, maker_fee, taker_fee)

        roi = res["pnl"]["roi_pct"]
        net = res["pnl"]["net_profit"]
        max_dd = res["risk"]["max_drawdown"]
        max_dd_pct = max_dd / 150.0 * 100
        pf = res["trades"]["profit_factor"]
        pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
        ok = "✅" if roi > 0 else "❌"
        params_str = f"G{best_gc}/SL{best_sl:.0f}%"

        print(
            f"  [{i:2d}]  "
            f"{test_start.strftime('%m-%d')}→{test_end.strftime('%m-%d')}  "
            f"{params_str:>12}  "
            f"{roi:>+7.2f}%  "
            f"{pf_str:>6}  "
            f"{max_dd_pct:>6.1f}%  "
            f"${net:>+8.4f}  "
            f"{ok}"
        )

        oos_results.append({
            "window": i,
            "test_start": test_start.strftime("%Y-%m-%d"),
            "test_end": test_end.strftime("%Y-%m-%d"),
            "best_gc": best_gc,
            "best_sl": best_sl,
            "roi_pct": roi,
            "net_profit": net,
            "max_dd_pct": round(max_dd_pct, 2),
            "profit_factor": pf if pf != float("inf") else 9999,
        })

    # Summary
    positive = [r for r in oos_results if r["roi_pct"] > 0]
    avg_roi = sum(r["roi_pct"] for r in oos_results) / len(oos_results) if oos_results else 0
    total_net = sum(r["net_profit"] for r in oos_results)
    max_single_dd = max((r["max_dd_pct"] for r in oos_results), default=0)
    avg_pf = sum(min(r["profit_factor"], 9999) for r in oos_results) / len(oos_results) if oos_results else 0

    print(f"\n{'='*62}")
    print(f"  📊 Walk-Forward 摘要")
    print(f"  總窗口數:        {len(oos_results)}")
    print(f"  正報酬窗口:      {len(positive)} / {len(oos_results)}  ({len(positive)/len(oos_results)*100:.0f}%)")
    print(f"  平均 OOS ROI:    {avg_roi:+.2f}%")
    print(f"  累計淨利:        ${total_net:+.4f}")
    print(f"  平均 PF:         {avg_pf:.2f}")
    print(f"  單窗口最大 DD:   {max_single_dd:.1f}%")

    criteria_roi = len(positive) >= max(4, round(len(oos_results) * 0.6))
    criteria_dd = max_single_dd < 15.0

    print(f"\n  📋 驗收標準")
    print(f"  正報酬窗口 ≥ 60%: {'✅ 通過' if criteria_roi else '❌ 未通過'} ({len(positive)}/{len(oos_results)})")
    print(f"  最大單窗口 DD < 15%: {'✅ 通過' if criteria_dd else '❌ 未通過'} ({max_single_dd:.1f}%)")
    overall = criteria_roi and criteria_dd
    print(f"\n  {'✅ Phase A2 通過 — 策略有 edge' if overall else '❌ Phase A2 未通過 — 需要 Phase B 方向調整'}")
    print(f"{'='*62}\n")

    out = ROOT / "data" / "walk_forward_results.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "config": {"months": months, "train_days": TRAIN_DAYS, "test_days": TEST_DAYS},
            "summary": {
                "total_windows": len(oos_results),
                "positive_windows": len(positive),
                "avg_roi_pct": round(avg_roi, 2),
                "total_net_profit": round(total_net, 4),
                "max_single_dd_pct": round(max_single_dd, 2),
                "avg_profit_factor": round(avg_pf, 2),
                "criteria_roi_pass": criteria_roi,
                "criteria_dd_pass": criteria_dd,
                "overall_pass": overall,
            },
            "windows": oos_results,
        }, f, indent=2, ensure_ascii=False)
    print(f"  💾 結果已儲存至 data/walk_forward_results.json")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=6)
    args = parser.parse_args()
    main(args.months)
