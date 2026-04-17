"""Configuration loading and validation."""

from bot.config.env import load_env
from bot.config.loader import load_config

__all__ = ["load_config", "load_env"]
