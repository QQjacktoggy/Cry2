#!/usr/bin/env python3
"""Run live trading. Requires explicit confirmation."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bot.config.env import load_env
from bot.config.loader import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run live trading")
    parser.add_argument("--config", default="config/config.yaml", help="Config file")
    parser.add_argument("--confirm", help="Confirmation string")
    args = parser.parse_args()

    load_env()
    config = load_config(args.config, environment="live")

    safety = config.get("safety", {})
    required_string = safety.get("confirmation_string", "CONFIRM_LIVE_TRADING")

    if safety.get("require_confirmation", True) and args.confirm != required_string:
        print("=" * 60)
        print("⚠️  LIVE TRADING MODE")
        print("=" * 60)
        print("This will trade with REAL MONEY on Binance Mainnet.")
        print(f"To confirm, run with: --confirm {required_string}")
        print("=" * 60)
        sys.exit(1)

    print("🚨 Live trading mode confirmed. Starting...")
    print("TODO: Implement live trading loop (same as paper but with live config)")
    # The actual implementation would be identical to run_paper.py
    # but with live environment config loaded


if __name__ == "__main__":
    main()
