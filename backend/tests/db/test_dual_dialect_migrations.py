"""`Base.metadata.create_all()` must produce a complete, valid schema on BOTH SQLite and
Postgres — this project has no Alembic migration chain; `create_all()` (see `db.base.
ensure_schema`) is the sole schema-provisioning mechanism, so this is what turns the
dual-dialect portability rule from an aspirational comment into a CI-enforced guarantee.

Every partial (`WHERE`-qualified) index/constraint in `db/models.py` is expressed via
`Index(..., sqlite_where=..., postgresql_where=...)` — the one piece of DDL genuinely at risk of
working on one dialect and silently failing (or silently not existing) on the other, which is
exactly what this test exists to catch. The SQLite half always runs; the Postgres half runs only
when `TEST_POSTGRES_URL` is set (the CI job sets it via a Postgres service container) and skips
— never fails — otherwise, so a local `uv run pytest` with no database around still passes
exactly as before.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import inspect

from db.base import Base, ensure_schema, make_engine

pytestmark = pytest.mark.dual_dialect


@pytest.fixture(params=["sqlite", "postgresql"])
async def database_url(request: pytest.FixtureRequest) -> AsyncIterator[str]:
    dialect = request.param
    if dialect == "sqlite":
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # noqa: SIM115 — closed below
        tmp.close()
        db_path = Path(tmp.name)
        try:
            yield f"sqlite+aiosqlite:///{db_path.as_posix()}"
        finally:
            db_path.unlink(missing_ok=True)  # noqa: ASYNC240 — local temp-file cleanup, negligible
        return

    pg_url = os.environ.get("TEST_POSTGRES_URL")
    if not pg_url:
        pytest.skip("TEST_POSTGRES_URL not set — Postgres half of the dual-dialect check skipped")
    engine = make_engine(pg_url)
    try:
        # A possibly-reused Postgres (CI runs on a fresh container, but a local run against a
        # persistent DB must not inherit a prior run's tables) — drop everything first so
        # `create_all` below is exercised against a genuinely clean database, not a no-op.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        yield pg_url
    finally:
        await engine.dispose()


async def test_create_all_produces_every_table_and_partial_index(database_url: str) -> None:
    """`ensure_schema` succeeds (no dialect-incompatible DDL) and every declared table — plus
    every partial index, the dialect-specific DDL most likely to silently diverge — exists
    afterward."""
    engine = make_engine(database_url)
    try:
        await ensure_schema(engine)

        async with engine.connect() as conn:
            table_names, index_names = await conn.run_sync(_inspect_tables_and_indexes)

        expected_tables = set(Base.metadata.tables)
        assert expected_tables <= table_names, expected_tables - table_names

        # The 7 partial indexes declared with `sqlite_where`/`postgresql_where` in db/models.py —
        # the ones a plain `create_all()` could silently drop if the dialect-specific kwarg were
        # ever malformed for one dialect but not the other.
        expected_partial_indexes = {
            "uq_turns_one_running_per_thread",
            "uq_watchlists_user_market_class_name",
            "uq_users_email",
            "uq_user_pets_one_active_per_user",
            "ix_schedule_jobs_due",
            "ix_alert_rules_scan",
            "ix_notifications_unread",
        }
        assert expected_partial_indexes <= index_names, expected_partial_indexes - index_names
    finally:
        await engine.dispose()


async def test_create_all_is_idempotent(database_url: str) -> None:
    """A second `ensure_schema` call against an already-provisioned database is a safe no-op —
    the property every composition root's startup path relies on (multiple replicas racing to
    call it, or a test fixture calling it after the app's own lifespan already did)."""
    engine = make_engine(database_url)
    try:
        await ensure_schema(engine)
        await ensure_schema(engine)  # must not raise
    finally:
        await engine.dispose()


def _inspect_tables_and_indexes(sync_conn: object) -> tuple[set[str], set[str]]:
    inspector = inspect(sync_conn)
    table_names = set(inspector.get_table_names())
    index_names: set[str] = set()
    for table_name in table_names:
        index_names.update(
            index["name"] for index in inspector.get_indexes(table_name) if index["name"]
        )
        # SQLite's own reflection reports a unique CHECK-less `UniqueConstraint` separately from
        # `get_indexes` on some versions — merge both so a partial-unique built as either an
        # index or a constraint is still found by name.
        index_names.update(
            uc["name"] for uc in inspector.get_unique_constraints(table_name) if uc.get("name")
        )
    return table_names, index_names


async def test_chat_search_uses_the_trigram_index_on_postgres(database_url: str) -> None:
    """The trigram index exists, the store's search statement has the exact shape it serves, and
    Postgres plans that shape through the index rather than scanning every message."""
    if not database_url.startswith("postgresql"):
        pytest.skip("trigram search index is Postgres-only")
    from sqlalchemy import event, text  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa: PLC0415

    from conversation.store import SqlAlchemyConversationStore  # noqa: PLC0415
    from db.postgres_search import MESSAGE_TEXT_INDEX  # noqa: PLC0415

    engine = make_engine(database_url)
    try:
        await ensure_schema(engine)
        statements: list[str] = []

        def _record(conn_, cursor, statement, parameters, context, executemany):  # noqa: PLR0913, PLR0917
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", _record)
        async with AsyncSession(engine) as session:
            await SqlAlchemyConversationStore(session).search_threads(user_id="u", query="x")
        search = next(sql for sql in statements if "messages.content" in sql)
        # The index is on lower(CAST(content AS VARCHAR)) WHERE type = 'text'; the planner can only
        # use it if the statement spells both out (a bound `type = $1` hides the predicate).
        assert "lower(CAST(messages.content AS VARCHAR))" in search
        assert "messages.type = 'text'" in search

        async with engine.connect() as conn:
            await conn.exec_driver_sql("SET enable_seqscan = off")
            plan = "\n".join(
                str(row[0])
                for row in await conn.exec_driver_sql(
                    "EXPLAIN SELECT 1 FROM messages WHERE type = 'text' "
                    "AND lower(CAST(content AS VARCHAR)) LIKE '%台積電%'"
                )
            )
            present = (
                await conn.execute(
                    text("SELECT 1 FROM pg_indexes WHERE indexname = :name"),
                    {"name": MESSAGE_TEXT_INDEX},
                )
            ).scalar()
        assert present == 1
        assert MESSAGE_TEXT_INDEX in plan, plan
    finally:
        await engine.dispose()


async def test_concurrent_startups_do_not_race_on_an_empty_database(database_url: str) -> None:
    """Compose starts api, scheduler, ingest and mcp together, each calling `ensure_schema`. On an
    empty Postgres, an unguarded `create_all` can fail with 'relation already exists'; the schema
    lock serialises them, so every caller finishes cleanly."""
    if not database_url.startswith("postgresql"):
        pytest.skip("the startup race and its advisory lock are Postgres-only")
    import asyncio  # noqa: PLC0415

    engines = [make_engine(database_url) for _ in range(4)]
    try:
        await asyncio.gather(*(ensure_schema(engine) for engine in engines))
    finally:
        for engine in engines:
            await engine.dispose()
