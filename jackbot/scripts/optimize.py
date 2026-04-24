"""Jackbot_V1 Parameter Optimization

1. Downloads 60 days of historical data.
2. Splits into Month 1 (In-Sample / Training) and Month 2 (Out-of-Sample / Testing).
3. Runs grid search on Month 1 to find the best parameters.
4. Applies the best parameters to Month 2.
"""

import itertools
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Fix Windows console encoding for Chinese + emoji
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Suppress structlog output for optimization runs
import structlog
structlog.configure(
    processors=[],
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
    logger_factory=structlog.PrintLoggerFactory(file=open("NUL", "w")),
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from jackbot.strategy.day_trader import DayTraderConfig
from scripts.backtest import BacktestEngine, download_klines


def split_data(klines_by_symbol: dict[str, list[dict]], split_ts: int):
    """Splits klines into two sets based on timestamp."""
    m1 = {}
    m2 = {}
    for sym, klines in klines_by_symbol.items():
        m1[sym] = [k for k in klines if k["timestamp"] < split_ts]
        m2[sym] = [k for k in klines if k["timestamp"] >= split_ts]
    return m1, m2


def run_optimization():
    # Parameters to test (Zero-fee allows very dense grids)
    grid_counts = [10, 15, 20, 30]
    stop_losses = [2.0, 3.0, 4.0]

    end_dt = datetime.now(UTC)
    mid_dt = end_dt - timedelta(days=30)
    start_dt = end_dt - timedelta(days=60)

    start_ts = int(start_dt.timestamp() * 1000)
    mid_ts = int(mid_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)

    symbols = ["BTCUSDC", "ETHUSDC"]

    print(f"\n🔄 下載 60 天歷史 K 線 (2 個月)...")
    klines_all = {}
    for symbol in symbols:
        print(f"   📥 {symbol}...", end=" ", flush=True)
        klines = download_klines(symbol, "5m", start_ts, end_ts)
        klines_all[symbol] = klines
        print(f"{len(klines):,} 根")

    m1_data, m2_data = split_data(klines_all, mid_ts)
    
    print(f"\n✅ 數據分割完成:")
    print(f"   [月 1 - 最佳化] {start_dt.strftime('%Y-%m-%d')} → {mid_dt.strftime('%Y-%m-%d')}")
    print(f"   [月 2 - 驗證期] {mid_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')}")

    print("\n🔍 開始 Month 1 參數最佳化 (Grid Search)...")
    best_net_profit = -9999.0
    best_params = None
    best_results = None

    combinations = list(itertools.product(grid_counts, stop_losses))
    
    for i, (gc, sl) in enumerate(combinations, 1):
        print(f"   [{i}/{len(combinations)}] 測試 GridCount={gc:2}, StopLoss={sl:.1f}%...", end=" ")
        
        config = DayTraderConfig(
            symbols=symbols,
            timeframe="5m",
            total_capital_usd=150.0,
            per_symbol_alloc_pct=50.0,
            default_grid_count=gc,
            grid_stop_loss_pct=sl,
            max_leverage=20,
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
        
        engine = BacktestEngine(config, commission_rate=0.0)
        res = engine.run(m1_data)
        net_profit = res["pnl"]["net_profit"]
        
        print(f"淨利: ${net_profit:>7.4f}")
        
        if net_profit > best_net_profit:
            best_net_profit = net_profit
            best_params = {"grid_count": gc, "stop_loss": sl}
            best_results = res

    print("\n🏆 Month 1 最佳參數:")
    print(f"   網格數 (Grid Count): {best_params['grid_count']}")
    print(f"   止損點 (Stop Loss):  {best_params['stop_loss']}%")
    print(f"   >> Month 1 淨利潤:  ${best_net_profit:.4f} (ROI: {best_results['pnl']['roi_pct']}%)")

    print("\n🚀 使用最佳參數執行 Month 2 驗證...")
    config_m2 = DayTraderConfig(
        symbols=symbols,
        timeframe="5m",
        total_capital_usd=150.0,
        per_symbol_alloc_pct=50.0,
        default_grid_count=best_params["grid_count"],
        grid_stop_loss_pct=best_params["stop_loss"],
        max_leverage=20,
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
    engine_m2 = BacktestEngine(config_m2, commission_rate=0.0)
    m2_results = engine_m2.run(m2_data)

    m2_net_profit = m2_results["pnl"]["net_profit"]
    m2_roi = m2_results["pnl"]["roi_pct"]

    print(f"   >> Month 2 淨利潤:  ${m2_net_profit:.4f} (ROI: {m2_roi}%)")

    # Combine report
    final_report = {
        "best_parameters": best_params,
        "month_1_training": best_results,
        "month_2_testing": m2_results
    }

    with open("data/optimization_report.json", "w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2, ensure_ascii=False)
        
    print(f"\n📊 完整報表已儲存至 data/optimization_report.json")

if __name__ == "__main__":
    run_optimization()
