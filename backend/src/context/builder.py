"""Multi-source builder (M8A/B/C) — `context/builder.py`.

The main entry point other layers (later: `tools/`) call to get a governed context package
for one `(symbol, market)`. Orchestrates: for each requested capability, call the
data-access router (never a source directly), merge the results into a `CanonicalPackage`,
and compute a `SourceHealthReport` alongside — never full-market, always one explicit
`(symbol, market)` (or a small explicit set) per call.

- **M8A** — official EOD merge (TWSE/TPEX/TAIFEX OpenAPI + FinMind history): primary,
  `price_eod` et al. resolved via the router's normal priority-fallback.
- **M8B** — TAIFEX derivatives context: the `taifex_openapi` adapter is real and configured
  for `derivatives_oi` (`config/source_priority.yaml`, wired in `dataaccess/composition.py`) —
  but it is **not** in the default capability set. `derivatives_oi` is only fetched when the
  caller explicitly opts in via `BuildOptions.include_derivatives=True`; a default build never
  requests it. (A capability with no configured adapter for a market still degrades to the
  router's own `unavailable` `SourceResult` rather than raising, surfaced in
  `SourceHealthReport` — that fallback exists independently of whether an adapter happens to be
  configured.)
- **M8C** — `TAIFEX_MIS` live-ish + real-time snapshots: **staged behind the
  `enable_taifex_mis_validation` flag** — not included in a default build until the caller
  explicitly opts in; live-ish/real-time data stays behind a validation flag until verified.

This module performs no I/O of its own — the router does the fetching.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from context.canonical import CanonicalPackage, build_canonical_package
from context.source_health import SourceHealthReport, build_source_health_report
from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, TLSPolicy
from schemas.market import Symbol
from schemas.source import SourceResult

logger = structlog.get_logger(__name__)

# The default capability set for a "give me context on this symbol" build (M8A). Deliberately
# excludes derivatives/live-ish capabilities — those are M8B/M8C, opt-in below.
_DEFAULT_CAPABILITIES: tuple[Capability, ...] = (
    Capability.PRICE_EOD,
    Capability.FINANCIALS,
    Capability.DIVIDENDS,
    Capability.INSTITUTIONAL_FLOW,
    Capability.ANALYST_CONSENSUS,
    Capability.SECTOR_CLASS,
)

# M8B — TAIFEX derivatives context. `sources/taifex_openapi.py` is a real, configured adapter
# for `derivatives_oi` — this is an opt-in gate, not a missing-adapter workaround: a default
# build never requests derivatives, so `include_derivatives=True` is required regardless of
# adapter availability. Requesting this capability with no adapter configured for a given
# market is still safe (the router degrades to an `unavailable` SourceResult rather than
# raising — dataaccess/router.py's per-adapter unavailable-tier health), independent of this
# gate.
_DERIVATIVES_CAPABILITIES: tuple[Capability, ...] = (Capability.DERIVATIVES_OI,)

# M8C — TAIFEX_MIS live-ish/real-time snapshots. Staged behind a validation flag —
# `PRICE_INTRADAY` is only fetched when the caller explicitly opts in via
# `enable_taifex_mis_validation=True`, regardless of
# whether a MIS adapter is configured. This is a builder-level gate, independent of (and in
# addition to) the entitlement gate that will apply to any Sponsor-tier serving adapter.
_LIVE_ISH_CAPABILITIES: tuple[Capability, ...] = (Capability.PRICE_INTRADAY,)


@dataclass(frozen=True)
class BuildOptions:
    """Opt-in switches for the multi-source builder — everything defaults to the safe/minimal
    M8A-only build; M8B/M8C are explicit opt-ins."""

    include_derivatives: bool = False  # M8B
    enable_taifex_mis_validation: bool = False  # M8C — validation-flag gate
    extra_capabilities: tuple[Capability, ...] = field(default_factory=tuple)


async def build_canonical_context(
    router: DataAccessRouter,
    symbol: Symbol,
    *,
    options: BuildOptions | None = None,
    tls_policy: TLSPolicy = TLSPolicy.STRICT,
) -> tuple[CanonicalPackage, SourceHealthReport]:
    """Fetch every requested capability for one `(symbol, market)` and build M5F + M5Q.

    Never a full-market scan: exactly one `Symbol`, and only the capabilities on the
    (bounded, explicit) request list. A capability with no configured/available adapter for
    this market yields the router's `unavailable` result rather than raising — it shows up
    in the returned `SourceHealthReport`, not as a crash.
    """
    opts = options or BuildOptions()
    capabilities = list(_DEFAULT_CAPABILITIES)
    if opts.include_derivatives:
        capabilities.extend(_DERIVATIVES_CAPABILITIES)
    if opts.enable_taifex_mis_validation:
        capabilities.extend(_LIVE_ISH_CAPABILITIES)
    capabilities.extend(opts.extra_capabilities)

    results: dict[Capability, SourceResult] = {}
    for cap in capabilities:
        result = await router.fetch(cap, symbol, tls_policy=tls_policy)
        results[cap] = result
        logger.info(
            "context_builder_fetched",
            capability=cap.value,
            symbol=symbol.id,
            market=symbol.market.value,
            source_id=result.source_id,
            health=result.health.status.value,
        )

    canonical = build_canonical_package(symbol, results)
    health = build_source_health_report(symbol, results)
    return canonical, health


__all__ = [
    "BuildOptions",
    "build_canonical_context",
]
