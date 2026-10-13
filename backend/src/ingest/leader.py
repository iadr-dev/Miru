"""Leader election: exactly one process at a time may write the derived store or run the
scheduler, however many replicas, deploy overlaps or hand-run CLIs are alive.

`LeaderElection.is_leader()` is the seam every write path already asks. It is cheap and never
blocks; the real work happens in the lease implementations below, which a process starts with
`acquire_leadership()` and keeps alive with `refresh_leadership()` once per loop pass:

* `PostgresAdvisoryLeader` - a session-level `pg_try_advisory_lock` on a dedicated connection. The
  database drops the lock the moment the connection dies, so a crashed leader frees the role
  without any timeout. This is the one that works across hosts.
* `FileLockLeader` - an OS file lock on the data root, for a machine with no Postgres (SQLite
  dev). The OS drops it when the process exits.
* `AlwaysLeader` - the in-process test double and the explicit "single process, no coordination"
  choice. It is never what production wiring returns.

`build_leader_election` picks the right one from the database URL. A standby replica simply keeps
calling `refresh_leadership()`: it takes over within one poll interval after the leader goes away.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, runtime_checkable

import structlog
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from ingest.filelock import FileLock

logger = structlog.get_logger(__name__)


class LeaderElection(Protocol):
    """Answers "is this process currently the leader?" The sole writer of the derived Parquet
    store (and the sole scheduler) must hold leadership for the duration of its work."""

    def is_leader(self) -> bool:
        """True if this process may write. Never blocks; a `False` result means "skip this
        write, someone else / nothing owns it right now" - never a hard failure."""
        ...


@runtime_checkable
class ManagedLeader(LeaderElection, Protocol):
    """A leader that has to be started, kept alive and released: the real leases."""

    async def acquire(self) -> bool: ...

    async def refresh(self) -> bool: ...

    async def release(self) -> None: ...


class AlwaysLeader:
    """Single process, no coordination: always the leader. For tests and for an explicit
    in-process setup. **Not safe with two or more writers** - production wiring uses
    `build_leader_election`, which returns a real lease."""

    def is_leader(self) -> bool:
        return True


class NeverLeader:
    """Test/standby double: always reports non-leadership, so callers can assert their write path
    correctly no-ops when leadership is not held."""

    def is_leader(self) -> bool:
        return False


def lease_key(role: str, scope: str) -> int:
    """A stable signed 64-bit key for `pg_try_advisory_lock`, derived from what is being led
    (`role`, e.g. "ingest-writer") and where (`scope`, e.g. the data root). Every process of the
    same deployment computes the same number; a different data root gets a different lease."""
    digest = hashlib.sha256(f"miru:{role}:{scope}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class FileLockLeader:
    """Leadership as an OS file lock held for the life of the process."""

    def __init__(self, path: Path | str, *, name: str) -> None:
        self._lock = FileLock(path)
        self._name = name

    def is_leader(self) -> bool:
        return self._lock.held

    async def acquire(self) -> bool:
        if self._lock.held:
            return True
        got = self._lock.try_acquire()
        if got:
            logger.info("leadership_acquired", role=self._name, kind="file_lock")
        return got

    async def refresh(self) -> bool:
        return await self.acquire()

    async def release(self) -> None:
        if self._lock.held:
            self._lock.release()
            logger.info("leadership_released", role=self._name, kind="file_lock")


class PostgresAdvisoryLeader:
    """Leadership as a session-level Postgres advisory lock on a dedicated connection.

    The connection is outside the application pool (a `NullPool` engine with autocommit), so the
    lease never competes with requests for a pooled connection and never sits "idle in
    transaction". If the connection breaks, Postgres has already released the lock; `refresh`
    notices on its next ping, reports non-leadership, and tries to take the lease again."""

    def __init__(self, database_url: str, *, key: int, name: str) -> None:
        self._url = database_url
        self._key = key
        self._name = name
        self._engine: AsyncEngine | None = None
        self._conn: AsyncConnection | None = None
        self._held = False

    def is_leader(self) -> bool:
        return self._held

    async def _connection(self) -> AsyncConnection:
        if self._conn is None:
            self._engine = create_async_engine(
                self._url, poolclass=NullPool, isolation_level="AUTOCOMMIT"
            )
            self._conn = await self._engine.connect()
        return self._conn

    async def _drop_connection(self) -> None:
        conn, engine, self._conn, self._engine = self._conn, self._engine, None, None
        self._held = False
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                logger.warning("leadership_connection_close_failed", role=self._name)
        if engine is not None:
            await engine.dispose()

    async def acquire(self) -> bool:
        if self._held:
            return True
        try:
            conn = await self._connection()
            result = await conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": self._key}
            )
            self._held = bool(result.scalar())
        except Exception:
            logger.warning("leadership_acquire_failed", role=self._name, exc_info=True)
            await self._drop_connection()
            return False
        if self._held:
            logger.info("leadership_acquired", role=self._name, kind="pg_advisory")
        return self._held

    async def refresh(self) -> bool:
        if self._held:
            try:
                conn = await self._connection()
                await conn.execute(text("SELECT 1"))
                return True
            except Exception:
                logger.warning("leadership_lost", role=self._name, exc_info=True)
                await self._drop_connection()
        return await self.acquire()

    async def release(self) -> None:
        was_held = self._held
        if self._conn is not None and was_held:
            try:
                await self._conn.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": self._key}
                )
            except Exception:
                logger.warning("leadership_unlock_failed", role=self._name)
        await self._drop_connection()
        if was_held:
            logger.info("leadership_released", role=self._name, kind="pg_advisory")


def build_leader_election(*, role: str, database_url: str, scope_root: Path | str) -> ManagedLeader:
    """The real lease for this deployment: a Postgres advisory lock when the app database is
    Postgres (works across hosts), otherwise an OS file lock under `scope_root`. `scope_root` is
    the data root being protected, so two deployments that share a database do not share a lease."""
    scope = str(Path(scope_root).resolve())
    if make_url(database_url).get_backend_name() == "postgresql":
        return PostgresAdvisoryLeader(database_url, key=lease_key(role, scope), name=role)
    return FileLockLeader(Path(scope_root) / f".{role}.lease", name=role)


async def acquire_leadership(leader: LeaderElection) -> bool:
    """Start a managed lease; a plain `LeaderElection` (a test double) is taken as it is."""
    if isinstance(leader, ManagedLeader):
        return await leader.acquire()
    return leader.is_leader()


async def refresh_leadership(leader: LeaderElection) -> bool:
    """Once per loop pass: keep a held lease alive, or let a standby try to take it over."""
    if isinstance(leader, ManagedLeader):
        return await leader.refresh()
    return leader.is_leader()


async def release_leadership(leader: LeaderElection) -> None:
    if isinstance(leader, ManagedLeader):
        await leader.release()


def get_default_leader_election() -> LeaderElection:
    """Single process, no coordination: tests and the explicit in-process choice. Production
    wiring uses `build_leader_election` instead."""
    return AlwaysLeader()


__all__ = [
    "AlwaysLeader",
    "FileLockLeader",
    "LeaderElection",
    "ManagedLeader",
    "NeverLeader",
    "PostgresAdvisoryLeader",
    "acquire_leadership",
    "build_leader_election",
    "get_default_leader_election",
    "lease_key",
    "refresh_leadership",
    "release_leadership",
]
