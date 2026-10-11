"""Per-caller request-rate guards for routes that were previously unbounded.

Chat turns and voice already have their own limits. Everything else (the public market data
routes, which can trigger a multi-second whole-market computation, and the sign-in endpoints,
which are a brute-force / token-churn target) had none. A guard is a FastAPI dependency: a signed-in
caller is counted by user id, an anonymous one by client address. The caps are abuse protection,
set well above what the web app itself sends, not a product feature, so they are the same for
every tier."""

from __future__ import annotations

import ipaddress

import structlog
from fastapi import Depends, Request

from accounts.models import User
from api.dependencies import AppState, get_state, optional_user
from api.errors import ApiError

logger = structlog.get_logger(__name__)

_WINDOW_SECONDS = 60
READ_SIGNED_IN_PER_MINUTE = 600
READ_ANONYMOUS_PER_MINUTE = 240
AUTH_PER_MINUTE = 30


def client_address(request: Request, *, trusted_proxy_count: int) -> str:
    """The caller's address. Behind `trusted_proxy_count` reverse proxies every request arrives
    from the nearest one, and each proxy appends the address it saw to `X-Forwarded-For`; so the
    caller is the entry that many hops from the RIGHT. Anything to its left was supplied by the
    client and is ignored, otherwise a client could rotate a forged value to dodge the limit.
    With no trusted proxy the header is ignored altogether."""
    if trusted_proxy_count > 0:
        # A request may carry several header lines; a proxy appends to the combined list, so
        # count over all of them (reading only the first line would let a client pad it).
        combined = ",".join(request.headers.getlist("x-forwarded-for"))
        hops = [h.strip() for h in combined.split(",")]
        hops = [h for h in hops if h]
        if len(hops) >= trusted_proxy_count:
            candidate = hops[-trusted_proxy_count]
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass  # not an address: fall back to the socket peer rather than key on junk
    return request.client.host if request.client else "unknown"


async def _enforce(request: Request, state: AppState, caller: str, *, per_minute: int) -> None:
    verdict = await state.rate_limiter.check(
        caller, max_requests=per_minute, window_seconds=_WINDOW_SECONDS
    )
    if verdict.allowed:
        return
    # Counted per route and caller kind (never the user id or address): a steady stream of these
    # for ordinary users means a limit is set too low. `route` is the path template, so it stays
    # low-cardinality.
    route = getattr(request.scope.get("route"), "path", request.url.path)
    logger.warning(
        "rate_limited",
        bucket=caller.split(":", 1)[0],
        caller_kind="user" if ":user:" in caller else "address",
        route=route,
        limit_per_minute=per_minute,
    )
    retry_after = max(1, int(verdict.retry_after_seconds or _WINDOW_SECONDS))
    raise ApiError(
        status_code=429,
        code="rate_limited",
        message="Too many requests. Please slow down and retry shortly.",
        retryable=True,
        headers={"Retry-After": str(retry_after)},
    )


async def guard_reads(
    request: Request,
    state: AppState = Depends(get_state),
    user: User | None = Depends(optional_user),
) -> None:
    if user is not None:
        await _enforce(request, state, f"read:user:{user.id}", per_minute=READ_SIGNED_IN_PER_MINUTE)
        return
    address = client_address(request, trusted_proxy_count=state.settings.trusted_proxy_count)
    await _enforce(request, state, f"read:ip:{address}", per_minute=READ_ANONYMOUS_PER_MINUTE)


async def guard_auth(request: Request, state: AppState = Depends(get_state)) -> None:
    address = client_address(request, trusted_proxy_count=state.settings.trusted_proxy_count)
    await _enforce(request, state, f"auth:ip:{address}", per_minute=AUTH_PER_MINUTE)


__all__ = ["guard_auth", "guard_reads"]
