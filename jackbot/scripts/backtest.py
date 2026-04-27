"""Jackbot_V1 Backtester — Simulates grid trading on historical data.

Downloads 5m klines from Binance and replays them through the DayTrader,
simulating limit-order fills when price crosses grid levels.

Usage:
  python scripts/backtest.py                           # Last 30 days
  python scripts/backtest.py --days 60                 # Last 60 days
  python scripts/backtest.py --start 2026-03-24        # Specific start date
  python scripts/backtest.py --capital 200             # Override capital
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from bisect import bisect_left
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Fix Windows console encoding for Chinese + emoji
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import httpx
import structlog

from jackbot.backtest_metrics import compute_calmar, compute_score, compute_sharpe
from jackbot.core.clock import Clock
from jackbot.core.constants import GridDirection, GridLevelState, TradingMode
from jackbot.core.event_bus import EventBus
from jackbot.core.events import FillEvent, MarketEvent
from jackbot.strategy.day_trader import DayTrader, DayTraderConfig
from jackbot.strategy.grid_engine import GridInstance

logger = structlog.get_logger("backtest")


# ── Backtest clock (follows bar time, not real time) ──────────────────

class BacktestClock(Clock):
    """Clock that returns the timestamp of the current bar being processed."""

    def __init__(self) -> None:
        self._now = datetime.now(UTC)

    def set_time(self, dt: datetime) -> None:
        self._now = dt

    def now(self) -> datetime:
        return self._now

# ── Binance public kline downloader ──────────────────────────────────


def download_klines(
    symbol: str,
    interval: str = "5m",
    start_ts: int = 0,
    end_ts: int = 0,
    limit: int = 1500,
) -> list[dict]:
    """Download klines from Binance public API (no auth needed)."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    all_klines: list[dict] = []
    current_start = start_ts

    with httpx.Client(timeout=30.0) as client:
        while True:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": current_start,
                "limit": limit,
            }
            if end_ts > 0:
                params["endTime"] = end_ts

            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            if not data:
                break

            for row in data:
                all_klines.append({
                    "timestamp": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                })

            # Move to next batch
            last_ts = int(data[-1][0])
            if last_ts >= end_ts and end_ts > 0:
                break
            current_start = last_ts + 1

            if len(data) < limit:
                break

    return all_klines


# ── Fill simulator ────────────────────────────────────────────────────


