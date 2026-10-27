"""Shared row-validation helper for the many "fetch one capability, validate its payload rows
into one `schemas.capabilities` model, return the tuple" tools added across this registry's
FinMind-coverage expansion.

This is a plain generic function, not a dynamic tool-class factory — every concrete tool in
this expansion is still its own explicitly-named `BaseTool` subclass (matching the existing
house style in `tools/market/`); this helper only spares each one from re-deriving the same
five-line `payload.get(key) -> validate each row` dance.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel

from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceResult

# Least healthy first: a bundle reports the worst status among its fetches.
_HEALTH_SEVERITY = (
    HealthStatus.UNREACHABLE,
    HealthStatus.UNAVAILABLE,
    HealthStatus.UNAVAILABLE_TIER,
    HealthStatus.STALE,
    HealthStatus.OK,
)


def worst_health(*results: SourceResult) -> HealthStatus:
    """The least healthy status among a bundled tool's fetches, so an outage in any section is
    never reported as `ok` (a non-ok value next to some rows means a section is missing)."""
    statuses = {r.health.status for r in results}
    return next((s for s in _HEALTH_SEVERITY if s in statuses), HealthStatus.OK)


def validate_rows[RowT: BaseModel](
    model: type[RowT], payload: dict[str, object], key: str
) -> tuple[RowT, ...]:
    """Validate `payload[key]` (if present and list-shaped) into a tuple of `model` rows.

    Mirrors the router's own graceful-degradation contract: a missing/non-list key is not an
    error here, it is simply "no rows" — the caller's `health_status`/`currentness_caveat`
    fields (read from the same `SourceResult`) already carry the honest reason why.
    """
    raw = payload.get(key)
    if not isinstance(raw, list):
        return ()
    rows = cast("list[dict[str, object]]", raw)
    return tuple(model.model_validate(row) for row in rows)


async def fetch_rows[RowT: BaseModel](  # noqa: PLR0913 — every kwarg is load-bearing for the
    # shared "fetch one capability, validate its rows" shape every tool in this expansion reuses;
    # splitting into a request object would only move the same argument count one level out.
    router: DataAccessRouter,
    cap: Capability,
    sym: Symbol,
    *,
    tls_policy: TLSPolicy,
    model: type[RowT],
    key: str,
    params: dict[str, object] | None = None,
) -> tuple[SourceResult, tuple[RowT, ...]]:
    """One router fetch + one `validate_rows` call — the single repeated shape behind every
    "fetch one capability, return its normalized rows" tool added across this registry's
    FinMind/gov-API coverage expansion. Returns the raw `SourceResult` too, so each tool can
    still surface its own `source_id`/`currentness_caveat`/`health_status` fields."""
    result = await router.fetch(cap, sym, tls_policy=tls_policy, **(params or {}))
    return result, validate_rows(model, result.payload, key)


__all__ = ["fetch_rows", "validate_rows", "worst_health"]
