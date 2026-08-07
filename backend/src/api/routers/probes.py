"""Infrastructure liveness/readiness probes — distinct from `/health` (which reports business
data-currency + FinMind quota, not infra state).

- `/livez` — the process is up and serving. Never touches a datastore, so a liveness check can't
  be dragged down by a slow dependency; a load balancer uses this only to decide "restart the pod".
- `/readyz` — refuses load-balancer traffic until the backing datastores this worker actually needs
  are reachable: Postgres always, and Redis only when the deployment is configured to use it
  (`event_bus_backend`/`rate_limiter_backend` = redis). A single failed dependency returns 503 with
  a per-dependency breakdown so an operator sees which one is down.
- `/startupz` — the slow-start gate: the same checks as readiness, reported under a separate path so
  an orchestrator can distinguish "still booting" from "was ready, now degraded".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy import text

from api.dependencies import AppState, get_state

router = APIRouter(tags=["ops"])


@router.get("/livez")
async def livez() -> dict[str, str]:
    return {"status": "ok"}


async def _check_postgres(state: AppState) -> bool:
    try:
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
    client: Redis = Redis.from_url(settings.redis_url)
    try:
        return bool(await client.ping())
    except Exception:
        return False
    finally:
        await client.aclose()


async def _readiness(state: AppState, response: Response) -> dict[str, object]:
    checks: dict[str, bool] = {"database": await _check_postgres(state)}
    redis_ok = await _check_redis(state)
    if redis_ok is not None:
        checks["redis"] = redis_ok
    ready = all(checks.values())
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