class FillSimulator:
    """Simulates limit order fills based on bar high/low.

    Grid limit orders are treated as maker fills (maker rate may be negative
    to model rebates). SL/breakout/review market closes are taker fills
    (taker_rate + ATR slippage).

    Partial fills:
      * legacy mode: ratio = random.uniform(0.5, 1.0) per level (back-compat)
      * volume-aware mode (default): ratio = clamp(bar.volume / per_level_qty
        / volume_norm, 0.1, 1.0). Models thin/heavy bars more honestly.

    Multi-level ties (multiple levels touched in same bar) are sorted by
    distance to close so the level closest to the bar's exit price wins fill
    priority — a coarse proxy for queue position.
    """

    _ATR_PERIOD = 14

    def __init__(
        self,
        maker_rate: float = 0.0,
        taker_rate: float = 0.0004,
        legacy_fill: bool = False,
        volume_norm: float = 50.0,
    ) -> None:
        self._fill_count = 0
        self._maker_rate = maker_rate
        self._taker_rate = taker_rate
        self._legacy_fill = legacy_fill
        self._volume_norm = volume_norm
        self._atr_buffers: dict[str, deque] = {}
        self._atr_values: dict[str, float] = {}

    def update_atr(self, bar: MarketEvent) -> None:
        """Update the rolling ATR for a symbol using this bar's true range."""
        sym = bar.symbol
        if sym not in self._atr_buffers:
            self._atr_buffers[sym] = deque(maxlen=self._ATR_PERIOD)
        self._atr_buffers[sym].append(bar.high - bar.low)
        if len(self._atr_buffers[sym]) == self._ATR_PERIOD:
            self._atr_values[sym] = sum(self._atr_buffers[sym]) / self._ATR_PERIOD

    def compute_taker_close(self, grid: GridInstance, bar: MarketEvent) -> float:
        """Return taker fee + slippage for SL market close.

        Uses actual open position reconstructed from level fill history:
        - buy_fill_price > 0, sell_fill_price == 0 → unmatched long (needs market sell)
        - sell_fill_price > 0, buy_fill_price == 0 → unmatched short (needs market buy)
        Breakout/review closes use limit orders (maker = 0%), so this is only for SL.
        """
        open_notional = sum(
            lvl.quantity * lvl.buy_fill_price
            for lvl in grid.levels
            if lvl.buy_fill_price > 0 and lvl.sell_fill_price == 0
        ) + sum(
            lvl.quantity * lvl.sell_fill_price
            for lvl in grid.levels
            if lvl.sell_fill_price > 0 and lvl.buy_fill_price == 0
        )
        if open_notional == 0:
            return 0.0
        commission = open_notional * self._taker_rate
        atr = self._atr_values.get(bar.symbol, 0.0)
        slippage = open_notional * (atr / bar.close) * 0.02 if bar.close > 0 else 0.0
        return commission + slippage

    def _fill_ratio(self, level_qty: float, bar_volume: float) -> float:
        if self._legacy_fill or level_qty <= 0:
            return random.uniform(0.5, 1.0)
        return min(1.0, max(0.1, bar_volume / level_qty / self._volume_norm))

    def check_fills(
        self,
        grids: list[GridInstance],
        bar: MarketEvent,
    ) -> list[FillEvent]:
        """Check all active grids for fills based on this bar's price range."""
        fills: list[FillEvent] = []

        # Collect candidate (grid, level, side) tuples for this bar, then sort
        # by distance to close so closer levels fill first when bar straddles
        # multiple. This is a coarse model of intra-bar order: the level at
        # the bar's high/low fills before levels nearer to close.
        candidates: list[tuple[GridInstance, "GridLevel", str, float]] = []
        for grid in grids:
            if grid.closed:
                continue
            for level in grid.levels:
                if not (bar.low <= level.price <= bar.high):
                    continue
                if level.state == GridLevelState.PENDING_BUY:
                    candidates.append((grid, level, "BUY", abs(level.price - bar.close)))
                elif level.state == GridLevelState.PENDING_SELL:
                    candidates.append((grid, level, "SELL", abs(level.price - bar.close)))

        # Sort by distance to close ascending — closest levels execute first.
        candidates.sort(key=lambda t: t[3])

        for grid, level, side, _dist in candidates:
            self._fill_count += 1
            qty = level.quantity * self._fill_ratio(level.quantity, bar.volume)
            fills.append(FillEvent(
                timestamp=bar.timestamp,
                symbol=bar.symbol,
                side=side,
                quantity=qty,
                price=level.price,
                commission=level.price * qty * self._maker_rate,
                order_id=f"sim_{self._fill_count}",
                grid_id=grid.grid_id,
                level_index=level.index,
            ))

        return fills

    def compute_funding(self, grid: GridInstance, current_price: float, rate: float) -> float:
        """Funding charge for a grid's net open position at this settlement.

        Long position pays funding when rate > 0; short position receives.
        Returns a signed dollar amount: positive = cost, negative = credit.
        """
        long_qty = sum(
            lvl.quantity for lvl in grid.levels
            if lvl.state == GridLevelState.FILLED_BUY
            and lvl.buy_fill_price > 0 and lvl.sell_fill_price == 0
        )
        short_qty = sum(
            lvl.quantity for lvl in grid.levels
            if lvl.state == GridLevelState.FILLED_SELL
            and lvl.sell_fill_price > 0 and lvl.buy_fill_price == 0
        )
        net_long_notional = (long_qty - short_qty) * current_price
        return net_long_notional * rate


# ── Funding rate fetch + cache ────────────────────────────────────────


_FUNDING_CACHE_DIR = ROOT / "data" / "funding"


