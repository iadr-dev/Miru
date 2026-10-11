"""Per-caller request-rate guards for routes that were previously unbounded.

Chat turns and voice already have their own limits. Everything else (the public market data
routes, which can trigger a multi-second whole-market computation, and the sign-in endpoints,
which are a brute-force / token-churn target) had none. A guard is a FastAPI dependency: a signed-in
caller is counted by user id, an anonymous one by client address. The caps are abuse protection,
set well above what the web app itself sends, not a product feature, so they are the same for
every tier."""

from __future__ import annotations

from fastapi import Depends, Request

from accounts.models import User
from api.dependencies import AppState, get_state, optional_user
from api.errors import ApiError

_WINDOW_SECONDS = 60
READ_SIGNED_IN_PER_MINUTE = 600
READ_ANONYMOUS_PER_MINUTE = 240
AUTH_PER_MINUTE = 30


def client_address(request: Request, *, trust_forwarded_for: bool) -> str:
    """The caller's address. Behind a reverse proxy every request arrives from the proxy, so the
    first `X-Forwarded-For` hop is used - but only when the deployment says the header is set by
    its own proxy (otherwise any client could spoof its way out of the limit)."""
    if trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


async def _enforce(state: AppState, caller: str, *, per_minute: int) -> None:
    verdict = await state.rate_limiter.check(
        caller, max_requests=per_minute, window_seconds=_WINDOW_SECONDS
    )
    if verdict.allowed:
        return
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
        await _enforce(state, f"read:user:{user.id}", per_minute=READ_SIGNED_IN_PER_MINUTE)
        return
    address = client_address(request, trust_forwarded_for=state.settings.trust_forwarded_for)
    await _enforce(state, f"read:ip:{address}", per_minute=READ_ANONYMOUS_PER_MINUTE)


async def guard_auth(request: Request, state: AppState = Depends(get_state)) -> None:
    address = client_address(request, trust_forwarded_for=state.settings.trust_forwarded_for)
    await _enforce(state, f"auth:ip:{address}", per_minute=AUTH_PER_MINUTE)


__all__ = ["guard_auth", "guard_reads"]
