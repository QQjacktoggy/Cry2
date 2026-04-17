"""Entry point for `python -m bot`."""



def main() -> None:
    """Main entry point."""
    print("binance-futures-bot v1.0.0")
    print("Use scripts/ for specific operations:")
    print("  python scripts/run_backtest.py  - Run backtest")
    print("  python scripts/run_paper.py     - Run paper trading")
    print("  python scripts/run_live.py      - Run live trading")
    print("  python scripts/run_dashboard.py - Start dashboard")


if __name__ == "__main__":
    main()
