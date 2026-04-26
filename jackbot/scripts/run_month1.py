import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
import logging

import structlog

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from jackbot.strategy.day_trader import DayTraderConfig
from scripts.backtest import BacktestEngine, download_klines

def run():
    # Setup structlog to write to a file
    log_file = open("data/month1_trades.log", "w", encoding="utf-8")
    
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
        ],
        logger_factory=structlog.PrintLoggerFactory(file=log_file),
    )

    end_dt = datetime.now(UTC) - timedelta(days=150)
    start_dt = datetime.now(UTC) - timedelta(days=180)
    
    start_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)
    
    symbols = ["BTCUSDT", "ETHUSDT"]
    klines_all = {}
    for sym in symbols:
        klines_all[sym] = download_klines(sym, "5m", start_ts, end_ts)

    config = DayTraderConfig(
        symbols=symbols,
        timeframe="5m",
        total_capital_usd=150.0,
        per_symbol_alloc_pct=50.0,
        compound_pct=50.0,
        default_grid_count=6,
        max_leverage=20,
        min_leverage=5,
        daily_profit_target_usd=10.0,
        daily_loss_limit_usd=15.0,
        grid_stop_loss_pct=3.0,
        conservative_size_factor=0.25,
        conservative_grid_spacing_mult=2.0,
        conservative_leverage=3,
        max_concurrent_grids=2,
        max_daily_resets=10,
        hourly_review_interval_bars=12,
        warmup_bars=50,
    )

    engine = BacktestEngine(config, commission_rate=0.0)
    engine.run(klines_all)
    log_file.close()
    
    print("Log saved to data/month1_trades.log")

if __name__ == "__main__":
    run()
