"""CatalogManager: Auto-generates DATA_CATALOG.md from DataStore contents.

Scans the data store and produces a formatted markdown catalog file
documenting all available datasets, their date ranges, and validation status.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from backtest_tool.data_manager.store import DataStore
from backtest_tool.data_manager.validator import DataValidator, ValidationResult

logger = structlog.get_logger(__name__)


class CatalogManager:
    """Generates and updates DATA_CATALOG.md."""

    def __init__(self, store: DataStore) -> None:
        """Initialize with a DataStore.

        Args:
            store: DataStore instance to catalog.
        """
        self.store = store
        self.catalog_path = store.data_dir / "DATA_CATALOG.md"

    def update(self) -> None:
        """Scan DataStore and regenerate DATA_CATALOG.md."""
        available = self.store.list_available()
        validator = DataValidator()
        validation_results = validator.validate_all(self.store)

        # Build validation lookup
        val_map: dict[str, ValidationResult] = {}
        for vr in validation_results:
            key = f"{vr.symbol}_{vr.timeframe}"
            val_map[key] = vr

        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines: list[str] = []

        lines.append("# 📋 Data Catalog — 回測資料目錄\n")
        lines.append(f"> 自動生成，請勿手動編輯。最後更新：{now}\n")

        # Klines section
        lines.append("## K 線資料\n")
        if available["klines"]:
            lines.append("| Symbol | Timeframe | 起始日期 | 結束日期 | 筆數 | 檔案大小 | 缺漏率 | 狀態 |")
            lines.append("|--------|-----------|----------|----------|------|----------|--------|------|")
            for k in available["klines"]:
                key = f"{k['symbol']}_{k['timeframe']}"
                vr = val_map.get(key)
                missing_rate = f"{vr.missing_rate:.2%}" if vr else "N/A"
                status = "✅" if (vr and vr.passed) else "❌" if vr else "⚠️"
                lines.append(
                    f"| {k['symbol']} | {k['timeframe']} | {k['start']} | {k['end']} "
                    f"| {k['rows']:,} | {k['size_mb']:.1f} MB | {missing_rate} | {status} |"
                )
        else:
            lines.append("*尚無 K 線資料*\n")

        lines.append("")

        # Funding section
        lines.append("## 資金費率資料\n")
        if available["funding"]:
            lines.append("| Symbol | 起始日期 | 結束日期 | 筆數 | 檔案大小 | 狀態 |")
            lines.append("|--------|----------|----------|------|----------|------|")
            for f in available["funding"]:
                key = f"{f['symbol']}_8h"
                vr = val_map.get(key)
                status = "✅" if (vr and vr.passed) else "❌" if vr else "⚠️"
                lines.append(
                    f"| {f['symbol']} | {f['start']} | {f['end']} "
                    f"| {f['rows']:,} | {f['size_mb']:.1f} MB | {status} |"
                )
        else:
            lines.append("*尚無資金費率資料*\n")

        lines.append("")

        # Validation summary
        lines.append("## 資料校驗摘要\n")
        total = len(validation_results)
        passed = sum(1 for vr in validation_results if vr.passed)
        high_missing = sum(1 for vr in validation_results if vr.missing_rate > 0.001)
        has_anomaly = sum(1 for vr in validation_results if vr.anomalous_rows > 0)

        if total > 0:
            lines.append(f"- ✅ 通過校驗：{passed} / {total} 資料集")
            lines.append(f"- ⚠️ 缺漏 bar > 0.1%：{high_missing} / {total}")
            lines.append(f"- ⚠️ 異常值偵測：{has_anomaly} / {total}")
        else:
            lines.append("*尚無資料可校驗*")

        # Write catalog
        content = "\n".join(lines) + "\n"
        self.catalog_path.write_text(content, encoding="utf-8")
        logger.info("DATA_CATALOG.md updated", path=str(self.catalog_path), datasets=total)
