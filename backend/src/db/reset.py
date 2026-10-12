"""Rebuild the app database from the models (development only).

    uv run python -m db.reset --yes
    docker compose run --rm --no-deps --entrypoint python api -m db.reset --yes

(The image entrypoint ignores a plain command and starts the API, so it must be overridden. Stop
the services that hold database connections first, and take a `pg_dump` if the data matters.)

Drops every table the models define and creates them again, so the schema is fresh and clean
instead of being patched with ALTER TABLE as the models change. This empties the *app* database
(users, threads, watchlists, alerts, subscriptions, derived scores). It never touches the market
data (Parquet/DuckDB under PARQUET_ROOT/DUCKDB_PATH) or Redis. Refuses to run outside development,
and without `--yes`."""

from __future__ import annotations

import argparse
import asyncio
import sys

from config import get_settings
from db.base import Base, ensure_schema, make_engine
from db.schema_lock import schema_lock


async def reset_database() -> None:
    from db import models  # noqa: F401, PLC0415  # pyright: ignore[reportUnusedImport]

    engine = make_engine()
    try:
        async with schema_lock(engine), engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await ensure_schema(engine)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--yes", action="store_true", help="confirm emptying the app database")
    args = parser.parse_args(argv)
    settings = get_settings()
    if settings.env != "development":
        print(f"Refusing to reset: ENV={settings.env!r} (only 'development').", file=sys.stderr)  # noqa: T201
        return 2
    if not args.yes:
        print("This empties the app database. Re-run with --yes to confirm.", file=sys.stderr)  # noqa: T201
        return 2
    asyncio.run(reset_database())
    print("App database rebuilt. Market data was not touched.")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
