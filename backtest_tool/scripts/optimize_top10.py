#!/usr/bin/env python3
"""優化回測獲利對策 10 次並作比對。

對 TrendDonchian 與 MeanReversionBB 策略各執行參數掃描，
取出前 10 組最優參數，並輸出詳細比對表格。

Usage:
    python -m backtest_tool.scripts.optimize_top10
    python -m backtest_tool.scripts.optimize_top10 --strategy trend_donchian
    python -m backtest_tool.scripts.optimize_top10 --symbol BTCUSDT --start 2024-01-01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest_tool.data_manager import DataStore
from backtest_tool.engine import BacktestRunner, ParamScanner
from backtest_tool.strategies import STRATEGY_MAP

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"

# ──────────────────────────────────────────────────────────────────────────────
# 參數掃描空間（精簡版，確保在合理時間內跑完）
# ──────────────────────────────────────────────────────────────────────────────
PARAM_SPACES: dict[str, dict] = {
    "trend_donchian": {
        "entry_period": [10, 15, 20, 25, 30],
        "exit_period": [5, 10, 15],
        "adx_threshold": [20, 25, 30],
        "atr_stop_mult": [1.5, 2.0, 2.5],
        "leverage": [1, 2, 3],
    },
    "mean_reversion_bb": {
        "bb_period": [15, 20, 25],
        "bb_std": [1.5, 2.0, 2.5],
        "rsi_oversold": [25, 30, 35],
        "rsi_overbought": [65, 70, 75],
        "leverage": [1, 2],
    },
    "grid_futures": {
        "grid_count": [10, 15, 20, 30],
        "ema_period": [100, 150, 200],
        "leverage": [1, 2, 3],
    },
}

STRATEGY_TIMEFRAMES: dict[str, str] = {
    "trend_donchian": "4h",
    "mean_reversion_bb": "1h",
    "grid_futures": "4h",
}

TOP_N = 10
INITIAL_CAPITAL = 10_000.0


# ──────────────────────────────────────────────────────────────────────────────
# 合成資料（若無法從 Binance 下載時使用）
# ──────────────────────────────────────────────────────────────────────────────

def _generate_synthetic_btc(
    timeframe: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    """產生模擬 BTC 走勢的合成 OHLCV 資料。

    使用幾何布朗運動（GBM）加入趨勢、均值回歸噪音，
    讓資料更接近真實市場行為。
    """
    freq_map = {
        "1h": "1H", "4h": "4H", "8h": "8H", "1d": "1D",
        "1m": "1min", "5m": "5min", "15m": "15min",
    }
    freq = freq_map.get(timeframe, "4H")
    index = pd.date_range(start=start, end=end, freq=freq)
    n = len(index)

    rng = np.random.default_rng(42)

    # 參數
    s0 = 45_000.0
    annual_drift = 0.40        # 年化漲幅 40%
    annual_vol = 0.65          # 年化波動率 65%
    bars_per_year = {"1H": 8760, "4H": 2190, "8H": 1095, "1D": 365}.get(freq, 2190)
    dt = 1.0 / bars_per_year

    mu = annual_drift * dt
    sigma = annual_vol * np.sqrt(dt)

    # GBM 路徑
    returns = rng.normal(mu - 0.5 * sigma**2, sigma, n)
    log_prices = np.cumsum(returns)
    closes = s0 * np.exp(log_prices)

    # 市場週期（bull/bear 切換）
    cycle = 0.15 * np.sin(2 * np.pi * np.arange(n) / (bars_per_year * 1.5))
    closes = closes * (1 + cycle)

    # 建立 OHLCV
    bar_vol = annual_vol / np.sqrt(bars_per_year)
    highs = closes * (1 + rng.uniform(0, bar_vol * 1.5, n))
    lows = closes * (1 - rng.uniform(0, bar_vol * 1.5, n))
    opens = np.roll(closes, 1)
    opens[0] = s0

    volumes = rng.lognormal(mean=13, sigma=0.8, size=n)

    df = pd.DataFrame(
        {
            "timestamp": (index.astype(np.int64) // 10**6).astype(int),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "quote_volume": volumes * closes,
            "trade_count": (volumes / 0.01).astype(int),
            "taker_buy_volume": volumes * 0.52,
            "taker_buy_quote_volume": volumes * closes * 0.52,
        },
        index=index,
    )
    df.index.name = "datetime"
    return df


def _load_or_generate(
    store: DataStore,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    """從 DataStore 讀取資料，若無資料則嘗試從 Binance 下載，最後用合成資料兜底。"""
    df = store.load_klines(symbol, timeframe, start=start, end=end)
    if not df.empty and len(df) > 100:
        print(f"   ✅ 讀取本地資料：{len(df)} 筆")
        return df

    print(f"   ⚠️  無本地資料，嘗試從 Binance 下載 {symbol} {timeframe}...")
    try:
        from backtest_tool.data_manager import DataImporter
        importer = DataImporter(store)
        summary = importer.download_from_binance(
            symbols=[symbol],
            timeframes=[timeframe],
            start=start,
            end=end,
        )
        df = store.load_klines(symbol, timeframe, start=start, end=end)
        if not df.empty and len(df) > 100:
            print(f"   ✅ 下載完成：{len(df)} 筆")
            return df
    except Exception as e:
        print(f"   ⚠️  Binance 下載失敗（{e}），改用合成資料")

    print(f"   🔧 使用合成 {symbol} 資料（{start} → {end}）...")
    df = _generate_synthetic_btc(timeframe, start, end)
    print(f"   ✅ 合成資料：{len(df)} 筆")
    return df


# ──────────────────────────────────────────────────────────────────────────────
# 比對表格輸出
# ──────────────────────────────────────────────────────────────────────────────

DISPLAY_METRICS = [
    ("sharpe_ratio",     "Sharpe",     "{:>7.3f}"),
    ("total_return",     "總報酬%",    "{:>+8.2f}"),
    ("annual_return",    "年化%",      "{:>+8.2f}"),
    ("max_drawdown",     "最大回撤%",  "{:>8.2f}"),
    ("calmar_ratio",     "Calmar",     "{:>7.3f}"),
    ("win_rate",         "勝率%",      "{:>7.2f}"),
    ("profit_factor",    "盈虧比",     "{:>7.3f}"),
    ("total_trades",     "交易次",     "{:>6.0f}"),
    ("sortino_ratio",    "Sortino",    "{:>7.3f}"),
]

PARAM_COLS = {
    "trend_donchian": ["entry_period", "exit_period", "adx_threshold", "atr_stop_mult", "leverage"],
    "mean_reversion_bb": ["bb_period", "bb_std", "rsi_oversold", "rsi_overbought", "leverage"],
    "grid_futures": ["grid_count", "ema_period", "leverage"],
}


def _print_comparison(strategy_name: str, df: pd.DataFrame, sort_by: str) -> None:
    """印出前 10 名策略參數比對表。"""
    sep = "=" * 100
    print(f"\n{sep}")
    print(f"  策略：{strategy_name}  │  最佳化目標：{sort_by}  │  共 {len(df)} 組")
    print(sep)

    param_cols = PARAM_COLS.get(strategy_name, [])

    # 表頭
    header_parts = [f"{'排名':>4}"]
    for pc in param_cols:
        header_parts.append(f"{pc:>14}")
    for _, col_label, _ in DISPLAY_METRICS:
        header_parts.append(f"{col_label:>10}")
    print("  " + "  ".join(header_parts))
    print("  " + "-" * 96)

    for rank, row in df.iterrows():
        parts = [f"{rank:>4}"]
        for pc in param_cols:
            val = row.get(pc, "")
            parts.append(f"{str(val):>14}")
        for col_key, _, fmt in DISPLAY_METRICS:
            val = row.get(col_key, 0)
            try:
                parts.append(fmt.format(val).rjust(10))
            except Exception:
                parts.append(f"{'N/A':>10}")
        print("  " + "  ".join(parts))

    print()
    # 最優組別摘要
    best = df.iloc[0]
    print(f"  🏆 第 1 名參數：{dict((p, best.get(p)) for p in param_cols)}")
    print(f"     Sharpe={best.get('sharpe_ratio', 0):.4f} "
          f"│ 總報酬={best.get('total_return', 0):+.2f}% "
          f"│ 年化={best.get('annual_return', 0):+.2f}% "
          f"│ 最大回撤={best.get('max_drawdown', 0):.2f}% "
          f"│ Calmar={best.get('calmar_ratio', 0):.4f}")
    print(sep)


def _print_cross_strategy_summary(all_results: dict[str, pd.DataFrame], sort_by: str) -> None:
    """跨策略最優組別總覽。"""
    sep = "=" * 100
    print(f"\n{sep}")
    print(f"  跨策略最優參數比對（目標：{sort_by}，各取第 1 名）")
    print(sep)

    rows = []
    for sname, df in all_results.items():
        if df.empty:
            continue
        best = df.iloc[0].copy()
        best["strategy"] = sname
        rows.append(best)

    if not rows:
        print("  （無結果）")
        return

    summary = pd.DataFrame(rows).set_index("strategy")

    cols = [
        ("sharpe_ratio",   "Sharpe",    "{:>7.3f}"),
        ("total_return",   "總報酬%",   "{:>+8.2f}"),
        ("annual_return",  "年化%",     "{:>+8.2f}"),
        ("max_drawdown",   "最大回撤%", "{:>8.2f}"),
        ("calmar_ratio",   "Calmar",    "{:>7.3f}"),
        ("win_rate",       "勝率%",     "{:>7.2f}"),
        ("profit_factor",  "盈虧比",    "{:>7.3f}"),
        ("total_trades",   "交易次",    "{:>6.0f}"),
    ]

    header = f"  {'策略':^24}"
    for _, label, _ in cols:
        header += f"  {label:>10}"
    print(header)
    print("  " + "-" * 96)

    for strat, row in summary.iterrows():
        line = f"  {strat:<24}"
        for col_key, _, fmt in cols:
            val = row.get(col_key, 0)
            try:
                line += f"  {fmt.format(val).rjust(10)}"
            except Exception:
                line += f"  {'N/A':>10}"
        print(line)

    print(sep)


# ──────────────────────────────────────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="優化回測獲利對策 10 次並比對")
    parser.add_argument("--strategy", type=str, default=None,
                        choices=list(PARAM_SPACES.keys()),
                        help="指定單一策略（預設：全部）")
    parser.add_argument("--symbol", type=str, default="BTCUSDT")
    parser.add_argument("--start", type=str, default="2024-01-01")
    parser.add_argument("--end", type=str, default="2026-04-17")
    parser.add_argument("--capital", type=float, default=INITIAL_CAPITAL)
    parser.add_argument("--target", type=str, default="sharpe_ratio",
                        help="最佳化目標指標")
    parser.add_argument("--top", type=int, default=TOP_N,
                        help="顯示前 N 名（預設 10）")
    parser.add_argument("--save-csv", type=str, default=None,
                        help="儲存結果為 CSV 檔案路徑")
    args = parser.parse_args()

    strategies_to_run = [args.strategy] if args.strategy else list(PARAM_SPACES.keys())

    print("=" * 100)
    print(f"  回測策略優化比對")
    print(f"  標的：{args.symbol}  │  期間：{args.start} → {args.end}")
    print(f"  初始資金：${args.capital:,.0f}  │  目標：{args.target}  │  各取前 {args.top} 名")
    print("=" * 100)

    store = DataStore()
    runner = BacktestRunner(config_path=str(CONFIG_PATH))
    runner.initial_capital = args.capital
    scanner = ParamScanner(runner)

    all_top10: dict[str, pd.DataFrame] = {}
    all_csv_parts: list[pd.DataFrame] = []

    for strat_name in strategies_to_run:
        tf = STRATEGY_TIMEFRAMES.get(strat_name, "4h")
        param_space = PARAM_SPACES[strat_name]
        strategy_cls = STRATEGY_MAP[strat_name]

        # 計算組合數
        import itertools
        n_combos = len(list(itertools.product(*param_space.values())))
        print(f"\n{'─' * 100}")
        print(f"  [{strat_name}]  時間框架：{tf}  │  參數組合：{n_combos} 組")
        print(f"{'─' * 100}")

        # 讀取/下載/合成資料
        print(f"\n  📂 載入資料...")
        df = _load_or_generate(store, args.symbol, tf, args.start, args.end)

        if df.empty or len(df) < 50:
            print(f"  ❌ 資料不足，跳過 {strat_name}")
            continue

        # 參數掃描
        print(f"\n  🔍 掃描 {n_combos} 組參數（目標：{args.target}，取前 {args.top} 名）...")
        results_df = scanner.scan(
            strategy_class=strategy_cls,
            param_space=param_space,
            ohlcv=df,
            sort_by=args.target,
            top_n=args.top,
        )

        if results_df.empty:
            print(f"  ⚠️  無有效結果")
            continue

        all_top10[strat_name] = results_df

        # 印出比對表
        _print_comparison(strat_name, results_df, args.target)

        # 收集 CSV 資料
        csv_df = results_df.copy()
        csv_df.insert(0, "strategy", strat_name)
        csv_df.insert(1, "symbol", args.symbol)
        csv_df.insert(2, "timeframe", tf)
        all_csv_parts.append(csv_df)

    # 跨策略總覽
    if len(all_top10) > 1:
        _print_cross_strategy_summary(all_top10, args.target)

    # 儲存 CSV
    if args.save_csv and all_csv_parts:
        combined = pd.concat(all_csv_parts, ignore_index=True)
        combined.to_csv(args.save_csv, index=False, encoding="utf-8-sig")
        print(f"\n  💾 結果已儲存至：{args.save_csv}")

    print("\n  ✅ 優化完成！\n")


if __name__ == "__main__":
    main()
