"""Every Alembic migration must apply — and roll back — cleanly on BOTH SQLite and Postgres.

This is what turns the dual-dialect portability rule from an aspirational comment into a CI-enforced
guarantee: the same revision chain is driven up (base -> head, one step at a time so a failure names
the exact revision) and back down (head -> base) against each dialect. The SQLite half always runs;
the Postgres half runs only when `TEST_POSTGRES_URL` is set (the CI job sets it via a Postgres
service container) and skips — never fails — otherwise, so a local `uv run pytest` with no database
around still passes exactly as before.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from db.base import make_engine

pytestmark = pytest.mark.dual_dialect

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"


def _config(database_url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "src" / "db" / "migrations"))
    # env.py reads the URL from get_settings().database_url, which reads DATABASE_URL from the
    # environment fresh each call — so setting it here steers both the online engine and get_url().
    os.environ["DATABASE_URL"] = database_url
    return cfg


def _ordered_revisions(cfg: Config) -> list[str]:
    script = ScriptDirectory.from_config(cfg)
    # walk_revisions yields head -> base; reverse to base -> head for a stepwise climb.
    return [rev.revision for rev in reversed(list(script.walk_revisions()))]


def _current_revision(database_url: str) -> str | None:
    """Read which revision the DB is stamped at, over the same async driver the app uses (aiosqlite
    / asyncpg) — so the test needs no extra sync driver dependency for either dialect."""

    async def _read() -> str | None:
        engine = make_engine(database_url)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(
                    lambda sync_conn: MigrationContext.configure(sync_conn).get_current_revision()
                )
        finally:
            await engine.dispose()

    return asyncio.run(_read())


@pytest.fixture(params=["sqlite", "postgresql"])
def database_url(request: pytest.FixtureRequest) -> Iterator[str]:
    dialect = request.param
    if dialect == "sqlite":
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # noqa: SIM115 — closed below
        tmp.close()
        db_path = Path(tmp.name)
        try:
            yield f"sqlite+aiosqlite:///{db_path.as_posix()}"
        finally:
            db_path.unlink(missing_ok=True)
        return

    pg_url = os.environ.get("TEST_POSTGRES_URL")
    if not pg_url:
        pytest.skip("TEST_POSTGRES_URL not set — Postgres half of the dual-dialect check skipped")
    # Ensure a clean starting point on a possibly-reused Postgres (CI runs on a fresh container, but
    # a local run against a persistent DB must not inherit a half-applied chain).
    cfg = _config(pg_url)
    downgrade(cfg, "base")
    yield pg_url


def test_migrations_apply_stepwise(database_url: str) -> None:
    """Each revision applies cleanly on this dialect, one step at a time, reaching exactly it."""
    cfg = _config(database_url)
    revisions = _ordered_revisions(cfg)
    assert revisions, "expected at least one migration to exist"

    for revision in revisions:
        upgrade(cfg, revision)
        assert _current_revision(database_url) == revision

    # Restore this connection's URL context in case env vars were mutated by read-back.
    os.environ["DATABASE_URL"] = database_url


def test_migrations_roll_back_to_base(database_url: str) -> None:
    """The full chain rolls back cleanly (apply + rollback is the expand-contract safety net)."""
    cfg = _config(database_url)
    upgrade(cfg, "head")
    revisions = _ordered_revisions(cfg)
    assert _current_revision(database_url) == revisions[-1]

    downgrade(cfg, "base")
    assert _current_revision(database_url) is None
