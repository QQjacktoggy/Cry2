"""YAML configuration loader with includes and environment variable substitution."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
import structlog

from bot.core.exceptions import ConfigError

logger = structlog.get_logger(__name__)

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")


def _substitute_env_vars(value: str) -> str:
    """Replace ${VAR} or ${VAR:default} patterns with environment variable values."""

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        default = match.group(2)
        env_value = os.environ.get(var_name, default)
        if env_value is None:
            raise ConfigError(f"Environment variable '{var_name}' not set and no default provided")
        return env_value

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _process_values(data: Any) -> Any:
    """Recursively process config values, substituting environment variables."""
    if isinstance(data, str):
        return _substitute_env_vars(data)
    elif isinstance(data, dict):
        return {k: _process_values(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_process_values(item) for item in data]
    return data


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a single YAML file."""
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if data is not None else {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Error parsing YAML file {path}: {e}") from e


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries. Override values take precedence."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def load_config(
    config_path: str | Path = "config/config.yaml",
    environment: str | None = None,
) -> dict[str, Any]:
    """Load and merge all configuration files.

    Args:
        config_path: Path to main config.yaml.
        environment: Override environment (backtest/paper/live).

    Returns:
        Merged configuration dictionary.
    """
    config_path = Path(config_path)
    config_dir = config_path.parent

    logger.info("loading_config", path=str(config_path))

    # Load main config
    config = _load_yaml_file(config_path)

    # Override environment if specified
    if environment:
        config["environment"] = environment

    current_env = config.get("environment", "paper")

    # Process includes
    includes = config.pop("includes", [])
    for include_pattern in includes:
        include_path = _substitute_env_vars(
            include_pattern.replace("${environment}", current_env)
        )
        full_path = config_dir / include_path
        if full_path.exists():
            include_data = _load_yaml_file(full_path)
            config = _merge_dicts(config, include_data)
            logger.debug("config_include_loaded", path=str(full_path))
        else:
            logger.warning("config_include_not_found", path=str(full_path))

    # Substitute environment variables in values
    config = _process_values(config)

    logger.info("config_loaded", environment=current_env, keys=list(config.keys()))
    return config
