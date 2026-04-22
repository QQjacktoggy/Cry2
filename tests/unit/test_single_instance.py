"""Tests for bot.runtime.single_instance."""

from __future__ import annotations

import multiprocessing
import sys
import time
from pathlib import Path

import pytest

from bot.runtime.single_instance import SingleInstance, SingleInstanceError


def _hold_lock(lock_path: str, hold_sec: float, result_queue) -> None:
    try:
        with SingleInstance(lock_path):
            result_queue.put("acquired")
            time.sleep(hold_sec)
    except Exception as e:  # pragma: no cover - diagnostic
        result_queue.put(f"err:{e}")


def test_lock_acquired_and_released(tmp_path: Path) -> None:
    lock = tmp_path / "run.lock"
    with SingleInstance(lock):
        assert lock.exists()
    # Re-acquire after release should work.
    with SingleInstance(lock):
        pass


@pytest.mark.skipif(sys.platform == "win32", reason="multiprocessing flock test is POSIX-only")
def test_second_process_blocked(tmp_path: Path) -> None:
    lock = tmp_path / "run.lock"
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=_hold_lock, args=(str(lock), 1.0, q))
    proc.start()
    try:
        # Wait until the first process acquires.
        assert q.get(timeout=5) == "acquired"
        # Second attempt should raise.
        with pytest.raises(SingleInstanceError), SingleInstance(lock):
            pass
    finally:
        proc.join(timeout=5)
