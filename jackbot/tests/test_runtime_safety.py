from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path

from jackbot.core.constants import GridDirection, GridLevelState
from jackbot.core.events import FillEvent
from jackbot.core.ownership import make_grid_client_order_id, parse_jackbot_client_order_id
from jackbot.portfolio.exchange_journal import ExchangeJournal
from jackbot.strategy.grid_engine import GridInstance, GridLevel


RUNNER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run.py"


def _load_runner_module():
    spec = importlib.util.spec_from_file_location("jackbot_runner_script", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _DummyTelegramBot:
    def __init__(self, *args, **kwargs) -> None:
        self._enabled = False
        self._token = ""
        self._chat_id = ""
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        self.messages.append(text)

    def notify_alert(self, title: str, message: str) -> None:
        self.messages.append(f"{title}:{message}")

    def notify_reconcile(self, report: dict, safe_mode: bool = False) -> None:
        self.messages.append(f"reconcile:{report.get('status')}:{safe_mode}")


class _DummyKlineFeed:
    def __init__(self, *args, **kwargs) -> None:
        self.on_bar = None

    async def start(self) -> None:
        return None


class _DummyUserDataStream:
    def __init__(self, *args, **kwargs) -> None:
        self.on_fill = None

    async def start(self) -> None:
        return None


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        self._next_order_id = 1000
        self.open_orders: dict[str, list[dict]] = {}
        self.positions: dict[str, dict] = {}

    def min_notional(self, symbol: str) -> float:
        return 20.0

    def order_notional(self, symbol: str, price: float, quantity: float) -> float:
        return float(price) * float(quantity)

    def get_open_orders(self, symbol: str) -> list[dict]:
        return list(self.open_orders.get(symbol, []))

    def get_position(self, symbol: str) -> dict:
        return self.positions.get(
            symbol,
            {
                "symbol": symbol,
                "positionAmt": "0",
                "entryPrice": "0",
                "markPrice": "0",
                "unRealizedProfit": "0",
                "leverage": "5",
                "marginType": "isolated",
            },
        )

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        reduce_only: bool = False,
        client_order_id: str = "",
    ) -> dict:
        self._next_order_id += 1
        order = {
            "symbol": symbol,
            "side": side,
            "price": price,
            "origQty": quantity,
            "reduceOnly": reduce_only,
            "clientOrderId": client_order_id,
            "orderId": str(self._next_order_id),
        }
        self.open_orders.setdefault(symbol, []).append(order)
        return {"orderId": str(self._next_order_id)}

    def cancel_order(self, symbol: str, order_id: str) -> None:
        orders = self.open_orders.get(symbol, [])
        self.open_orders[symbol] = [order for order in orders if str(order.get("orderId")) != str(order_id)]

    def set_margin_type(self, symbol: str, margin_type: str) -> None:
        return None

    def set_leverage(self, symbol: str, leverage: int) -> None:
        return None

    def close(self) -> None:
        return None


def _make_runner(tmp_path, monkeypatch):
    runner_module = _load_runner_module()
    monkeypatch.setattr(
        runner_module,
        "ExchangeJournal",
        lambda: ExchangeJournal(tmp_path / "exchange_journal.db"),
    )
    monkeypatch.setattr(runner_module, "TelegramBot", _DummyTelegramBot)
    monkeypatch.setattr(runner_module, "KlineFeed", _DummyKlineFeed)
    monkeypatch.setattr(runner_module, "UserDataStream", _DummyUserDataStream)
    monkeypatch.setattr(runner_module, "BinanceClient", _FakeClient)

    config = {
        "strategy": {"variant": "baseline_grid"},
        "exchange": {
            "mode": "testnet",
            "base_url": "https://testnet.binancefuture.com",
            "ws_url": "wss://fstream.binancefuture.com",
            "api_key_env": "TEST_API_KEY",
            "api_secret_env": "TEST_API_SECRET",
        },
        "trading": {
            "symbols": ["ETHUSDC"],
            "timeframe": "5m",
            "total_capital_usd": 150.0,
        },
        "grid": {
            "default_grid_count": 8,
            "max_leverage": 10,
            "min_leverage": 5,
            "warmup_bars": 10,
        },
        "targets": {
            "daily_profit_target_usd": 4.5,
            "daily_loss_limit_pct": 10.0,
            "grid_stop_loss_pct": 3.0,
        },
        "risk": {
            "max_concurrent_grids": 2,
            "max_daily_resets": 10,
        },
        "telegram": {
            "enabled": False,
        },
    }
    runner = runner_module.JackbotRunner(config=config, dry_run=True)
    return runner


