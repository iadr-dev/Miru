"""Root pytest config shared by the whole suite.

Windows-only: force the selector event loop policy at import time (before any test/fixture runs).
The default `ProactorEventLoop` on Windows runs a `aiosqlite` connection's cross-thread future
completion (its worker thread calling `Future.get_loop().call_soon_threadsafe(...)` back into the
loop) into a native access violation under this suite's concurrency pattern (a request-scoped
session concurrently used by a background `asyncio.create_task`, as `api.turn_driver.
run_and_stream` does) — reproduced outside FastAPI entirely, tied to the Proactor loop
specifically, and gone under the selector loop.

This must be a bare `asyncio.set_event_loop_policy(...)` at collection time, not a
`pytest_asyncio`-fixture override: `starlette.testclient.TestClient` drives every request through
its own `anyio` blocking portal on a dedicated background thread with its own fresh event loop —
outside pytest-asyncio's fixture-managed loop entirely — so only the process-global policy (read
by that thread when it creates its loop) actually reaches it.

Prod runs in Docker/Linux, where this has no effect; this is purely a Windows dev/CI test-runner
fix."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# `magika` (a transitive dependency of the optional `markitdown` extra, pulled in the moment any
# test imports `attachments.extractor`) calls `dotenv.load_dotenv(dotenv.find_dotenv())`
# unconditionally at its own module-import time — with no scoping, this walks up from the CWD,
# finds this repo's real `backend/.env`, and loads every real secret in it (API keys, DB
# credentials, `ENV=production`, ...) into the actual OS-level `os.environ` for the rest of the
# test process. That's a real leak, not just a test-pollution nuisance: once loaded, os.environ
# stays polluted for every later test in the same run (e.g. `ingest.backfill_cli`'s
# `_check_not_production` reads `ENV=production` from a completely unrelated test's
# `Settings(_env_file=None, ...)`, since `_env_file=None` only disables the file source — real
# env vars still take priority). Patched here, at collection time, before any test module can
# import `markitdown`/`magika` transitively — `dotenv.find_dotenv` returning "" makes magika's own
# `load_dotenv(find_dotenv())` call a no-op ("" means "no file found").
import dotenv
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker


def _no_dotenv_file(*_args: object, **_kwargs: object) -> str:
    return ""


dotenv.find_dotenv = _no_dotenv_file


@pytest.fixture
async def pg_session_factory() -> AsyncIterator[async_sessionmaker[object]]:
    """A session factory over a REAL Postgres (every table created, dropped afterwards) for the
    concurrency tests: SQLite has one writer, so a race is latent there. Skips (never fails) when
    `TEST_POSTGRES_URL` is unset; the CI concurrency leg sets it against a service container."""
    pg_url = os.environ.get("TEST_POSTGRES_URL")
    if not pg_url:
        pytest.skip("TEST_POSTGRES_URL not set - Postgres concurrency check skipped")
    from db.base import Base, make_engine, make_session_factory  # noqa: PLC0415 - keep import light

    engine = make_engine(pg_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield make_session_factory(engine)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture(autouse=True)
def _synthetic_reference_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every loader of the private reference data at the synthetic fixture directory, so no
    test depends on (or can leak) the product's own research kept in `REFERENCE_DATA_DIR`."""
    from pathlib import Path  # noqa: PLC0415 - keep the module import list light

    from config import private_data  # noqa: PLC0415

    fixture_dir = Path(__file__).resolve().parent / "fixtures" / "reference_data"
    monkeypatch.setattr(private_data, "reference_dir", lambda: fixture_dir)
