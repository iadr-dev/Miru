"""A cross-process file lock that works on Windows (dev) and POSIX (production).

The lock is held on an open file handle, not on the file's existence, so the operating system
releases it when the process exits or crashes: there is no stale lock file to clean up and no
timeout to guess. Two `FileLock` objects on the same path conflict even inside one process, which
is what lets the tests exercise it without spawning anything.

Used for two things: the per-partition write lock in `ParquetStore` (held for milliseconds while a
new version is swapped in), and the single-writer lease of `FileLockLeader` (held for the life of
the ingest process on a machine that has no Postgres to take an advisory lock from)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import TracebackType

_POLL_SECONDS = 0.05
DEFAULT_TIMEOUT_SECONDS = 120.0


class LockTimeoutError(TimeoutError):
    """The lock stayed held by someone else for the whole wait."""


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class FileLock:
    """An exclusive lock on `path`. Re-entrancy is not supported: acquiring twice raises."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def try_acquire(self) -> bool:
        """Take the lock if it is free right now. Never waits."""
        if self._fd is not None:
            raise RuntimeError(f"{self._path} is already locked by this object")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        if _try_lock(fd):
            self._fd = fd
            return True
        os.close(fd)
        return False

    def acquire(self, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        """Wait up to `timeout` seconds for the lock; raise `LockTimeoutError` if it never frees."""
        deadline = time.monotonic() + timeout
        while not self.try_acquire():
            if time.monotonic() >= deadline:
                raise LockTimeoutError(f"could not lock {self._path} within {timeout:.0f}s")
            time.sleep(_POLL_SECONDS)

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "FileLock", "LockTimeoutError"]