def test_grid_client_order_id_round_trip() -> None:
    client_id = make_grid_client_order_id("grid_ETHUSDC_abcdef12", 3, "SELL", 2)

    assert client_id == "jb_grid_grid_ETHUSDC_abcdef12_03_S_02"
    assert parse_jackbot_client_order_id(client_id) == ("grid_ETHUSDC_abcdef12", 3)


def test_exchange_journal_persists_fill_context(tmp_path) -> None:
    journal = ExchangeJournal(tmp_path / "exchange_journal.db")
    fill = FillEvent(
        timestamp=datetime(2026, 5, 1, tzinfo=UTC),
        symbol="ETHUSDC",
        side="SELL",
        quantity=0.066,
        price=2260.26,
        commission=0.02685188,
        commission_asset="USDC",
        realized_pnl=0.38378448,
        order_id="289322692",
        trade_id="62761431",
        client_order_id="jb_grid_grid_ETHUSDC_abcdef12_02_S_00",
        grid_id="grid_ETHUSDC_abcdef12",
        level_index=2,
        source="user_data",
    )

    assert journal.record_fill(fill)

    summary = journal.get_summary_since("2026-05-01T00:00:00+00:00")
    recent = journal.recent_fills(1)[0]

    assert summary.today_fills == 1
    assert summary.today_realized_pnl == 0.38378448
    assert summary.today_commission == 0.02685188
    assert recent["grid_id"] == "grid_ETHUSDC_abcdef12"
    assert recent["level_index"] == 2


def test_exchange_journal_restores_runtime_state_and_active_grid(tmp_path) -> None:
    journal = ExchangeJournal(tmp_path / "exchange_journal.db")
    grid = GridInstance(
        grid_id="grid_ETHUSDC_restore01",
        symbol="ETHUSDC",
        direction=GridDirection.LONG,
        upper_price=2280.0,
        lower_price=2220.0,
        grid_count=4,
        leverage=5,
        total_investment=150.0,
        per_level_qty=0.066,
        matched_profit=0.5,
        unrealized_pnl=1.25,
        total_matched=2,
        created_at=datetime(2026, 5, 1, 8, 0, tzinfo=UTC),
    )
    grid.levels.extend(
        [
            GridLevel(
                index=0,
                price=2220.0,
                state=GridLevelState.FILLED_BUY,
                buy_order_id="1001",
                buy_fill_price=2220.0,
                quantity=0.066,
                filled_quantity=0.066,
                commission=0.01,
            ),
            GridLevel(
                index=1,
                price=2235.0,
                state=GridLevelState.PENDING_SELL,
                sell_order_id="1002",
                quantity=0.066,
            ),
        ]
    )
    journal.save_grid_snapshot(grid)
    journal.save_runtime_state(
        session_started_at="2026-05-01T08:00:00+00:00",
        safe_mode_reason="startup_exchange_orphan_state",
        trader_state={"mode": "aggressive", "daily_profit": 1.2},
        portfolio_state={"initial_capital": 150.0, "realized_pnl": 0.8, "total_commission": 0.1},
        metadata={"strategy_variant": "baseline_grid"},
    )

    restored_state = journal.load_runtime_state()
    restored_grids = journal.load_active_grids()

    assert restored_state is not None
    assert restored_state["safe_mode_reason"] == "startup_exchange_orphan_state"
    assert restored_state["trader_state"]["daily_profit"] == 1.2
    assert restored_state["portfolio_state"]["realized_pnl"] == 0.8
    assert restored_state["metadata"]["strategy_variant"] == "baseline_grid"

    assert len(restored_grids) == 1
    restored_grid = restored_grids[0]
    assert restored_grid.grid_id == "grid_ETHUSDC_restore01"
    assert restored_grid.direction == GridDirection.LONG
    assert len(restored_grid.levels) == 2
    assert restored_grid.levels[0].state == GridLevelState.FILLED_BUY
    assert restored_grid.levels[0].buy_order_id == "1001"
    assert restored_grid.levels[1].state == GridLevelState.PENDING_SELL
    assert restored_grid.levels[1].sell_order_id == "1002"


