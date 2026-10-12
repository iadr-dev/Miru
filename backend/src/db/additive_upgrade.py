"""Additive, data-preserving schema upgrade for databases created by an older version of the models.

`create_all` builds whatever is missing but never touches a table that already exists, so a column,
index or unique constraint added to a model later never reaches a database that already has the
table. This closes that gap *without* a migration chain: at startup it compares the live schema to
`Base.metadata` and **adds** what is missing. It never drops, renames or rewrites anything, and on a
database that is already current it does nothing.

What it covers: missing columns, missing indexes, missing *named* unique constraints.
What it deliberately does not: dropping columns the models no longer have, changing a column's type
or a CHECK constraint's definition, foreign keys on newly added columns, backfilling data. Those are
destructive or data-dependent and stay a deliberate manual step.

A new NOT NULL column is only added as NOT NULL when the model gives it a server default (otherwise
existing rows could not satisfy it); without one it is added nullable.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import Column, MetaData, Table, UniqueConstraint, inspect
from sqlalchemy.engine import Connection, Dialect
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateIndex, DefaultClause

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _Step:
    description: str
    sql: str
    is_column: bool


def _server_default_sql(column: Column[object], dialect: Dialect) -> str | None:
    default = column.server_default
    if not isinstance(default, DefaultClause):
        return None
    arg = default.arg
    if isinstance(arg, str):
        escaped = arg.replace("'", "''")
        return f"'{escaped}'"
    return str(arg.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))


def _add_column_sql(table: Table, column: Column[object], dialect: Dialect) -> str:
    quote = dialect.identifier_preparer.quote
    if_not_exists = "IF NOT EXISTS " if dialect.name == "postgresql" else ""
    sql = (
        f"ALTER TABLE {quote(table.name)} ADD COLUMN {if_not_exists}{quote(column.name)} "
        f"{column.type.compile(dialect=dialect)}"
    )
    default = _server_default_sql(column, dialect)
    if default is not None:
        sql += f" DEFAULT {default}"
        if not column.nullable:
            sql += " NOT NULL"
    return sql


def _unique_index_sql(name: str, table: Table, columns: list[str], dialect: Dialect) -> str:
    quote = dialect.identifier_preparer.quote
    cols = ", ".join(quote(c) for c in columns)
    return f"CREATE UNIQUE INDEX IF NOT EXISTS {quote(name)} ON {quote(table.name)} ({cols})"


def _add_unique_sql(name: str, constraint: UniqueConstraint, table: Table, dialect: Dialect) -> str:
    columns = [c.name for c in constraint.columns]
    if dialect.name == "sqlite":  # SQLite cannot ADD CONSTRAINT; a unique index is equivalent
        return _unique_index_sql(name, table, columns, dialect)
    quote = dialect.identifier_preparer.quote
    cols = ", ".join(quote(c) for c in columns)
    return f"ALTER TABLE {quote(table.name)} ADD CONSTRAINT {quote(name)} UNIQUE ({cols})"


def _plan(conn: Connection, metadata: MetaData) -> list[_Step]:
    """Everything the live schema is missing relative to `metadata`, in a safe order."""
    inspector = inspect(conn)
    dialect = conn.dialect
    live_tables = set(inspector.get_table_names())
    steps: list[_Step] = []
    for table in metadata.sorted_tables:
        if table.name not in live_tables:
            continue  # `create_all` builds whole missing tables
        live_columns = {c["name"] for c in inspector.get_columns(table.name)}
        live_index_names = {i["name"] for i in inspector.get_indexes(table.name)}
        live_unique_names = {u["name"] for u in inspector.get_unique_constraints(table.name)}
        for column in table.columns:
            if column.name in live_columns:
                continue
            steps.append(
                _Step(
                    f"column {table.name}.{column.name}",
                    _add_column_sql(table, column, dialect),
                    is_column=True,
                )
            )
            if column.unique:
                name = f"uq_{table.name}_{column.name}"
                steps.append(
                    _Step(
                        f"unique {table.name}.{name}",
                        _unique_index_sql(name, table, [column.name], dialect),
                        is_column=False,
                    )
                )
        for index in table.indexes:
            if index.name is None or index.name in live_index_names:
                continue
            steps.append(
                _Step(
                    f"index {table.name}.{index.name}",
                    str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect)),
                    is_column=False,
                )
            )
        for constraint in table.constraints:
            if not isinstance(constraint, UniqueConstraint):
                continue
            constraint_name = constraint.name
            if not isinstance(constraint_name, str):
                continue
            if constraint_name in live_unique_names or constraint_name in live_index_names:
                continue
            steps.append(
                _Step(
                    f"unique {table.name}.{constraint_name}",
                    _add_unique_sql(constraint_name, constraint, table, dialect),
                    is_column=False,
                )
            )
    # Columns first: indexes and unique keys may reference a column added in the same pass.
    return sorted(steps, key=lambda step: not step.is_column)


async def pending_additive_steps(engine: AsyncEngine, metadata: MetaData) -> list[str]:
    """What `apply_additive_upgrade` would add, without changing anything - how strict mode tells
    that an existing database is behind the models."""
    async with engine.connect() as conn:
        steps = await conn.run_sync(_plan, metadata)
    return [step.description for step in steps]


async def apply_additive_upgrade(engine: AsyncEngine, metadata: MetaData) -> list[str]:
    """Add whatever `metadata` has that the live schema lacks; returns what was applied.

    Each step runs in its own transaction, so one failure cannot roll back the others, and a step
    another process already applied (replicas start together) is recognised and skipped. A failed
    column add raises — the models would be unusable without it. A failed index/unique add (for
    example a unique key that existing duplicate rows violate) is logged loudly and skipped so the
    service still starts."""
    async with engine.connect() as conn:
        steps = await conn.run_sync(_plan, metadata)
    applied: list[str] = []
    for step in steps:
        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql(step.sql)
        except DBAPIError as exc:
            async with engine.connect() as conn:
                remaining = {s.description for s in await conn.run_sync(_plan, metadata)}
            if step.description not in remaining:
                continue  # another process got there first
            if step.is_column:
                raise
            logger.error(
                "schema_upgrade_step_failed", step=step.description, error=type(exc).__name__
            )
            continue
        applied.append(step.description)
        logger.warning("schema_upgraded", step=step.description)
    return applied


__all__ = ["apply_additive_upgrade", "pending_additive_steps"]
