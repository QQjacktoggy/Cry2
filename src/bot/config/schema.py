"""Pydantic schema validation for configuration."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class TelegramConfig(BaseModel):
    """Telegram notification configuration."""

    enabled: bool = True
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"


class PathsConfig(BaseModel):
    """File path configuration."""

    data_dir: str = "./data"
    logs_dir: str = "./logs"
    results_dir: str = "./data/backtest_results"


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = "INFO"
    format: str = "json"
    rotation: str = "daily"
    max_days: int = 7

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid}")
        return v.upper()


class ExchangeConfig(BaseModel):
    """Exchange connection configuration."""

    name: str = "binance"
    mode: str = "testnet"
    base_url: str = "https://testnet.binancefuture.com"
    ws_url: str = "wss://stream.binancefuture.com"
    api_key_env: str = "BINANCE_TESTNET_API_KEY"
    api_secret_env: str = "BINANCE_TESTNET_API_SECRET"

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        valid = {"testnet", "live"}
        if v not in valid:
            raise ValueError(f"Invalid exchange mode: {v}. Must be one of {valid}")
        return v


class ExecutionConfig(BaseModel):
    """Execution configuration."""

    executor: str = "sim"
    slippage_model: str = "fixed_bps"
    slippage_bps: float = 2.0
    slippage_bps_base: float = 1.0
    fee_rate_maker: float = 0.0002
    fee_rate_taker: float = 0.0004

    @field_validator("executor")
    @classmethod
    def validate_executor(cls, v: str) -> str:
        valid = {"sim", "live"}
        if v not in valid:
            raise ValueError(f"Invalid executor: {v}. Must be one of {valid}")
        return v


class RiskLimitsConfig(BaseModel):
    """Risk management limits configuration."""

    max_risk_per_trade_pct: float = 1.0
    max_position_value_pct: float = 20.0
    max_leverage: int = 3
    hard_max_leverage: int = 5
    daily_loss_limit_pct: float = 3.0
    daily_trade_count_limit: int = 50
    weekly_loss_limit_pct: float = 8.0
    maintenance_margin_ratio_min: float = 50.0
    circuit_breaker_bar_pct: float = 5.0
    circuit_breaker_cooldown_min: int = 30
    max_consecutive_errors: int = 5
    max_api_latency_ms: int = 3000
    ws_disconnect_timeout_sec: int = 60
    ws_kill_switch_timeout_sec: int = 300
    margin_reduce_threshold_pct: float = 50.0
    margin_reduce_amount_pct: float = 30.0


class SafetyConfig(BaseModel):
    """Live trading safety configuration."""

    require_confirmation: bool = True
    confirmation_string: str = "CONFIRM_LIVE_TRADING"
    max_position_value_usdt: float = 100.0


class TrendFilterConfig(BaseModel):
    """Trend filter sub-configuration."""

    enabled: bool = True
    ema_period: int = 200
    timeframe: str = "4h"


class VolatilityFilterConfig(BaseModel):
    """Volatility filter sub-configuration."""

    atr_percentile: int = 70


class StrategyParamsBase(BaseModel):
    """Base strategy parameters."""

    enabled: bool = True
    symbols: list[str] = Field(default_factory=list)
    timeframe: str = "4h"
    leverage: int = 2


class BotConfig(BaseModel):
    """Top-level bot configuration schema."""

    project: str = "binance-futures-bot"
    version: str = "1.0"
    environment: str = "paper"
    paths: PathsConfig = Field(default_factory=PathsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    risk_limits: RiskLimitsConfig = Field(default_factory=RiskLimitsConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    initial_capital: float = 10000.0
    strategies: dict[str, Any] = Field(default_factory=dict)
    capital_allocation: dict[str, float] = Field(default_factory=dict)
    symbols: dict[str, Any] = Field(default_factory=dict)

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        valid = {"backtest", "paper", "live"}
        if v not in valid:
            raise ValueError(f"Invalid environment: {v}. Must be one of {valid}")
        return v


def validate_config(raw_config: dict[str, Any]) -> BotConfig:
    """Validate raw config dict against schema."""
    return BotConfig(**raw_config)
