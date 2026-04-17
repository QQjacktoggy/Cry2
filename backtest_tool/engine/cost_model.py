"""Cost model: Fee and slippage calculations for VBT backtests.

Matches the parent project's FeeModel and SlippageModel for consistency.
Provides helper functions to compute effective fee rates for VBT.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
import yaml

logger = structlog.get_logger(__name__)


@dataclass
class CostModel:
    """Fee and slippage model for backtest cost simulation.

    Attributes:
        maker_rate: Maker fee rate (e.g., 0.0002 = 0.02%).
        taker_rate: Taker fee rate (e.g., 0.0004 = 0.04%).
        default_type: Default fee type ('maker' or 'taker').
        slippage_bps: Slippage in basis points (e.g., 2.0 = 0.02%).
    """

    maker_rate: float = 0.0002
    taker_rate: float = 0.0004
    default_type: str = "taker"
    slippage_bps: float = 2.0

    @classmethod
    def from_config(cls, config: dict) -> CostModel:
        """Create CostModel from backtest_config.yaml dict.

        Args:
            config: Parsed YAML config dict (the 'backtest' section).

        Returns:
            CostModel instance.
        """
        fees = config.get("fees", {})
        slippage = config.get("slippage", {})

        return cls(
            maker_rate=fees.get("maker_rate", 0.0002),
            taker_rate=fees.get("taker_rate", 0.0004),
            default_type=fees.get("default_type", "taker"),
            slippage_bps=slippage.get("fixed_bps", 2.0),
        )

    @classmethod
    def from_yaml(cls, path: str) -> CostModel:
        """Load CostModel from a YAML config file.

        Args:
            path: Path to backtest_config.yaml.

        Returns:
            CostModel instance.
        """
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return cls.from_config(config.get("backtest", {}))

    @property
    def default_fee_rate(self) -> float:
        """Get the default fee rate based on default_type."""
        if self.default_type == "maker":
            return self.maker_rate
        return self.taker_rate

    @property
    def slippage_rate(self) -> float:
        """Convert slippage BPS to decimal rate."""
        return self.slippage_bps / 10000.0

    @property
    def total_cost_rate(self) -> float:
        """Total per-trade cost rate (fee + slippage) for VBT.

        VBT's `fees` parameter combines all per-trade costs.
        We combine the fee rate and slippage rate into one value.
        """
        return self.default_fee_rate + self.slippage_rate

    def summary(self) -> dict[str, float]:
        """Return a summary dict of all cost components.

        Returns:
            Dict with fee and slippage details.
        """
        return {
            "maker_rate": self.maker_rate,
            "taker_rate": self.taker_rate,
            "default_type": self.default_type,
            "default_fee_rate": self.default_fee_rate,
            "slippage_bps": self.slippage_bps,
            "slippage_rate": self.slippage_rate,
            "total_cost_rate": self.total_cost_rate,
        }