def test_repair_plan_detects_missing_counter_sell(tmp_path, monkeypatch) -> None:
    runner = _make_runner(tmp_path, monkeypatch)
    grid = GridInstance(
        grid_id="grid_ETHUSDC_repair01",
        symbol="ETHUSDC",
        direction=GridDirection.LONG,
        upper_price=2280.0,
        lower_price=2220.0,
        grid_count=4,
        leverage=5,
        total_investment=150.0,
        per_level_qty=0.01,
    )
    grid.levels.extend(
        [
            GridLevel(
                index=0,
                price=2220.0,
                state=GridLevelState.FILLED_BUY,
                buy_fill_price=2220.0,
                buy_order_id="2001",
                quantity=0.01,
                filled_quantity=0.01,
            ),
            GridLevel(
                index=1,
                price=2240.0,
                state=GridLevelState.PENDING_SELL,
                quantity=0.01,
            ),
        ]
    )
    runner._trader._engine.restore_grid(grid)

    plan = runner._build_repair_plan(refresh_exchange_state=True)

    assert plan["status"] == "recoverable"
    assert len(plan["recoverable_actions"]) == 1
    action = plan["recoverable_actions"][0]
    assert action["kind"] == "place_missing_counter_sell"
    assert action["symbol"] == "ETHUSDC"
    assert action["level_index"] == 1
    assert action["side"] == "SELL"


def test_repair_confirm_places_recoverable_order_on_testnet(tmp_path, monkeypatch) -> None:
    runner = _make_runner(tmp_path, monkeypatch)
    grid = GridInstance(
        grid_id="grid_ETHUSDC_repair02",
        symbol="ETHUSDC",
        direction=GridDirection.LONG,
        upper_price=2280.0,
        lower_price=2220.0,
        grid_count=4,
        leverage=5,
        total_investment=150.0,
        per_level_qty=0.01,
    )
    grid.levels.extend(
        [
            GridLevel(
                index=0,
                price=2220.0,
                state=GridLevelState.FILLED_BUY,
                buy_fill_price=2220.0,
                buy_order_id="3001",
                quantity=0.01,
                filled_quantity=0.01,
            ),
            GridLevel(
                index=1,
                price=2240.0,
                state=GridLevelState.PENDING_SELL,
                quantity=0.01,
            ),
        ]
    )
    runner._trader._engine.restore_grid(grid)
    runner._safe_mode_reason = "startup_exchange_orphan_state"
    runner._trader.enter_safe_mode("startup_exchange_orphan_state")

    plan = runner._execute_repair_plan("confirm")

    execution = plan["execution"]
    assert execution["status"] == "applied"
    assert execution["applied"] == 1
    assert execution["failed"] == 0
    assert plan["recoverable_actions"] == []

    open_orders = runner._client.get_open_orders("ETHUSDC")
    assert len(open_orders) == 1
    assert open_orders[0]["side"] == "SELL"
    assert open_orders[0]["clientOrderId"].startswith("jb_grid_grid_ETHUSDC_repair02_01_S_")