def download_funding(symbol: str, start_ts: int, end_ts: int) -> list[tuple[int, float]]:
    """Download Binance USDT-M futures funding rates for [start_ts, end_ts].

    Cached per-symbol-and-window in data/funding/. Each entry is (ms, rate).
    Funding settles every 8h; ~3 entries per day per symbol.
    """
    _FUNDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _FUNDING_CACHE_DIR / f"{symbol}_{start_ts}_{end_ts}.json"
    if cache_path.exists():
        return [(int(ts), float(rate)) for ts, rate in json.loads(cache_path.read_text())]

    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    out: list[tuple[int, float]] = []
    cursor = start_ts
    with httpx.Client(timeout=30.0) as client:
        while True:
            resp = client.get(url, params={
                "symbol": symbol,
                "startTime": cursor,
                "endTime": end_ts,
                "limit": 1000,
            })
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            for row in data:
                out.append((int(row["fundingTime"]), float(row["fundingRate"])))
            last_ts = int(data[-1]["fundingTime"])
            if last_ts >= end_ts or len(data) < 1000:
                break
            cursor = last_ts + 1

    cache_path.write_text(json.dumps(out))
    return out


def funding_rate_at(events: list[tuple[int, float]], ts_ms: int) -> float:
    """Return the funding rate that *just settled* at or before ts_ms.

    Settlement happens at fundingTime; the rate applies to that single 8h
    interval. We bisect to find the most recent event ≤ ts_ms.
    """
    if not events:
        return 0.0
    times = [e[0] for e in events]
    idx = bisect_left(times, ts_ms + 1) - 1
    if idx < 0:
        return 0.0
    return events[idx][1]


# ── Backtest engine ───────────────────────────────────────────────────


