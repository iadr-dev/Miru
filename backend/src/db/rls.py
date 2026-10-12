"""Postgres row-level security: a database-enforced backstop for per-user isolation.

Per-user isolation is otherwise a convention: every store adds `WHERE user_id = ?`. One query that
forgets it would read or write another user's rows. With RLS on, Postgres itself filters every
statement on a user-owned table to the rows whose `user_id` equals the session's `app.user_id`, so
a missing `WHERE` returns nothing instead of leaking.

How identity reaches the database
---------------------------------
* A request's authenticated user is held in a contextvar (`bind_user`). Each transaction a session
  begins copies it into the Postgres setting `app.user_id` (`SET LOCAL` semantics), via an
  `after_begin` hook installed once per process, so no store needs to know about RLS.
* Code that legitimately works across users (the scheduler and ingest processes, webhooks that
  arrive before any sign-in, the sign-in flow itself) runs inside `system_access()`, which sets
  `app.rls_system = 'on'`. Anything that sets neither is denied: it fails closed.

RLS is opt-in (`ROW_LEVEL_SECURITY=true`) and Postgres only; on SQLite every function here is a
no-op, so tests and local development without Postgres are unaffected. `FORCE ROW LEVEL SECURITY`
makes the table owner (the role the app connects as) subject to the policies too."""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from contextvars import ContextVar

import structlog
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from db.base import Base

logger = structlog.get_logger(__name__)

POLICY_NAME = "user_isolation"
_USER_SETTING = "app.user_id"
_SYSTEM_SETTING = "app.rls_system"

_user_id: ContextVar[str | None] = ContextVar("rls_user_id", default=None)
_system: ContextVar[bool] = ContextVar("rls_system", default=False)

_enabled = False
_hook_installed = False


def rls_tables() -> list[str]:
    """Every table that stores rows per user: the ones with a `user_id` column."""
    return sorted(table.name for table in Base.metadata.sorted_tables if "user_id" in table.c)


def current_user_id() -> str | None:
    return _user_id.get()


def bind_user(user_id: str) -> None:
    """Make `user_id` the acting user for the rest of this request (and tasks it spawns)."""
    _user_id.set(user_id)


@contextlib.contextmanager
def system_access() -> Generator[None]:
    """Work that is not on behalf of one user: background jobs, inbound webhooks, sign-in.

    Keep these blocks narrow: inside one, the database no longer enforces isolation."""
    token = _system.set(True)
    try:
        yield
    finally:
        _system.reset(token)


_IDENTITY_SQL = text(
    "SELECT set_config(:user_key, :user_value, true), set_config(:sys_key, :sys_value, true)"
)


def _identity_params() -> dict[str, str]:
    return {
        "user_key": _USER_SETTING,
        "user_value": _user_id.get() or "",
        "sys_key": _SYSTEM_SETTING,
        "sys_value": "on" if _system.get() else "off",
    }


def enter_system_mode() -> None:
    """For a whole-process entry point that only ever does cross-user work (the scheduler, the
    ingest daemon, one-off CLIs): stay in system mode until the process exits."""
    _system.set(True)


def _apply_identity(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    connection.execute(_IDENTITY_SQL, _identity_params())


def _after_begin(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    del session, transaction
    _apply_identity(connection)


def install(*, enabled: bool) -> None:
    """Switch RLS plumbing on for this process (idempotent). Called from each entry point."""
    global _enabled, _hook_installed  # noqa: PLW0603 - process-wide switch, set once at startup
    _enabled = enabled
    if enabled and not _hook_installed:
        event.listen(Session, "after_begin", _after_begin)
        _hook_installed = True


def is_enabled() -> bool:
    return _enabled


async def bind_session(session: AsyncSession) -> None:
    """Apply the current identity to the transaction that is already open on `session`.

    `after_begin` covers every transaction a session starts later; the one that loaded the user in
    the first place began before the user was known, so it needs this explicit call."""
    if not _enabled:
        return
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    await session.execute(_IDENTITY_SQL, _identity_params())


def _policy_sql(table: str) -> list[str]:
    predicate = (
        f"(user_id = current_setting('{_USER_SETTING}', true) "
        f"OR current_setting('{_SYSTEM_SETTING}', true) = 'on')"
    )
    return [
        f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
        f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
        f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{table}"',
        f'CREATE POLICY {POLICY_NAME} ON "{table}" USING {predicate} WITH CHECK {predicate}',
    ]


class RlsNotEnforcedError(RuntimeError):
    """RLS is switched on, but the database role the app connects as is exempt from it."""


async def assert_enforced(engine: AsyncEngine) -> None:
    """Fail fast if the connected role bypasses RLS.

    Postgres superusers and roles with BYPASSRLS ignore every policy, even FORCEd ones - and the
    `POSTGRES_USER` a container or managed instance is created with is a superuser. Policies that
    are silently not applied are worse than none, because they look like protection."""
    if engine.dialect.name != "postgresql":
        return
    async with engine.connect() as conn:
        row = (
            await conn.exec_driver_sql(
                "SELECT current_user, rolsuper OR rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user"
            )
        ).one()
    if row[1]:
        raise RlsNotEnforcedError(
            f"ROW_LEVEL_SECURITY is on but the database role {row[0]!r} is a superuser or has "
            "BYPASSRLS, so Postgres ignores every policy. Connect the app as an ordinary role "
            "(CREATE ROLE ... LOGIN NOSUPERUSER NOBYPASSRLS) that owns its tables."
        )


async def apply_policies(engine: AsyncEngine) -> list[str]:
    """Create (or refresh) the isolation policy on every user-owned table. Idempotent. Returns the
    tables it covered; Postgres only."""
    if engine.dialect.name != "postgresql":
        return []
    tables = rls_tables()
    try:
        async with engine.begin() as conn:
            for table in tables:
                for statement in _policy_sql(table):
                    await conn.exec_driver_sql(statement)
    except DBAPIError:
        logger.error("rls_policies_failed", exc_info=True)
        raise
    await assert_enforced(engine)
    logger.info("rls_policies_applied", tables=len(tables))
    return tables


__all__ = [
    "POLICY_NAME",
    "RlsNotEnforcedError",
    "apply_policies",
    "assert_enforced",
    "bind_session",
    "bind_user",
    "current_user_id",
    "enter_system_mode",
    "install",
    "is_enabled",
    "rls_tables",
    "system_access",
]
