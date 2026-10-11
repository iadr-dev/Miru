"""Context-wrapping tools — `get_canonical_context`, `build_conversation_package`,
`run_bounded_observation` (the immediate/deferred core, built against what's real).

Design note (judgment call, applied consistently across every tool in this module): these
tools wrap **multi-capability, shape-varying** builds (`context/builder.py`,
`context/conversation.py`, `context/observation.py`) where the payload under each capability
has a different, adapter-specific shape. Rather than forward `SourceResult.payload` verbatim
(never surfaced raw — governance §6), each capability's result is reduced to typed provenance
metadata + a small `payload_keys` list (top-level keys only, no values) — enough for the agent
to know *what* is available and *how current* it is without ever seeing a raw payload dict.
`build_conversation_package` is the one exception by design: it already returns the Mode-C
`ConversationFact` shape (`context/conversation.py`), which is payload-free by construction, so
this module passes it through unchanged rather than re-summarizing an already-safe artifact.
Capability-specific tools that *do* return real structured data (e.g. `tools/market/`'s
dividend-schedule tool) do so via `schemas.capabilities`' typed per-capability models instead —
a different, equally-safe path for the single-capability case.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from context.builder import BuildOptions, build_canonical_context
from context.conversation import build_conversation_package as _build_conversation_package
from context.observation import run_bounded_observation as _run_bounded_observation
from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, DataClass, TimingClass
from schemas.source import HealthStatus, SourceResult
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import SymbolInput


class CapabilitySummary(BaseModel):
    """One capability's redacted provenance summary — never the raw `payload` dict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: Capability
    source_id: str
    data_class: DataClass
    timing_class: TimingClass
    fetched_at: datetime
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    is_available: bool
    payload_keys: tuple[str, ...] = Field(default_factory=tuple)


def _summarize(capability: Capability, result: SourceResult) -> CapabilitySummary:
    return CapabilitySummary(
        capability=capability,
        source_id=result.source_id,
        data_class=result.data_class,
        timing_class=result.timing_class,
        fetched_at=result.fetched_at,
        currentness_caveat=result.currentness_caveat,
        health_status=result.health.status,
        is_available=result.health.status is HealthStatus.OK and bool(result.payload),
        payload_keys=tuple(sorted(result.payload.keys())),
    )


class GetCanonicalContextOutput(BaseModel):
    """Redacted M5F canonical summary for one `(symbol, market)`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    version: int
    built_at: datetime
    records: tuple[CapabilitySummary, ...]


class GetCanonicalContextTool(BaseTool):
    """`get_canonical_context` — wraps `context/builder.py`'s `build_canonical_context` for
    one `(symbol, market)`. Immediate core."""

    name = "get_canonical_context"
    description = (
        "Get the preserved canonical market-context package (M5F) for one stock/ETF symbol — "
        "price/financials/dividends/institutional-flow/analyst/sector data with provenance "
        "and currentness caveats, never raw payloads."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetCanonicalContextOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        symbol = typed.to_symbol()
        canonical, _health = await build_canonical_context(
            self._router, symbol, tls_policy=ctx.tls_policy
        )
        records = tuple(
            _summarize(record.capability, record.result) for record in canonical.records
        )
        return GetCanonicalContextOutput(
            symbol=canonical.symbol.id,
            market=canonical.symbol.market.value,
            version=canonical.version,
            built_at=canonical.built_at,
            records=records,
        )


class ConversationFactOut(BaseModel):
    """One Mode-C fact, passed through from `context/conversation.py` unchanged (already
    payload-free)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: Capability
    source_id: str
    data_class: DataClass
    timing_class: TimingClass
    fetched_at: datetime
    currentness_caveat: str = Field(min_length=1)
    headline: str = Field(min_length=1)
    is_available: bool


class BuildConversationPackageOutput(BaseModel):
    """M5N Conversation Package — the AI-safe caveated artifact for one symbol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    built_at: datetime
    non_trading_notice: str = Field(min_length=1)
    facts: tuple[ConversationFactOut, ...]


class BuildConversationPackageTool(BaseTool):
    """`build_conversation_package` — assembles the M5N package from a fresh canonical build
    (`context/builder.py` -> `context/conversation.py`). Immediate core."""

    name = "build_conversation_package"
    description = (
        "Assemble the AI-safe conversation package for one stock/ETF symbol: caveated, "
        "non-trading facts with no raw payloads — the artifact meant for discussion."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = BuildConversationPackageOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        symbol = typed.to_symbol()
        canonical, _health = await build_canonical_context(
            self._router, symbol, tls_policy=ctx.tls_policy
        )
        package = _build_conversation_package(symbol, canonical=canonical)
        facts = tuple(
            ConversationFactOut(
                capability=fact.capability,
                source_id=fact.source_id,
                data_class=fact.data_class,
                timing_class=fact.timing_class,
                fetched_at=fact.fetched_at,
                currentness_caveat=fact.currentness_caveat,
                headline=fact.headline,
                is_available=fact.is_available,
            )
            for fact in package.facts
        )
        return BuildConversationPackageOutput(
            symbol=package.symbol.id,
            market=package.symbol.market.value,
            built_at=package.built_at,
            non_trading_notice=package.non_trading_notice,
            facts=facts,
        )


class RunBoundedObservationInput(SymbolInput):
    """One explicit, bounded live-ish check for one `(capability, symbol)` — never a loop/poll."""

    capability: Capability


class RunBoundedObservationOutput(BaseModel):
    """M5K bounded-observation result — redacted, never the raw payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    requested_at: datetime
    bounded_note: str = Field(min_length=1)
    record: CapabilitySummary


class RunBoundedObservationTool(BaseTool):
    """`run_bounded_observation` — wraps `context/observation.py`'s single-shot check. Public,
    deferred (not part of the named immediate-core set)."""

    name = "run_bounded_observation"
    description = (
        "Perform exactly one explicit, bounded live-ish check of a single capability for one "
        "symbol — never a poll or a stream."
    )
    input_model: type[BaseModel] = RunBoundedObservationInput
    output_model: type[BaseModel] = RunBoundedObservationOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(RunBoundedObservationInput, args)
        symbol = typed.to_symbol()
        run = await _run_bounded_observation(
            self._router, typed.capability, symbol, tls_policy=ctx.tls_policy
        )
        return RunBoundedObservationOutput(
            symbol=run.symbol.id,
            market=run.symbol.market.value,
            requested_at=run.requested_at,
            bounded_note=run.bounded_note,
            record=_summarize(run.capability, run.result),
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table (the immediate-core:
    `get_canonical_context` + `build_conversation_package`; `run_bounded_observation` is a
    real, deferred capability the same layer supports)."""
    tools: list[tuple[Tool, bool]] = [
        (GetCanonicalContextTool(router), True),
        (BuildConversationPackageTool(router), True),
        (RunBoundedObservationTool(router), False),
    ]
    return tools


# `BuildOptions` is re-exported only so a future caller wiring M8B/M8C opt-ins doesn't need to
# reach past this module into `context.builder` directly for the one flag it might want to
# expose later (no tool built this phase uses non-default options).
__all__ = [
    "BuildConversationPackageOutput",
    "BuildConversationPackageTool",
    "BuildOptions",
    "CapabilitySummary",
    "GetCanonicalContextOutput",
    "GetCanonicalContextTool",
    "RunBoundedObservationInput",
    "RunBoundedObservationOutput",
    "RunBoundedObservationTool",
    "build_registration_table",
]
