"""Single-instance guard.

We hold an OS-level file lock on a pidfile for the lifetime of the
runner. A second process (e.g. an accidental simultaneous
``systemctl start`` while the previous one is still shutting down)
will fail to acquire the lock and exit instead of racing against us
for the same testnet account.

Why not check-then-write the pid? Because that has a TOCTOU race. The
file lock is held by the kernel, so a stale pidfile on crash is not a
bug: the next process will acquire the lock and proceed.

Platforms
---------
POSIX: fcntl.flock (LOCK_EX | LOCK_NB).
Windows: msvcrt.locking (LK_NBLCK).
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from types import TracebackType
from typing import IO


class SingleInstanceError(RuntimeError):
    """Raised when another bot process already holds the instance lock."""


class SingleInstance:
    """Context-manager that holds an exclusive lock on a pidfile.

    Usage::

        with SingleInstance("./data/run.lock"):
            main()
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: IO[str] | None = None

    def __enter__(self) -> SingleInstance:
        # Open (or create) the pidfile for writing. We keep it open for
        # the whole process lifetime so the kernel-level lock sticks.
        self._fh = open(self._path, "a+", encoding="utf-8")
        self._acquire()
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(str(os.getpid()))
        self._fh.flush()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._fh is None:
            return
        try:
            self._release()
        finally:
            self._fh.close()
            self._fh = None
            # Leave pidfile on disk so operators can still inspect the
            # last pid that held it; the lock is what matters.

    # ── platform-specific ────────────────────────────────────────────

    def _acquire(self) -> None:
        assert self._fh is not None
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as e:
                raise SingleInstanceError(
                    f"Another bot process is already running (lock: {self._path})"
                ) from e
        else:
            import fcntl

            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                raise SingleInstanceError(
                    f"Another bot process is already running (lock: {self._path})"
                ) from e

    def _release(self) -> None:
        assert self._fh is not None
        if sys.platform == "win32":
            import msvcrt

            with contextlib.suppress(OSError):
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            with contextlib.suppress(OSError):
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
