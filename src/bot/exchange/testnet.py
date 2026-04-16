"""Testnet endpoint configuration."""

from __future__ import annotations

from bot.core.constants import (
    BINANCE_FUTURES_BASE_URL,
    BINANCE_FUTURES_TESTNET_URL,
    BINANCE_FUTURES_WS_URL,
    BINANCE_FUTURES_WS_TESTNET_URL,
)


class EndpointConfig:
    """Exchange endpoint configuration for testnet/live."""

    def __init__(self, mode: str = "testnet") -> None:
        self.mode = mode

    @property
    def base_url(self) -> str:
        if self.mode == "testnet":
            return BINANCE_FUTURES_TESTNET_URL
        return BINANCE_FUTURES_BASE_URL

    @property
    def ws_url(self) -> str:
        if self.mode == "testnet":
            return BINANCE_FUTURES_WS_TESTNET_URL
        return BINANCE_FUTURES_WS_URL

    @property
    def is_testnet(self) -> bool:
        return self.mode == "testnet"