class BacktestEngine:
    """Runs the DayTrader strategy on historical data."""

    def __init__(
        self,
        config: DayTraderConfig,
        maker_rate: float = 0.0,
        taker_rate: float = 0.0004,
        legacy_fill: bool = False,
        funding_by_symbol: dict[str, list[tuple[int, float]]] | None = None,
    ) -> None:
        self._cfg = config
        self._bus = EventBus()
        self._clock = BacktestClock()
        self._trader = DayTrader(config=config, event_bus=self._bus, clock=self._clock)
        self._sim = FillSimulator(maker_rate, taker_rate, legacy_fill=legacy_fill)
        self._funding_by_symbol = funding_by_symbol or {}
        self._next_funding_idx: dict[str, int] = {s: 0 for s in self._funding_by_symbol}

        # Tracking
        self._daily_results: dict[str, dict] = {}  # date → {profit, trades, ...}
        self._daily_profits_by_date: dict[str, float] = {}  # date → accumulated profit
        self._current_date: str = ""
        self._all_profits: list[float] = []
        self._total_commission: float = 0.0
        self._total_funding: float = 0.0
        self._total_fills: int = 0
        self._mode_switches: int = 0
        self._stop_losses: int = 0
        self._breakouts: int = 0
        self._reviews_closed: int = 0
        self._bars_processed: int = 0

        # Subscribe to events
        self._bus.subscribe("GridProfitEvent", self._on_profit)

    def _on_profit(self, event) -> None:
        self._all_profits.append(event.profit_usd)

    def run(self, klines_by_symbol: dict[str, list[dict]]) -> dict:
        """Run backtest on merged, time-sorted klines.

        Args:
            klines_by_symbol: {symbol: [kline_dicts]} — already downloaded

        Returns:
            Summary dict with all metrics
        """
        # Merge and sort all klines by timestamp
        all_bars: list[tuple[str, dict]] = []
        for symbol, klines in klines_by_symbol.items():
            for k in klines:
                all_bars.append((symbol, k))

        all_bars.sort(key=lambda x: x[1]["timestamp"])

        logger.info(
            "backtest_start",
            total_bars=len(all_bars),
            symbols=list(klines_by_symbol.keys()),
        )

        prev_mode = self._trader.mode
        prev_date = ""

        for symbol, k in all_bars:
            ts = datetime.fromtimestamp(k["timestamp"] / 1000, tz=UTC)

            # Advance the backtest clock so DayTrader sees correct date
            self._clock.set_time(ts)

            bar = MarketEvent(
                timestamp=ts,
                symbol=symbol,
                timeframe=self._cfg.timeframe,
                open=k["open"],
                high=k["high"],
                low=k["low"],
                close=k["close"],
                volume=k["volume"],
                source="backtest",
            )

            self._bars_processed += 1

            # Track date changes
            date_str = ts.strftime("%Y-%m-%d")
            if date_str != self._current_date:
                if self._current_date:
                    self._snapshot_daily(self._current_date)
                self._current_date = date_str

            # 1. Process bar through DayTrader (creates grids, handles reviews, etc.)
            signals = self._trader.on_bar(bar)

            # Track stop-losses and breakouts
            for grid in list(self._trader._engine._grids.values()):
                if grid.closed and grid.close_reason == "stop_loss":
                    if not hasattr(grid, "_counted"):
                        self._stop_losses += 1
                        grid._counted = True
                elif grid.closed and grid.close_reason == "breakout":
                    if not hasattr(grid, "_counted"):
                        self._breakouts += 1
                        grid._counted = True
                elif grid.closed and "review:" in grid.close_reason:
                    if not hasattr(grid, "_counted"):
                        self._reviews_closed += 1
                        grid._counted = True

            # 1.5 Charge close costs for newly-closed grids:
            #   SL  → market order (taker fee + ATR slippage)
            #   breakout / review → limit order at current price (maker = 0%)
            for grid in self._trader._engine._grids.values():
                if grid.closed and not hasattr(grid, "_fee_charged"):
                    if grid.close_reason == "stop_loss":
                        self._total_commission += self._sim.compute_taker_close(grid, bar)
                    grid._fee_charged = True

            # Funding settlement: charge any funding events whose timestamp
            # falls at or before this bar (and that haven't been charged yet).
            self._settle_funding(symbol, bar)

            # 2. Update ATR then simulate fills for active grids
            self._sim.update_atr(bar)
            active_grids = self._trader._engine.active_grids
            fills = self._sim.check_fills(active_grids, bar)

            for fill in fills:
                self._total_fills += 1
                self._total_commission += fill.commission
                # Process fill through DayTrader
                self._trader.on_fill(fill)

            # 3. Track mode switches
            if self._trader.mode != prev_mode:
                if self._trader.mode == TradingMode.CONSERVATIVE:
                    self._mode_switches += 1
                prev_mode = self._trader.mode

        # Final day
        if self._current_date:
            self._snapshot_daily(self._current_date)

        return self._compile_results()

    def _settle_funding(self, symbol: str, bar: MarketEvent) -> None:
        events = self._funding_by_symbol.get(symbol)
        if not events:
            return
        idx = self._next_funding_idx[symbol]
        bar_ts_ms = int(bar.timestamp.timestamp() * 1000)
        while idx < len(events) and events[idx][0] <= bar_ts_ms:
            ts_ms, rate = events[idx]
            for grid in self._trader._engine.active_grids:
                if grid.symbol != symbol:
                    continue
                charge = self._sim.compute_funding(grid, bar.close, rate)
                self._total_funding += charge
            idx += 1
        self._next_funding_idx[symbol] = idx

    def _snapshot_daily(self, date_str: str) -> None:
        """Snapshot daily state — called when date changes."""
        self._daily_results[date_str] = {
            "profit": round(self._trader.daily_profit, 4),
            "mode": self._trader.mode.value,
            "resets": self._trader._daily_resets,
            "halted": self._trader.is_halted,
            "capital_end_of_day": round(self._cfg.total_capital_usd, 4),
        }

    def _compile_results(self) -> dict:
        """Compile all backtest metrics."""
        total_profit = sum(self._all_profits)
        net_profit = total_profit - self._total_commission - self._total_funding
        num_trades = len(self._all_profits)

        # Daily stats
        daily_profits = [d["profit"] for d in self._daily_results.values()]
        winning_days = sum(1 for p in daily_profits if p > 0)
        losing_days = sum(1 for p in daily_profits if p < 0)
        flat_days = sum(1 for p in daily_profits if p == 0)
        target_days = sum(
            1 for d in self._daily_results.values()
            if d["profit"] >= self._cfg.daily_profit_target_usd
        )
        halted_days = sum(1 for d in self._daily_results.values() if d["halted"])

        max_daily = max(daily_profits) if daily_profits else 0
        min_daily = min(daily_profits) if daily_profits else 0
        avg_daily = sum(daily_profits) / len(daily_profits) if daily_profits else 0

        # Drawdown
        equity_curve = []
        running = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in self._all_profits:
            running += p
            equity_curve.append(running)
            peak = max(peak, running)
            dd = peak - running
            max_dd = max(max_dd, dd)

        # Per-trade stats
        wins = [p for p in self._all_profits if p > 0]
        losses = [p for p in self._all_profits if p <= 0]
        win_rate = len(wins) / num_trades * 100 if num_trades > 0 else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        return {
            "period": {
                "days": len(self._daily_results),
                "bars": self._bars_processed,
                "start": min(self._daily_results.keys()) if self._daily_results else "",
                "end": max(self._daily_results.keys()) if self._daily_results else "",
            },
            "capital": self._cfg.total_capital_usd,
            "pnl": {
                "gross_profit": round(total_profit, 4),
                "commission": round(self._total_commission, 4),
                "funding": round(self._total_funding, 4),
                "net_profit": round(net_profit, 4),
                "roi_pct": round(net_profit / self._cfg.total_capital_usd * 100, 2),
            },
            "trades": {
                "total_fills": self._total_fills,
                "matched_trades": num_trades,
                "win_rate_pct": round(win_rate, 1),
                "avg_win": round(avg_win, 4),
                "avg_loss": round(avg_loss, 4),
                "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else float("inf"),
            },
            "daily": {
                "winning_days": winning_days,
                "losing_days": losing_days,
                "flat_days": flat_days,
                "target_hit_days": target_days,
                "halted_days": halted_days,
                "max_daily_profit": round(max_daily, 4),
                "min_daily_profit": round(min_daily, 4),
                "avg_daily_profit": round(avg_daily, 4),
            },
            "risk": {
                "max_drawdown": round(max_dd, 4),
                "max_drawdown_pct": round(max_dd / self._cfg.total_capital_usd * 100.0, 2)
                    if self._cfg.total_capital_usd > 0 else 0.0,
                "sharpe": round(self._sharpe(daily_profits), 3),
                "calmar": round(self._calmar(net_profit, max_dd, len(daily_profits)), 3),
                "score": round(self._score(net_profit, max_dd, len(daily_profits)), 3),
                "stop_losses": self._stop_losses,
                "breakouts": self._breakouts,
                "reviews_closed": self._reviews_closed,
                "mode_switches": self._mode_switches,
            },
            "daily_detail": self._daily_results,
        }

    def _daily_returns_pct(self, daily_profits: list[float]) -> list[float]:
        cap = self._cfg.total_capital_usd
        return [p / cap * 100.0 for p in daily_profits] if cap > 0 else []

    def _sharpe(self, daily_profits: list[float]) -> float:
        return compute_sharpe(self._daily_returns_pct(daily_profits))

    def _calmar(self, net_profit: float, max_dd: float, days: int) -> float:
        cap = self._cfg.total_capital_usd or 1.0
        roi = net_profit / cap * 100.0
        max_dd_pct = max_dd / cap * 100.0
        return compute_calmar(roi, max(days, 1), max_dd_pct)

    def _score(self, net_profit: float, max_dd: float, days: int) -> float:
        cap = self._cfg.total_capital_usd or 1.0
        roi = net_profit / cap * 100.0
        max_dd_pct = max_dd / cap * 100.0
        return compute_score(
            sharpe=self._sharpe([d["profit"] for d in self._daily_results.values()]),
            calmar=compute_calmar(roi, max(days, 1), max_dd_pct),
            roi_pct=roi,
            max_dd_pct=max_dd_pct,
        )


