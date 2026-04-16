"""Walk-Forward analysis.

Rolls through time: train on N months, test on M months, repeat.
Default: 6 months train, 1 month test.
"""

from __future__ import annotations

from typing import Any, Callable

import structlog

from bot.utils.time_utils import parse_date, datetime_to_ms

logger = structlog.get_logger(__name__)


class WalkForwardAnalyzer:
    """Walk-Forward optimization and validation."""

    def __init__(
        self,
        train_months: int = 6,
        test_months: int = 1,
    ) -> None:
        self.train_months = train_months
        self.test_months = test_months

    def generate_windows(
        self,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Generate train/test time windows.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).

        Returns:
            List of windows with train_start, train_end, test_start, test_end.
        """
        from datetime import timedelta

        start = parse_date(start_date)
        end = parse_date(end_date)

        windows = []
        current = start

        while True:
            train_start = current
            train_end_approx = train_start + timedelta(days=self.train_months * 30)
            test_start = train_end_approx
            test_end = test_start + timedelta(days=self.test_months * 30)

            if test_end > end:
                break

            windows.append({
                "window_id": len(windows) + 1,
                "train_start_ms": datetime_to_ms(train_start),
                "train_end_ms": datetime_to_ms(train_end_approx),
                "test_start_ms": datetime_to_ms(test_start),
                "test_end_ms": datetime_to_ms(test_end),
                "train_start": train_start.strftime("%Y-%m-%d"),
                "train_end": train_end_approx.strftime("%Y-%m-%d"),
                "test_start": test_start.strftime("%Y-%m-%d"),
                "test_end": test_end.strftime("%Y-%m-%d"),
            })

            current = test_start  # slide forward

        logger.info("walk_forward_windows", count=len(windows))
        return windows

    def run(
        self,
        start_date: str,
        end_date: str,
        backtest_fn: Callable[..., dict[str, Any]],
        optimize_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Run walk-forward analysis.

        Args:
            start_date: Overall start date.
            end_date: Overall end date.
            backtest_fn: Function to run a backtest given start_ms, end_ms.
            optimize_fn: Optional optimization function for train period.

        Returns:
            List of per-window results.
        """
        windows = self.generate_windows(start_date, end_date)
        results = []

        for window in windows:
            logger.info(
                "walk_forward_window",
                window_id=window["window_id"],
                train=f"{window['train_start']} → {window['train_end']}",
                test=f"{window['test_start']} → {window['test_end']}",
            )

            # Optional: optimize on train period
            best_params = {}
            if optimize_fn:
                best_params = optimize_fn(
                    start_ms=window["train_start_ms"],
                    end_ms=window["train_end_ms"],
                )

            # Backtest on test period
            test_result = backtest_fn(
                start_ms=window["test_start_ms"],
                end_ms=window["test_end_ms"],
                **best_params,
            )

            results.append({
                **window,
                "optimized_params": best_params,
                "test_result": test_result,
            })

        logger.info("walk_forward_complete", windows=len(results))
        return results
