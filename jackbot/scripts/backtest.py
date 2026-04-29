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
import random
import sys
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Fix Windows console encoding for Chinese + emoji
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import httpx
import structlog

from jackbot.core.clock import Clock
from jackbot.core.constants import GridDirection, GridLevelState, TradingMode
from jackbot.core.event_bus import EventBus
from jackbot.core.events import FillEvent, MarketEvent, StrategyPnLEvent
from jackbot.config_utils import build_day_trader_params, load_merged_config
from jackbot.strategy.day_trader import DayTrader, DayTraderConfig
from jackbot.strategy.grid_engine import GridInstance

logger = structlog.get_logger("backtest")


def load_config(path: str = "config/settings.yaml") -> dict:
    config_path = ROOT / path
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    return load_merged_config(config_path)


def resolve_fee_rates(
    fee_model: str,
    fees_cfg: dict,
    maker_override: float | None,
    taker_override: float | None,
) -> tuple[float, float]:
    """Resolve maker/taker fee rates for backtesting."""
    if fee_model == "binance_futures":
        maker_rate = 0.00018
        taker_rate = 0.00045
    elif fee_model == "zero_maker":
        maker_rate = 0.0
        taker_rate = 0.00045
    else:
        maker_rate = fees_cfg.get("maker", 0.0)
        taker_rate = fees_cfg.get("taker", 0.0004)

    if maker_override is not None:
        maker_rate = maker_override
    if taker_override is not None:
        taker_rate = taker_override

    return maker_rate, taker_rate


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

    Grid limit orders are treated as maker fills (zero fee on Binance).
    SL/breakout/review market closes are taker fills (0.04% + ATR slippage).
    Partial fills: 50–100% random fill rate per level.
    """

    _ATR_PERIOD = 14

    def __init__(self, maker_rate: float = 0.0, taker_rate: float = 0.0004) -> None:
        self._fill_count = 0
        self._maker_rate = maker_rate
        self._taker_rate = taker_rate
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

    def _atr_slippage(self, symbol: str, close_price: float, notional: float) -> float:
        atr = self._atr_values.get(symbol, 0.0)
        if close_price <= 0 or notional <= 0:
            return 0.0
        return notional * (atr / close_price) * 0.02

    def estimate_close_breakdown(self, symbol: str, close_price: float, notional: float, order_type: str) -> dict[str, float]:
        """Estimate close-side fee/slippage breakdown for a forced grid exit."""
        if notional <= 0:
            return {"maker_fee": 0.0, "taker_fee": 0.0, "slippage": 0.0}
        if order_type == "MARKET":
            return {
                "maker_fee": 0.0,
                "taker_fee": notional * self._taker_rate,
                "slippage": self._atr_slippage(symbol, close_price, notional),
            }
        return {
            "maker_fee": notional * self._maker_rate,
            "taker_fee": 0.0,
            "slippage": 0.0,
        }

    def realize_close(self, grid: GridInstance, bar: MarketEvent, close_kind: str) -> tuple[float, dict[str, float]]:
        """Realize unmatched position PnL and close-side costs for a grid exit."""
        realized_pnl = 0.0
        close_notional = 0.0

        for level in grid.levels:
            if level.buy_fill_price > 0 and level.sell_fill_price == 0:
                realized_pnl += (bar.close - level.buy_fill_price) * level.quantity
                close_notional += bar.close * level.quantity
            elif level.sell_fill_price > 0 and level.buy_fill_price == 0:
                realized_pnl += (level.sell_fill_price - bar.close) * level.quantity
                close_notional += bar.close * level.quantity

        close_order_type = "MARKET" if close_kind == "stop_loss" else "LIMIT"
        close_breakdown = self.estimate_close_breakdown(grid.symbol, bar.close, close_notional, close_order_type)
        rounded = {key: round(value, 6) for key, value in close_breakdown.items()}
        return round(realized_pnl, 4), rounded

    def check_fills(
        self,
        grids: list[GridInstance],
        bar: MarketEvent,
    ) -> list[FillEvent]:
        """Check all active grids for fills based on this bar's price range.

        All grid-level fills are passive limit orders (maker, zero fee).
        Applies a 50–100% random partial fill rate per level.
        """
        fills: list[FillEvent] = []

        for grid in grids:
            if grid.closed:
                continue

            for level in grid.levels:
                # Check BUY fills: price dropped to or below the level
                if (
                    level.state == GridLevelState.PENDING_BUY
                    and level.price >= bar.low
                    and level.price <= bar.high
                ):
                    self._fill_count += 1
                    qty = level.quantity * random.uniform(0.5, 1.0)
                    fills.append(FillEvent(
                        timestamp=bar.timestamp,
                        symbol=bar.symbol,
                        side="BUY",
                        quantity=qty,
                        price=level.price,
                        commission=level.price * qty * self._maker_rate,
                        order_id=f"sim_{self._fill_count}",
                        grid_id=grid.grid_id,
                        level_index=level.index,
                    ))

                # Check SELL fills: price rose to or above the level
                elif (
                    level.state == GridLevelState.PENDING_SELL
                    and level.price >= bar.low
                    and level.price <= bar.high
                ):
                    self._fill_count += 1
                    qty = level.quantity * random.uniform(0.5, 1.0)
                    fills.append(FillEvent(
                        timestamp=bar.timestamp,
                        symbol=bar.symbol,
                        side="SELL",
                        quantity=qty,
                        price=level.price,
                        commission=level.price * qty * self._maker_rate,
                        order_id=f"sim_{self._fill_count}",
                        grid_id=grid.grid_id,
                        level_index=level.index,
                    ))

        return fills


# ── Backtest engine ───────────────────────────────────────────────────


class BacktestEngine:
    """Runs the DayTrader strategy on historical data."""

    def __init__(
        self,
        config: DayTraderConfig,
        maker_rate: float = 0.0,
        taker_rate: float = 0.0004,
    ) -> None:
        self._cfg = config
        self._initial_capital = config.total_capital_usd
        self._bus = EventBus()
        self._clock = BacktestClock()
        self._trader = DayTrader(config=config, event_bus=self._bus, clock=self._clock)
        self._trader.mark_warmup_complete()
        self._sim = FillSimulator(maker_rate, taker_rate)

        # Tracking
        self._daily_results: dict[str, dict] = {}
        self._current_date: str = ""
        self._matched_trade_pnls: list[float] = []
        self._close_pnls: list[float] = []
        self._close_net_pnls: list[float] = []
        self._gross_profit_total: float = 0.0
        self._realized_profit_total: float = 0.0
        self._total_commission: float = 0.0
        self._maker_fee_total: float = 0.0
        self._taker_fee_total: float = 0.0
        self._slippage_total: float = 0.0
        self._total_fills: int = 0
        self._mode_switches: int = 0
        self._stop_losses: int = 0
        self._breakouts: int = 0
        self._reviews_closed: int = 0
        self._bars_processed: int = 0
        self._snapshot_realized_profit: float = 0.0
        self._snapshot_commission: float = 0.0
        self._snapshot_grid_net: float = 0.0
        self._snapshot_trend_net: float = 0.0
        self._snapshot_maker_fee: float = 0.0
        self._snapshot_taker_fee: float = 0.0
        self._grid_pnl_net: float = 0.0
        self._trend_pnl_net: float = 0.0

        # Subscribe to events
        self._bus.subscribe("GridProfitEvent", self._on_profit)
        self._bus.subscribe("StrategyPnLEvent", self._on_strategy_pnl)

    def _on_profit(self, event) -> None:
        self._matched_trade_pnls.append(event.profit_usd - event.commission)
        self._gross_profit_total += event.profit_usd
        self._realized_profit_total += event.profit_usd
        self._grid_pnl_net += event.profit_usd - event.commission

    def _on_strategy_pnl(self, event: StrategyPnLEvent) -> None:
        self._realized_profit_total += event.gross_pnl
        self._total_commission += event.commission
        if event.bucket == "trend":
            self._trend_pnl_net += event.net_pnl
        else:
            self._grid_pnl_net += event.net_pnl
        if event.source.startswith("trend_"):
            self._taker_fee_total += event.commission

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

            # 1.5 Realize close PnL/costs for newly-closed grids.
            for grid in self._trader._engine._grids.values():
                if grid.closed and not hasattr(grid, "_close_realized"):
                    close_pnl, close_breakdown = self._sim.realize_close(grid, bar, grid.close_reason)
                    close_cost = sum(close_breakdown.values())
                    self._close_pnls.append(close_pnl)
                    self._close_net_pnls.append(close_pnl - close_cost)
                    self._realized_profit_total += close_pnl
                    self._total_commission += close_cost
                    self._maker_fee_total += close_breakdown["maker_fee"]
                    self._taker_fee_total += close_breakdown["taker_fee"]
                    self._slippage_total += close_breakdown["slippage"]
                    self._grid_pnl_net += close_pnl - close_cost
                    self._trader.record_realized_pnl(close_pnl - close_cost, bucket="grid")
                    grid._close_realized = True

            # 2. Update ATR then simulate fills for active grids
            self._sim.update_atr(bar)
            active_grids = self._trader._engine.active_grids
            fills = self._sim.check_fills(active_grids, bar)

            for fill in fills:
                self._total_fills += 1
                self._total_commission += fill.commission
                self._maker_fee_total += fill.commission
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

    def _snapshot_daily(self, date_str: str) -> None:
        """Snapshot daily state — called when date changes."""
        gross_profit = self._realized_profit_total - self._snapshot_realized_profit
        commission = self._total_commission - self._snapshot_commission
        net_profit = gross_profit - commission
        grid_pnl = self._grid_pnl_net - self._snapshot_grid_net
        trend_pnl = self._trend_pnl_net - self._snapshot_trend_net
        maker_fee = self._maker_fee_total - self._snapshot_maker_fee
        taker_fee = self._taker_fee_total - self._snapshot_taker_fee
        self._daily_results[date_str] = {
            "gross_profit": round(gross_profit, 4),
            "commission": round(commission, 4),
            "net_profit": round(net_profit, 4),
            "grid_pnl": round(grid_pnl, 4),
            "trend_pnl": round(trend_pnl, 4),
            "maker_fee": round(maker_fee, 6),
            "taker_fee": round(taker_fee, 6),
            "mode": self._trader.mode.value,
            "resets": self._trader._daily_resets,
            "halted": self._trader.is_halted,
            "capital_end_of_day": round(self._cfg.total_capital_usd, 4),
        }
        self._snapshot_realized_profit = self._realized_profit_total
        self._snapshot_commission = self._total_commission
        self._snapshot_grid_net = self._grid_pnl_net
        self._snapshot_trend_net = self._trend_pnl_net
        self._snapshot_maker_fee = self._maker_fee_total
        self._snapshot_taker_fee = self._taker_fee_total

    def _compile_results(self) -> dict:
        """Compile all backtest metrics."""
        total_close_pnl = sum(self._close_pnls)
        realized_before_fees = self._realized_profit_total
        matched_grid_gross_profit = self._gross_profit_total
        gross_profit = realized_before_fees
        net_profit = realized_before_fees - self._total_commission
        ending_equity = self._initial_capital + net_profit
        num_trades = len(self._matched_trade_pnls)
        commission_ratio = self._total_commission / gross_profit if gross_profit > 0 else 0.0
        total_daily_resets = sum(d.get("resets", 0) for d in self._daily_results.values())
        max_daily_resets = max((d.get("resets", 0) for d in self._daily_results.values()), default=0)

        # Daily stats
        daily_profits = [d["net_profit"] for d in self._daily_results.values()]
        winning_days = sum(1 for p in daily_profits if p > 0)
        losing_days = sum(1 for p in daily_profits if p < 0)
        flat_days = sum(1 for p in daily_profits if p == 0)
        target_days = sum(
            1 for d in self._daily_results.values()
            if d["net_profit"] >= self._cfg.daily_profit_target_usd
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
        equity_points = self._matched_trade_pnls + self._close_pnls
        for p in equity_points:
            running += p
            equity_curve.append(running)
            peak = max(peak, running)
            dd = peak - running
            max_dd = max(max_dd, dd)

        # Per-trade stats
        realized_components = self._matched_trade_pnls + self._close_net_pnls
        wins = [p for p in realized_components if p > 0]
        losses = [p for p in realized_components if p < 0]
        realized_event_count = len(realized_components)
        win_rate = len(wins) / realized_event_count * 100 if realized_event_count > 0 else 0
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
            "ending_equity": round(ending_equity, 4),
            "initial_capital": self._initial_capital,
            "pnl": {
                "gross_profit": round(gross_profit, 4),
                "matched_grid_gross_profit": round(matched_grid_gross_profit, 4),
                "close_pnl": round(total_close_pnl, 4),
                "realized_before_fees": round(realized_before_fees, 4),
                "commission": round(self._total_commission, 4),
                "commission_to_gross_profit": round(commission_ratio, 4),
                "net_profit": round(net_profit, 4),
                "roi_pct": round(net_profit / self._initial_capital * 100, 2),
            },
            "trades": {
                "total_fills": self._total_fills,
                "matched_trades": num_trades,
                "realized_events": realized_event_count,
                "forced_close_events": len(self._close_net_pnls),
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
                "stop_losses": self._stop_losses,
                "breakouts": self._breakouts,
                "reviews_closed": self._reviews_closed,
                "mode_switches": self._mode_switches,
                "daily_reset_total": total_daily_resets,
                "max_daily_resets_used": max_daily_resets,
            },
            "fees": {
                "maker_fee_total": round(self._maker_fee_total, 6),
                "taker_fee_total": round(self._taker_fee_total, 6),
                "slippage_total": round(self._slippage_total, 6),
            },
            "strategy_pnl": {
                "grid_pnl": round(self._grid_pnl_net, 4),
                "trend_pnl": round(self._trend_pnl_net, 4),
            },
            "daily_detail": self._daily_results,
        }


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
    print(f"💰 初始資金: ${results['initial_capital']:.2f}")
    print(f"📈 期末淨值: ${results['ending_equity']:.2f}")
    print(f"🏦 策略資金: ${results['capital']:.2f}")

    print(f"\n{'─' * 40}")
    print("  💵 損益")
    print(f"{'─' * 40}")
    print(f"  毛利潤:      ${pnl['gross_profit']:>10.4f}")
    print(f"  強平/關倉PnL: ${pnl['close_pnl']:>10.4f}")
    print(f"  費前已實現:  ${pnl['realized_before_fees']:>10.4f}")
    print(f"  手續費:      ${pnl['commission']:>10.4f}")
    print(f"  淨利潤:      ${pnl['net_profit']:>10.4f}")
    print(f"  投資報酬率:   {pnl['roi_pct']:>9.2f}%")

    print(f"\n{'─' * 40}")
    print("  📈 交易統計")
    print(f"{'─' * 40}")
    print(f"  總成交:       {trades['total_fills']:>8}")
    print(f"  配對完成:     {trades['matched_trades']:>8}")
    print(f"  強制關倉:     {trades['forced_close_events']:>8}")
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
    print(f"  達標天:       {daily['target_hit_days']:>8}  (日標 ≥ ${results['target']:g})")
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
        profit_str = f"${d['net_profit']:>9.4f}"
        mode_emoji = "🛡️" if d["mode"] == "conservative" else "⚡"
        halt_str = "🚨" if d["halted"] else "  "
        print(f"  {date:<12} {profit_str:>10} {mode_emoji} {d['mode']:<12} {d['resets']:>4} {halt_str:>4}")

    print(f"\n{'=' * 60}")
    print(f"  🏁 最終結果: {'✅ 盈利' if pnl['net_profit'] > 0 else '❌ 虧損'}  "
          f"${pnl['net_profit']:.4f} ({pnl['roi_pct']:.2f}%)")
    print(f"{'=' * 60}\n")


# ── Main ──────────────────────────────────────────────────────────────


def main():
    config_data = load_config()
    trading_cfg = config_data.get("trading", {})
    grid_cfg = config_data.get("grid", {})
    targets_cfg = config_data.get("targets", {})
    conservative_cfg = config_data.get("conservative", {})
    risk_cfg = config_data.get("risk", {})
    fees_cfg = config_data.get("fees", {})

    parser = argparse.ArgumentParser(description="Jackbot_V1 Backtest")
    parser.add_argument("--days", type=int, default=30, help="Number of days to backtest")
    parser.add_argument("--start", type=str, default="", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=trading_cfg.get("total_capital_usd", 150.0), help="Initial capital (USDT)")
    parser.add_argument("--target", type=float, default=targets_cfg.get("daily_profit_target_usd", 10.0), help="Daily target (USDT)")
    parser.add_argument("--max-leverage", type=int, default=grid_cfg.get("max_leverage", 10), help="Max leverage")
    parser.add_argument("--min-leverage", type=int, default=grid_cfg.get("min_leverage", 5), help="Min leverage")
    parser.add_argument("--stop-loss", type=float, default=targets_cfg.get("grid_stop_loss_pct", 2.0), help="Grid stop-loss %")
    parser.add_argument("--grid-count", type=int, default=grid_cfg.get("default_grid_count", 10), help="Default grid count")
    parser.add_argument("--compound", type=float, default=trading_cfg.get("compound_pct", 0.0), help="Compound profit % (e.g. 50.0)")
    parser.add_argument("--fee-model", choices=["config", "binance_futures", "zero_maker"], default="config", help="Fee preset to use")
    parser.add_argument("--maker-fee", type=float, default=None, help="Override maker fee rate")
    parser.add_argument("--taker-fee", type=float, default=None, help="Override taker fee rate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for partial-fill simulation")
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

    symbols = trading_cfg.get("symbols", ["BTCUSDT", "ETHUSDT"])
    timeframe = trading_cfg.get("timeframe", "5m")
    per_symbol_alloc_pct = trading_cfg.get("per_symbol_alloc_pct", 50.0)
    maker_rate, taker_rate = resolve_fee_rates(args.fee_model, fees_cfg, args.maker_fee, args.taker_fee)
    p(f"\n🔄 下載歷史 K 線...")
    p(f"   期間: {start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')}")
    p(f"   幣種: {', '.join(symbols)}")
    p(f"   週期: {timeframe}")
    p(f"   手續費: maker={maker_rate:.5f}, taker={taker_rate:.5f} ({args.fee_model})")
    p(f"   隨機種子: {args.seed}")

    # Download klines
    klines_by_symbol: dict[str, list[dict]] = {}
    for symbol in symbols:
        p(f"   📥 {symbol}...", end=" ", flush=True)
        klines = download_klines(symbol, timeframe, start_ts, end_ts)
        klines_by_symbol[symbol] = klines
        p(f"{len(klines):,} 根")

    # Configure
    trader_params = build_day_trader_params(
        config_data,
        capital_override=args.capital,
        maker_rate=maker_rate,
        taker_rate=taker_rate,
    )
    trader_params.update({
        "symbols": symbols,
        "timeframe": timeframe,
        "per_symbol_alloc_pct": per_symbol_alloc_pct,
        "compound_pct": args.compound,
        "default_grid_count": args.grid_count,
        "max_leverage": args.max_leverage,
        "min_leverage": args.min_leverage,
        "daily_profit_target_usd": args.target,
        "grid_stop_loss_pct": args.stop_loss,
    })
    config = DayTraderConfig.from_dict(trader_params)

    # Run backtest
    p(f"\n⚙️  回測中...")
    random.seed(args.seed)
    engine = BacktestEngine(config, maker_rate=maker_rate, taker_rate=taker_rate)
    results = engine.run(klines_by_symbol)
    results["target"] = args.target
    results["seed"] = args.seed
    results["fee_model"] = {
        "name": args.fee_model,
        "maker": maker_rate,
        "taker": taker_rate,
    }

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