# ── Report formatting ─────────────────────────────────────────────────


def print_report(results: dict) -> None:
    """Print a formatted backtest report."""
    period = results["period"]
    pnl = results["pnl"]
    trades = results["trades"]
    daily = results["daily"]
    risk = results["risk"]

    print("\n" + "=" * 60)
    print("  JACKBOT_V1 回測報告")
    print("=" * 60)

    print(f"\n📅 期間: {period['start']} → {period['end']} ({period['days']} 天)")
    print(f"📊 處理 K 線: {period['bars']:,} 根")
    print(f"💰 初始資金: ${results['capital']:.2f}")

    print(f"\n{'─' * 40}")
    print("  💵 損益")
    print(f"{'─' * 40}")
    print(f"  毛利潤:      ${pnl['gross_profit']:>10.4f}")
    print(f"  手續費:      ${pnl['commission']:>10.4f}")
    print(f"  Funding:     ${pnl.get('funding', 0.0):>10.4f}")
    print(f"  淨利潤:      ${pnl['net_profit']:>10.4f}")
    print(f"  投資報酬率:   {pnl['roi_pct']:>9.2f}%")

    print(f"\n{'─' * 40}")
    print("  📈 交易統計")
    print(f"{'─' * 40}")
    print(f"  總成交:       {trades['total_fills']:>8}")
    print(f"  配對完成:     {trades['matched_trades']:>8}")
    print(f"  勝率:         {trades['win_rate_pct']:>7.1f}%")
    print(f"  平均獲利:    ${trades['avg_win']:>10.4f}")
    print(f"  平均虧損:    ${trades['avg_loss']:>10.4f}")
    print(f"  獲利因子:     {trades['profit_factor']:>8.2f}")

    print(f"\n{'─' * 40}")
    print("  📅 每日統計")
    print(f"{'─' * 40}")
    print(f"  獲利天:       {daily['winning_days']:>8}")
    print(f"  虧損天:       {daily['losing_days']:>8}")
    print(f"  持平天:       {daily['flat_days']:>8}")
    print(f"  達標天:       {daily['target_hit_days']:>8}  (日標 ≥ ${results.get('capital', 0) and 10})")
    print(f"  暫停天:       {daily['halted_days']:>8}")
    print(f"  最佳日:      ${daily['max_daily_profit']:>10.4f}")
    print(f"  最差日:      ${daily['min_daily_profit']:>10.4f}")
    print(f"  平均日利潤:  ${daily['avg_daily_profit']:>10.4f}")

    print(f"\n{'─' * 40}")
    print("  🛡️ 風控")
    print(f"{'─' * 40}")
    print(f"  最大回撤:    ${risk['max_drawdown']:>10.4f}")
    print(f"  止損觸發:     {risk['stop_losses']:>8}")
    print(f"  突破關閉:     {risk['breakouts']:>8}")
    print(f"  Review關閉:   {risk['reviews_closed']:>8}")
    print(f"  模式切換:     {risk['mode_switches']:>8}")

    # Daily detail table
    print(f"\n{'─' * 60}")
    print("  📋 每日明細")
    print(f"{'─' * 60}")
    print(f"  {'日期':<12} {'利潤':>10} {'模式':<14} {'重置':>4} {'暫停':>4}")
    print(f"  {'─'*12} {'─'*10} {'─'*14} {'─'*4} {'─'*4}")

    for date, d in sorted(results["daily_detail"].items()):
        profit_str = f"${d['profit']:>9.4f}"
        mode_emoji = "🛡️" if d["mode"] == "conservative" else "⚡"
        halt_str = "🚨" if d["halted"] else "  "
        print(f"  {date:<12} {profit_str:>10} {mode_emoji} {d['mode']:<12} {d['resets']:>4} {halt_str:>4}")

    print(f"\n{'=' * 60}")
    print(f"  🏁 最終結果: {'✅ 盈利' if pnl['net_profit'] > 0 else '❌ 虧損'}  "
          f"${pnl['net_profit']:.4f} ({pnl['roi_pct']:.2f}%)")
    print(f"{'=' * 60}\n")


