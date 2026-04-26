"""Regression tests for Binance order precision handling."""

from jackbot.exchange.client import BinanceClient


def test_limit_order_is_snapped_to_tick_and_step() -> None:
    client = BinanceClient(api_key="k", api_secret="s", testnet=True)
    captured: dict = {}

    def fake_signed_post(path: str, params: dict):
        captured["path"] = path
        captured["params"] = params
        return {"orderId": 123}

    client._signed_post = fake_signed_post  # type: ignore[method-assign]

    client._price_tick_size["BTCUSDT"] = 0.1
    client._qty_step_size["BTCUSDT"] = 0.0001

    client.place_limit_order(
        symbol="BTCUSDT",
        side="BUY",
        price=77186.62,
        quantity=0.001613,
    )

    assert captured["path"] == "/fapi/v1/order"
    assert captured["params"]["price"] == "77186.6"
    assert captured["params"]["quantity"] == "0.0016"


def test_market_order_quantity_is_snapped_to_step() -> None:
    client = BinanceClient(api_key="k", api_secret="s", testnet=True)
    captured: dict = {}

    def fake_signed_post(path: str, params: dict):
        captured["path"] = path
        captured["params"] = params
        return {"orderId": 456}

    client._signed_post = fake_signed_post  # type: ignore[method-assign]

    client._qty_step_size["ETHUSDT"] = 0.001

    client.place_market_order(
        symbol="ETHUSDT",
        side="SELL",
        quantity=0.054082,
    )

    assert captured["path"] == "/fapi/v1/order"
    assert captured["params"]["quantity"] == "0.054"
