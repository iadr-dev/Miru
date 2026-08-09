"""Resolves an MCP caller to a `tools.base.ToolContext` — the same entitlement carrier the
internal agent loop builds for every tool dispatch.

No accounts/entitlements system exists yet (a future phase's job), so the only rule this module
can enforce today is the safe default: no credential -> anonymous -> `Tier.FREE`. The optional
API-key path is deliberately a plugged-in seam (a `PrincipalResolver` protocol), not a lookup
this module performs itself — exactly the same shape as `ToolContext.tier` already being passed
in rather than queried internally (see `tools/base.py`). A future accounts module supplies its
own resolver function; this module never talks to a database, cache, or session store.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import structlog

from schemas.enums import Tier
from tools.base import ToolContext

logger = structlog.get_logger(__name__)

_API_KEY_HEADER = "x-api-key"


@runtime_checkable
class PrincipalResolver(Protocol):
    """The seam a future per-user accounts/entitlements lookup plugs into.

    Given the raw API-key credential string a caller presented, return the `(user_id, tier)`
    it resolves to, or `None` if the key is unknown/revoked (the caller then falls back to the
    anonymous default rather than erroring — an unrecognized key never elevates and never hard
    -fails the whole request). Implementations may be async in a future phase's real lookup;
    this module treats the resolver call as awaitable so that seam pre-exists.
    """

    async def resolve(self, api_key: str) -> tuple[str, Tier] | None: ...


class AnonymousOnlyResolver:
    """The default resolver — every API key is unknown, so every caller gets the anonymous,
    `Tier.FREE` context. This is the safe, no-lookup-required behavior this module ships with
    until a real accounts-backed resolver is injected."""

    async def resolve(self, api_key: str) -> tuple[str, Tier] | None:
        del api_key  # accepted for the Protocol; this resolver recognizes no key by design
        return None


async def resolve_principal(
    headers: dict[str, str],
    *,
    resolver: PrincipalResolver | None = None,
) -> ToolContext:
    """Build the `ToolContext` for one MCP call from its HTTP headers.

    `headers` is expected pre-lowercased (FastMCP's own `get_http_headers()` already lowercases
    keys) — this function does its own defensive `.lower()` lookup regardless, so a caller
    passing a differently-cased dict still resolves correctly.

    No credential, or a credential the resolver doesn't recognize, both resolve to the same
    anonymous/`Tier.FREE` context — this function never raises on a bad/missing key; entitlement
    masking (not an auth error) is the intended failure mode for sponsor-tier tools.
    """
    api_key = headers.get(_API_KEY_HEADER) or headers.get(_API_KEY_HEADER.upper())
    if not api_key:
        return ToolContext()

    active_resolver = resolver or AnonymousOnlyResolver()
    resolved = await active_resolver.resolve(api_key)
    if resolved is None:
        logger.info("mcp_principal_unresolved_api_key")
        return ToolContext()

    user_id, tier = resolved
    return ToolContext(user_id=user_id, tier=tier)


__all__ = [
    "AnonymousOnlyResolver",
    "PrincipalResolver",
    "resolve_principal",
]