# ── Main ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Jackbot_V1 Backtest")
    parser.add_argument("--days", type=int, default=30, help="Number of days to backtest")
    parser.add_argument("--start", type=str, default="", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=150.0, help="Initial capital (USDT)")
    parser.add_argument("--target", type=float, default=10.0, help="Daily target (USDT)")
    parser.add_argument("--max-leverage", type=int, default=10, help="Max leverage")
    parser.add_argument("--min-leverage", type=int, default=5, help="Min leverage")
    parser.add_argument("--stop-loss", type=float, default=2.0, help="Grid stop-loss %")
    parser.add_argument("--grid-count", type=int, default=10, help="Default grid count")
    parser.add_argument("--compound", type=float, default=0.0, help="Compound profit % (e.g. 50.0)")
    parser.add_argument("--maker-fee", type=float, default=0.0, help="Maker fee rate (negative = rebate)")
    parser.add_argument("--taker-fee", type=float, default=0.0004, help="Taker fee rate (SL/breakout/review, default 0.0004)")
    parser.add_argument("--legacy-fill", action="store_true", help="Use legacy random 50–100% fill ratio (parity check)")
    parser.add_argument("--no-funding", action="store_true", help="Skip historical funding rate fetch+settlement")
    parser.add_argument("--out", type=str, default="", help="Output JSON file path")
    args = parser.parse_args()

    def p(msg, **kwargs):
        if not args.out:
            print(msg, **kwargs)

    # Calculate time range
    if args.start:
        start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    else:
        start_dt = datetime.now(UTC) - timedelta(days=args.days)

    end_dt = datetime.now(UTC)
    start_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)

    symbols = ["BTCUSDC", "ETHUSDC"]
    p(f"\n🔄 下載歷史 K 線...")
    p(f"   期間: {start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')}")
    p(f"   幣種: {', '.join(symbols)}")

    # Download klines
    klines_by_symbol: dict[str, list[dict]] = {}
    for symbol in symbols:
        p(f"   📥 {symbol}...", end=" ", flush=True)
        klines = download_klines(symbol, "5m", start_ts, end_ts)
        klines_by_symbol[symbol] = klines
        p(f"{len(klines):,} 根")

    # Configure
    config = DayTraderConfig(
        symbols=symbols,
        timeframe="5m",
        total_capital_usd=args.capital,
        per_symbol_alloc_pct=50.0,
        compound_pct=args.compound,
        default_grid_count=args.grid_count,
        max_leverage=args.max_leverage,
        min_leverage=args.min_leverage,
        daily_profit_target_usd=args.target,
        daily_loss_limit_pct=10.0,
        grid_stop_loss_pct=args.stop_loss,
        conservative_size_factor=0.25,
        conservative_grid_spacing_mult=2.0,
        conservative_leverage=3,
        max_concurrent_grids=2,
        max_daily_resets=10,
        hourly_review_interval_bars=12,
        warmup_bars=50,
    )

    # Run backtest
    p(f"\n⚙️  回測中...")
    funding_by_symbol: dict[str, list[tuple[int, float]]] = {}
    if not args.no_funding:
        for sym in symbols:
            try:
                funding_by_symbol[sym] = download_funding(sym, start_ts, end_ts)
                p(f"   📥 funding {sym}: {len(funding_by_symbol[sym])} events")
            except Exception as e:
                logger.warning("funding_fetch_failed", symbol=sym, error=str(e))
                funding_by_symbol[sym] = []

    engine = BacktestEngine(
        config,
        maker_rate=args.maker_fee,
        taker_rate=args.taker_fee,
        legacy_fill=args.legacy_fill,
        funding_by_symbol=funding_by_symbol,
    )
    results = engine.run(klines_by_symbol)

    # Print report
    if args.out:
        import json
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        # Force a simple english print to show completion
        print(f"Backtest completed successfully. Results saved to {args.out}")
    else:
        print_report(results)


if __name__ == "__main__":
    main()
