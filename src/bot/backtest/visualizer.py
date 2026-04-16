"""Visualization for backtest results.

Generates: equity curve, drawdown curve, monthly heatmap.
Uses matplotlib for static plots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


class BacktestVisualizer:
    """Generates backtest result visualizations."""

    def plot_equity_curve(
        self,
        equity_curve: list[tuple[int, float]],
        output_path: str | None = None,
    ) -> Any:
        """Plot equity curve.

        Args:
            equity_curve: List of (timestamp_ms, equity) tuples.
            output_path: Path to save the plot. If None, returns figure.

        Returns:
            matplotlib Figure or None if saved to file.
        """
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            logger.warning("matplotlib_not_available")
            return None

        if not equity_curve:
            return None

        timestamps, values = zip(*equity_curve)
        dates = pd.to_datetime(list(timestamps), unit="ms")

        fig, ax = plt.subplots(figsize=(14, 6))
        ax.plot(dates, values, linewidth=1.5, color="#2196F3")
        ax.fill_between(dates, values, alpha=0.1, color="#2196F3")
        ax.set_title("Equity Curve", fontsize=14, fontweight="bold")
        ax.set_xlabel("Date")
        ax.set_ylabel("Equity (USDT)")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()

        if output_path:
            fig.savefig(output_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            logger.info("equity_curve_saved", path=output_path)
            return None
        return fig

    def plot_drawdown(
        self,
        equity_curve: list[tuple[int, float]],
        output_path: str | None = None,
    ) -> Any:
        """Plot drawdown curve."""
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            return None

        if not equity_curve:
            return None

        timestamps, values = zip(*equity_curve)
        dates = pd.to_datetime(list(timestamps), unit="ms")
        equity = pd.Series(values, index=dates)

        peak = equity.cummax()
        drawdown_pct = (equity - peak) / peak * 100

        fig, ax = plt.subplots(figsize=(14, 4))
        ax.fill_between(drawdown_pct.index, drawdown_pct.values, 0, alpha=0.4, color="#f44336")
        ax.plot(drawdown_pct.index, drawdown_pct.values, linewidth=1, color="#f44336")
        ax.set_title("Drawdown", fontsize=14, fontweight="bold")
        ax.set_xlabel("Date")
        ax.set_ylabel("Drawdown (%)")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()

        if output_path:
            fig.savefig(output_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            return None
        return fig

    def plot_monthly_heatmap(
        self,
        equity_curve: list[tuple[int, float]],
        output_path: str | None = None,
    ) -> Any:
        """Plot monthly returns heatmap."""
        try:
            import matplotlib.pyplot as plt
            import matplotlib.colors as mcolors
        except ImportError:
            return None

        if not equity_curve or len(equity_curve) < 2:
            return None

        timestamps, values = zip(*equity_curve)
        equity = pd.Series(values, index=pd.to_datetime(list(timestamps), unit="ms"))

        # Resample to monthly returns
        monthly = equity.resample("ME").last().pct_change().dropna() * 100

        if monthly.empty:
            return None

        # Create year x month matrix
        monthly_df = pd.DataFrame({
            "year": monthly.index.year,
            "month": monthly.index.month,
            "return": monthly.values,
        })
        pivot = monthly_df.pivot_table(values="return", index="year", columns="month", aggfunc="sum")

        fig, ax = plt.subplots(figsize=(12, max(4, len(pivot) * 0.8)))

        # Color map: red for negative, green for positive
        cmap = plt.cm.RdYlGn
        norm = mcolors.TwoSlopeNorm(vmin=pivot.min().min(), vcenter=0, vmax=max(pivot.max().max(), 1))

        im = ax.imshow(pivot.values, cmap=cmap, norm=norm, aspect="auto")

        # Labels
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        ax.set_xticks(range(12))
        ax.set_xticklabels([month_names[c - 1] for c in pivot.columns])
        ax.set_yticks(range(len(pivot)))
        ax.set_yticklabels(pivot.index)

        # Add text values
        for i in range(len(pivot)):
            for j in range(len(pivot.columns)):
                val = pivot.iloc[i, j]
                if not np.isnan(val):
                    ax.text(j, i, f"{val:.1f}%", ha="center", va="center", fontsize=9)

        ax.set_title("Monthly Returns (%)", fontsize=14, fontweight="bold")
        plt.colorbar(im, ax=ax, label="Return (%)")
        plt.tight_layout()

        if output_path:
            fig.savefig(output_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            return None
        return fig

    def save_all(
        self,
        equity_curve: list[tuple[int, float]],
        output_dir: str,
    ) -> None:
        """Save all visualizations to output directory."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        self.plot_equity_curve(equity_curve, str(out / "equity_curve.png"))
        self.plot_drawdown(equity_curve, str(out / "drawdown.png"))
        self.plot_monthly_heatmap(equity_curve, str(out / "monthly_heatmap.png"))
