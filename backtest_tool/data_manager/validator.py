"""DataValidator: Integrity checks for kline and funding data.

Validates timestamp monotonicity, OHLC consistency, gap detection,
and anomalous values.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# Expected bar intervals in milliseconds
TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


@dataclass
class ValidationResult:
    """Result of a data validation check."""

    symbol: str
    timeframe: str
    total_rows: int = 0
    passed: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_bars: int = 0
    missing_rate: float = 0.0
    anomalous_rows: int = 0


class DataValidator:
    """Validates kline and funding rate data integrity."""

    def __init__(self, gap_tolerance: float = 3.0) -> None:
        """Initialize validator.

        Args:
            gap_tolerance: Max allowed gap as multiple of expected interval.
                           Gaps > tolerance * expected_interval are flagged.
        """
        self.gap_tolerance = gap_tolerance

    def validate_klines(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
    ) -> ValidationResult:
        """Validate kline DataFrame integrity.

        Args:
            df: DataFrame with kline data (must have 'timestamp' column).
            symbol: Symbol name for reporting.
            timeframe: Timeframe string (e.g., '4h').

        Returns:
            ValidationResult with all findings.
        """
        result = ValidationResult(symbol=symbol, timeframe=timeframe)

        if df.empty:
            result.warnings.append("Empty DataFrame")
            return result

        result.total_rows = len(df)

        # 1. Timestamp monotonicity
        ts = df["timestamp"].values
        diffs = np.diff(ts)
        non_increasing = int(np.sum(diffs <= 0))
        if non_increasing > 0:
            result.errors.append(f"Timestamp not monotonically increasing: {non_increasing} violations")
            result.passed = False

        # 2. OHLC validity: prices > 0
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                invalid = int((df[col] <= 0).sum())
                if invalid > 0:
                    result.errors.append(f"{col} has {invalid} non-positive values")
                    result.passed = False

        # 3. OHLC consistency: high >= max(open, close), low <= min(open, close)
        if all(c in df.columns for c in ["open", "high", "low", "close"]):
            max_oc = df[["open", "close"]].max(axis=1)
            min_oc = df[["open", "close"]].min(axis=1)

            high_violations = int((df["high"] < max_oc - 1e-10).sum())
            low_violations = int((df["low"] > min_oc + 1e-10).sum())

            if high_violations > 0:
                result.warnings.append(f"High < max(open, close): {high_violations} rows")
            if low_violations > 0:
                result.warnings.append(f"Low > min(open, close): {low_violations} rows")

        # 4. Volume >= 0
        if "volume" in df.columns:
            neg_vol = int((df["volume"] < 0).sum())
            if neg_vol > 0:
                result.errors.append(f"Negative volume: {neg_vol} rows")
                result.passed = False

        # 5. Gap detection
        if timeframe in TIMEFRAME_MS and len(ts) > 1:
            expected_interval = TIMEFRAME_MS[timeframe]
            max_gap = expected_interval * self.gap_tolerance
            gaps = diffs[diffs > max_gap]
            result.missing_bars = len(gaps)

            expected_total = int((ts[-1] - ts[0]) / expected_interval) + 1
            if expected_total > 0:
                result.missing_rate = max(0.0, 1.0 - len(ts) / expected_total)

            if result.missing_bars > 0:
                result.warnings.append(
                    f"Detected {result.missing_bars} gaps > {self.gap_tolerance}x expected interval"
                )

        # 6. Anomalous values (extreme price changes)
        if "close" in df.columns and len(df) > 1:
            pct_change = df["close"].pct_change().abs()
            extreme = int((pct_change > 0.5).sum())  # >50% single-bar move
            result.anomalous_rows = extreme
            if extreme > 0:
                result.warnings.append(f"Extreme price changes (>50%): {extreme} bars")

        return result

    def validate_funding(
        self,
        df: pd.DataFrame,
        symbol: str,
    ) -> ValidationResult:
        """Validate funding rate DataFrame integrity.

        Args:
            df: DataFrame with funding rate data.
            symbol: Symbol name for reporting.

        Returns:
            ValidationResult with all findings.
        """
        result = ValidationResult(symbol=symbol, timeframe="8h")

        if df.empty:
            result.warnings.append("Empty DataFrame")
            return result

        result.total_rows = len(df)

        # Timestamp monotonicity
        ts = df["timestamp"].values
        diffs = np.diff(ts)
        non_increasing = int(np.sum(diffs <= 0))
        if non_increasing > 0:
            result.errors.append(f"Timestamp not monotonically increasing: {non_increasing} violations")
            result.passed = False

        # Funding rate range check (typical: -0.01 to 0.01 per 8h)
        if "funding_rate" in df.columns:
            extreme = int((df["funding_rate"].abs() > 0.05).sum())
            if extreme > 0:
                result.warnings.append(f"Extreme funding rates (|rate| > 5%): {extreme} entries")

        # Gap detection (8h = 28_800_000 ms)
        if len(ts) > 1:
            expected_interval = TIMEFRAME_MS["8h"]
            max_gap = expected_interval * self.gap_tolerance
            gaps = diffs[diffs > max_gap]
            result.missing_bars = len(gaps)

            expected_total = int((ts[-1] - ts[0]) / expected_interval) + 1
            if expected_total > 0:
                result.missing_rate = max(0.0, 1.0 - len(ts) / expected_total)

            if result.missing_bars > 0:
                result.warnings.append(
                    f"Detected {result.missing_bars} gaps > {self.gap_tolerance}x expected interval"
                )

        return result

    def validate_all(self, store: DataStore) -> list[ValidationResult]:
        """Validate all datasets in the store.

        Args:
            store: DataStore instance to validate.

        Returns:
            List of ValidationResult for each dataset.
        """

        results: list[ValidationResult] = []
        available = store.list_available()

        for kline_info in available["klines"]:
            symbol = kline_info["symbol"]
            tf = kline_info["timeframe"]
            try:
                df = store.load_klines(symbol, tf)
                vr = self.validate_klines(df, symbol, tf)
                results.append(vr)
            except Exception as e:
                vr = ValidationResult(symbol=symbol, timeframe=tf, passed=False)
                vr.errors.append(f"Failed to load: {e}")
                results.append(vr)

        for fund_info in available["funding"]:
            symbol = fund_info["symbol"]
            try:
                df = store.load_funding(symbol)
                vr = self.validate_funding(df, symbol)
                results.append(vr)
            except Exception as e:
                vr = ValidationResult(symbol=symbol, timeframe="8h", passed=False)
                vr.errors.append(f"Failed to load: {e}")
                results.append(vr)

        return results
