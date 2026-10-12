"""The sole chokepoint for HTTP client construction — TLS verification is strict by default
and no code path may downgrade it silently.

Every adapter in `sources/` that needs an `httpx.AsyncClient` MUST obtain it from
`build_async_client()` here — never construct one ad-hoc (ruff's bandit rules block
`verify=False` outside this seam). A non-strict policy is always explicitly chosen by the
caller and always loudly logged.
"""

from __future__ import annotations

import structlog

from netclient import SharedClientLease, lease_client
from schemas.enums import TLSPolicy

logger = structlog.get_logger(__name__)


def build_async_client(
    *,
    tls_policy: TLSPolicy = TLSPolicy.STRICT,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
) -> SharedClientLease:
    """The pooled `httpx.AsyncClient` an adapter uses, as a lease for `async with ... as client`.

    - `strict` (default): full certificate verification. Silent, no log noise.
    - `compatibility`: relaxed verification — still explicitly requested; loudly logged.
    - `unsafe-explicit`: verification disabled — only via explicit opt-in; loudly logged.

    Never call this with a policy the caller didn't explicitly choose — no code path may
    downgrade TLS silently.
    """
    verify: bool = True
    if tls_policy is TLSPolicy.STRICT:
        verify = True
    elif tls_policy is TLSPolicy.COMPATIBILITY:
        verify = True  # relaxed cert *chain* leniency is not exposed by httpx; log the choice
        logger.warning(
            "tls_policy_non_strict",
            policy=tls_policy.value,
            detail="compatibility mode explicitly selected — verification remains on, "
            "but this is not the default and must be a deliberate, logged choice",
        )
    elif tls_policy is TLSPolicy.UNSAFE_EXPLICIT:
        verify = False
        logger.warning(
            "tls_policy_unsafe_explicit",
            policy=tls_policy.value,
            detail="TLS verification DISABLED via explicit opt-in",
        )

    # One pooled client per (loop, verify, timeout, headers); `async with` no longer closes it.
    # No status-based retries here: ingest governance is "a failed fetch journals FAILED and the
    # next scheduled run retries", and some sources are metered.
    return lease_client(verify=verify, timeout=timeout, headers=headers)
