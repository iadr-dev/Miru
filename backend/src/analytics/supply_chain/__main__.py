"""Operator CLI: `python -m analytics.supply_chain load|stats`.

`load` rebuilds the index tables from the private relation files; `stats` prints row counts
(numbers only, never relation content).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from datetime import UTC, datetime

from analytics.supply_chain.refdata_io.loader import RelationFileError, load_reference_relations
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from config.settings import get_settings
from db import (
    models,  # noqa: F401  # pyright: ignore[reportUnusedImport] - registers ORM models
    rls,
)
from db.base import ensure_schema, make_engine, make_session_factory


async def _run(command: str) -> int:
    settings = get_settings()
    rls.install(enabled=settings.row_level_security)
    rls.enter_system_mode()  # platform-wide reference data, not one user
    engine = make_engine(settings.database_url)
    try:
        await ensure_schema(engine)
        factory = make_session_factory(engine)
        async with factory() as session:
            store = SqlAlchemySupplyChainStore(session)
            if command == "load":
                try:
                    result = await load_reference_relations(store, today=datetime.now(UTC).date())
                except RelationFileError as exc:
                    sys.stderr.write(f"invalid relation file: {exc}\n")
                    return 1
                await session.commit()
                sys.stdout.write(json.dumps(result.__dict__) + "\n")
            else:
                counts = await store.counts()
                sys.stdout.write(counts.model_dump_json(indent=2) + "\n")
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analytics.supply_chain")
    parser.add_argument("command", choices=["load", "stats"])
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.command))


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(main())
