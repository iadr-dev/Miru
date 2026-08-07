"""Bounded Observation (M5K, Mode B / Level 2) — `context/observation.py`.

A **single explicit, operator/user-triggered** live-ish check. It reads from the
data-access router exactly like `canonical.py` does, but is tagged as a one-shot bounded
observation rather than a preserved canonical fact:

- **No polling, no scheduling, no loop.** `run_bounded_observation()` performs exactly one
  `DataAccessRouter.fetch()` call per requested capability, then returns — there is no
  timer, no background task, no retry loop in this module.
- **Runs without a separate approval step** — unlike a legacy
  "safety-tier" gate, there is nothing here to approve; the caller (a tool) simply invokes it.
- **Entitlement hook (not yet built — Phase 9):** where an observation draws on FinMind
  Sponsor data, the *tool* layer's `pre_tool` entitlement hook
  (`accounts/entitlements.py`, not yet built) is what will gate it — this module does not
  and must not implement any gating itself. The attachment point is `ObservationRun.result.
  data_class`, already carried through from the router's `SourceResult`; a future
  entitlement hook inspects that field before the tool result reaches the caller.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, TLSPolicy
from schemas.market import Symbol
from schemas.source import SourceResult


class ObservationRun(BaseModel):
    """One bounded, auditable observation run for one `(capability, symbol)` (M5K).

    Immutable — a run record is written once, matching the "auditable observation-run
    record" requirement. It is *not* a canonical fact: it never
    feeds `CanonicalPackage` directly; a caller that wants to preserve it does so by
    including its `result` in a fresh canonical build.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: Capability
    symbol: Symbol
    requested_at: datetime
    result: SourceResult
    bounded_note: str = Field(
        default=(
            "Bounded Observation (M5K) — a single explicit, operator-triggered check, "
            "not a stream or a poll."
        ),
        min_length=1,
    )


async def run_bounded_observation(
    router: DataAccessRouter,
    capability: Capability,
    symbol: Symbol,
    *,
    tls_policy: TLSPolicy = TLSPolicy.STRICT,
    **params: object,
) -> ObservationRun:
    """Perform exactly one bounded, explicit live-ish check via the router and return it.

    Called once per invocation — the caller (a tool, itself triggered once per user/operator
    request) owns "once per request"; this function contains no loop, timer, or retry-with-
    backoff of its own, by design — no auto-polling is permitted at this layer.
    """
    requested_at = datetime.now(UTC)
    result = await router.fetch(capability, symbol, tls_policy=tls_policy, **params)
    return ObservationRun(
        capability=capability,
        symbol=symbol,
        requested_at=requested_at,
        result=result,
    )


__all__ = ["ObservationRun", "run_bounded_observation"]
