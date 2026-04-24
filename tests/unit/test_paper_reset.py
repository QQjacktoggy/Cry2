from __future__ import annotations

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from bot.runtime.paper_reset import clear_runtime_state


def test_clear_runtime_state_archives_journal_and_removes_health(tmp_path: Path) -> None:
    journal_path = tmp_path / "paper_trades.db"
    health_path = tmp_path / "health.json"
    wal_path = tmp_path / "paper_trades.db-wal"
    shm_path = tmp_path / "paper_trades.db-shm"

    journal_path.write_text("journal")
    health_path.write_text("health")
    wal_path.write_text("wal")
    shm_path.write_text("shm")

    result = clear_runtime_state(
        journal_path=journal_path,
        archive_journal=True,
        health_path=health_path,
    )

    assert result.errors == []
    assert result.archived_journal
    assert not journal_path.exists()
    assert Path(result.archived_journal).exists()
    assert result.cleared_health is True
    assert not health_path.exists()
    assert not wal_path.exists()
    assert not shm_path.exists()


def test_clear_runtime_state_is_noop_for_missing_files(tmp_path: Path) -> None:
    result = clear_runtime_state(
        journal_path=tmp_path / "missing.db",
        archive_journal=True,
        health_path=tmp_path / "missing.json",
    )

    assert result.errors == []
    assert result.archived_journal == ""
    assert result.cleared_health is False