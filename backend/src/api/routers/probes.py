"""Infrastructure liveness/readiness probes — distinct from `/health` (which reports business
data-currency + FinMind quota, not infra state).

- `/livez` — the process is up and serving. Never touches a datastore, so a liveness check can't
  be dragged down by a slow dependency; a load balancer uses this only to decide "restart the pod".
- `/readyz` — refuses load-balancer traffic until Postgres is reachable (503). Redis, when the
  deployment uses it (`event_bus_backend`/`rate_limiter_backend` = redis), is reported in the
  per-dependency breakdown but does not gate readiness: every replica shares it, and the request
  path degrades without it.
- `/startupz` — the slow-start gate: the same checks as readiness, reported under a separate path so
  an orchestrator can distinguish "still booting" from "was ready, now degraded".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from api.dependencies import AppState, get_state
from netclient.redis_clients import request_path_redis

router = APIRouter(tags=["ops"])


@router.get("/livez")
async def livez() -> dict[str, str]:
    return {"status": "ok"}


_PROBE_TIMEOUT_SECONDS = 3
_probe_engines: dict[str, AsyncEngine] = {}


def _probe_engine(url: str) -> AsyncEngine:
    """A tiny engine used only by the probes: no pool, a short connect and statement timeout.

    The probes must keep answering when the application pool is exhausted - that is precisely when
    the load balancer needs to see a healthy-but-busy replica, not a dead one. Sharing the pool
    made a saturated replica fail readiness, get ejected, and push its load onto the others."""
    engine = _probe_engines.get(url)
    if engine is None:
        engine = create_async_engine(
            url,
            poolclass=NullPool,
            connect_args={
                "timeout": _PROBE_TIMEOUT_SECONDS,
                "server_settings": {"statement_timeout": str(_PROBE_TIMEOUT_SECONDS * 1000)},
            },
        )
        _probe_engines[url] = engine
    return engine


async def _check_postgres(state: AppState) -> bool:
    url = state.settings.database_url
    try:
        if url.startswith("postgresql"):
            async with _probe_engine(url).connect() as conn:
                await conn.execute(text("SELECT 1"))
        else:  # SQLite (dev/tests): there is no pool pressure to isolate from
            async with state.session_factory() as session:
                await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _check_redis(state: AppState) -> bool | None:
    """Ping Redis when the deployment relies on it; `None` means "not applicable here" so a
    single-process (in-memory backend) worker never fails readiness on a Redis it doesn't use."""
    settings = state.settings
    if settings.event_bus_backend != "redis" and settings.rate_limiter_backend != "redis":
        return None
    client: Redis = request_path_redis(settings)
    try:
        return bool(await client.ping())
    except Exception:
        return False
    finally:
        await client.aclose()


async def _readiness(state: AppState, response: Response) -> dict[str, object]:
    # Only the database gates readiness. Redis is reported, but every replica shares the one
    # Redis, so failing readiness on it took every replica out of the load balancer at once and
    # made a Redis outage a full outage; the request path now degrades instead (local rate limit,
    # cache misses go to the source, live quotes pause).
    database = await _check_postgres(state)
    checks: dict[str, bool] = {"database": database}
    redis_ok = await _check_redis(state)
    if redis_ok is not None:
        checks["redis"] = redis_ok
    ready = database
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "checks": checks}


@router.get("/readyz")
async def readyz(response: Response, state: AppState = Depends(get_state)) -> dict[str, object]:
    return await _readiness(state, response)


@router.get("/startupz")
async def startupz(response: Response, state: AppState = Depends(get_state)) -> dict[str, object]:
    return await _readiness(state, response)


__all__ = ["router"]
