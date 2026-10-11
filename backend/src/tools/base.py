"""The `Tool` Protocol + `ToolContext` + the entitlement gate.

Every agent action goes through a registered `Tool` — the agent never touches network/
filesystem directly. All tools are **read-only and always runnable**; there is no
safety-tier system and no confirmation/approval gate (a deliberate design choice, superseding an
earlier safety-tier design). The *only* access control is the `data_class` entitlement check
(`dispatch_tool` below), which substitutes a masked teaser for `sponsor` data instead of
running the tool — never a hard error.

Design notes (judgment calls):
- The design sketches its own `DataClass` enum, but it is byte-for-byte the same
  two-value shape (`public`/`sponsor`) as `schemas.enums.DataClass`, already built in Phase 1
  and already carried on every `SourceResult`/`CanonicalRecord`/`ConversationFact`. Reusing it
  here (rather than defining a duplicate, colliding `tools.base.DataClass`) keeps exactly one
  `data_class` vocabulary project-wide.
- `Tool` is declared as a `Protocol` (the project convention: depend on seams/Protocols, never
  a concrete implementation) — callers
  (the registry, the future agent runtime) type against this Protocol, never a concrete class.
  `BaseTool` below is a plain (non-Protocol) implementation-sharing convenience: concrete tool
  classes may subclass it to get `to_tool_spec()` for free, but nothing outside `tools/`
  should ever type a variable as `BaseTool` — always `Tool`.
- `to_tool_spec()` is the Phase 4 -> Phase 5 integration seam: it converts a `Tool`'s
  `input_model` into an `llm.base.ToolSpec` (name/description/JSON-schema) so the future agent
  runtime can hand `[t.to_spec() for t in registry.immediate_tools()]` to `LLMClient.chat`.
  `llm/` is not listed in `tests/arch/test_layering.py`'s `LAYER_ORDER` (it sits beside `tools/`
  as a leaf module the future `agent/` runtime depends on independently, per `llm/base.py`'s
  own docstring) — so `tools -> llm.base` for this one type is not a tracked/forbidden edge,
  and is the explicit integration point the phase-4 brief calls out.
- `ToolContext.journal` is a placeholder (`object | None`) for Phase 7's audit-journal handle
  (the future journal is expected to record every dispatch) — unused today,
  kept so this model's shape doesn't need to change again once the journal lands.
- **The real Phase-9 entitlement lookup plugs into `dispatch_tool` below** — see its docstring.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

import anyio.to_thread
import structlog
from pydantic import BaseModel, ConfigDict, Field

from llm.base import ToolSpec
from schemas.enums import DataClass, Tier, TLSPolicy

logger = structlog.get_logger(__name__)


def parse_args[T: BaseModel](model: type[T], args: BaseModel) -> T:
    """Re-validate an arbitrary `BaseModel` instance into a tool's own `input_model` type.

    `Tool.run(args: BaseModel, ...)` is deliberately typed against the base `BaseModel` (the
    Protocol's contract) rather than each tool's concrete input type — a concrete
    tool implementation calls this at the top of `run()` to recover its own typed model,
    without an `isinstance`/`cast` dance at every call site.
    """
    return model.model_validate(args.model_dump())


class ToolContext(BaseModel):
    """Per-call context: caller identity/entitlement, the active
    TLS policy, and a journal-handle slot.

    `tier` stands in for the not-yet-built `accounts/entitlements` per-user lookup (Phase 9):
    the future runtime resolves the authenticated (or anonymous -> `Tier.FREE`) caller's tier
    and passes it here. **This module never queries a database or session itself.**
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str | None = None
    tier: Tier = Tier.FREE
    tls_policy: TLSPolicy = TLSPolicy.STRICT
    journal: object | None = None  # Phase 7 audit-journal handle — not wired yet


@runtime_checkable
class Tool(Protocol):
    """The agent's action-surface contract.

    `input_model`/`output_model` double as the MCP structured input/output schema. `run()` is
    async end-to-end and must never touch `sources/`/the network directly — only via `context/`
    or `dataaccess/`.
    """

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    data_class: DataClass
    card_type: str | None
    supports_row_preview: bool

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        """Execute the tool. Always runnable — no approval/confirmation gate."""
        ...

    def to_tool_spec(self) -> ToolSpec:
        """Render this tool's schema as an `llm.base.ToolSpec` for the LLM adapter."""
        ...


class BaseTool:
    """Implementation-sharing convenience for concrete `Tool` implementations.

    Not a `Protocol`, not itself typed against by callers outside `tools/` (always use `Tool`)
    — purely spares every concrete tool class from re-deriving `to_tool_spec()`.
    """

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    data_class: DataClass
    # Opt-in structured-card classification: a tool whose typed output is genuinely
    # chart/table/gauge-shaped sets this to a stable label the frontend renders a card from;
    # left at None (the default for every tool) it stays prose-only, so opting a tool in is an
    # explicit, per-tool decision with no behavior change for the rest.
    card_type: str | None = None
    # Opt-in row-level preview (governance.md §4 "top-N-preview exception"): a `sponsor` tool
    # whose output is a ranked row list may set this `True` to receive `ctx` at `run()` time
    # even when the caller is unentitled, so it can null out each row's value fields itself
    # beyond its own `free_row_count` — see `dispatch_tool` below. Left `False` (the default),
    # a `sponsor` tool never runs at all for an unentitled caller; opting in is per-tool and
    # never changes behavior for an entitled caller (who always gets every row, unmasked).
    supports_row_preview: bool = False

    def to_tool_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.input_model.model_json_schema(),
        )

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:  # pragma: no cover
        raise NotImplementedError


@dataclass
class _CacheEntry:
    stored_at: float
    token: object
    result: BaseModel


class _ResultCache:
    """One tool instance's cached results and in-flight computations."""

    def __init__(self) -> None:
        self.entries: dict[str, _CacheEntry] = {}
        self.inflight: dict[str, asyncio.Future[BaseModel]] = {}


_MAX_CACHED_RESULTS = 128


class SyncStoreTool(BaseTool):
    """A tool whose whole body is blocking work (Parquet/DuckDB reads, pandas, row validation).

    Subclasses implement the plain synchronous `run_sync`; `run` hands it to a worker thread, so a
    slow read cannot freeze the event loop. Done inline in an `async def`, one heavy request (the
    sector-rotation heatmap took ~9 s) stalls every other request on the process, including
    trivial ones. A tool that also awaits upstream I/O should instead extract just its store
    reads into a sync helper and run that through `anyio.to_thread.run_sync`.

    A worker thread still shares the GIL, so CPU-bound Python (validating tens of thousands of
    rows) slows other requests down even off the loop. The data only changes when the daily
    ingest lands, so a whole-market derivation can opt into a result cache: set
    `cache_ttl_seconds > 0` and a result is reused while the store's freshness token (which
    changes whenever ingest writes new data) is unchanged and the entry is younger than the TTL.
    The key is the arguments plus the caller's tier (row-preview tools mask by entitlement).
    Identical simultaneous requests share one computation (single-flight).

    Thread-safety: the stores open a fresh DuckDB connection per read and keep no per-request
    state, so concurrent worker threads are safe; contextvars (structlog bindings) are copied
    into the thread by anyio."""

    # Seconds a result may be reused while the store is unchanged; 0 (the default) never caches.
    cache_ttl_seconds: float = 0.0

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:  # pragma: no cover
        raise NotImplementedError

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        store = getattr(self, "_store", None)
        market = getattr(args, "market", None)
        if self.cache_ttl_seconds <= 0 or store is None or market is None:
            return await self._compute(args, ctx)

        token = store.freshness_token(getattr(market, "value", str(market)))
        key = f"{ctx.tier.value}|{args.model_dump_json()}"
        cache = _result_caches.setdefault(self, _ResultCache())
        now = time.monotonic()
        entry = cache.entries.get(key)
        if (
            entry is not None
            and entry.token == token
            and now - entry.stored_at < self.cache_ttl_seconds
        ):
            return entry.result
        pending = cache.inflight.get(key)
        if pending is not None:
            try:
                return await asyncio.shield(pending)
            except Exception:
                return await self._compute(args, ctx)

        future: asyncio.Future[BaseModel] = asyncio.get_running_loop().create_future()
        cache.inflight[key] = future
        try:
            result = await self._compute(args, ctx)
        except Exception as exc:
            future.set_exception(exc)
            future.exception()  # mark retrieved: nobody may be waiting
            raise
        except BaseException:
            future.cancel()
            raise
        else:
            cache.entries[key] = _CacheEntry(now, token, result)
            while len(cache.entries) > _MAX_CACHED_RESULTS:
                cache.entries.pop(next(iter(cache.entries)))
            future.set_result(result)
            return result
        finally:
            cache.inflight.pop(key, None)

    async def _compute(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        return await anyio.to_thread.run_sync(self.run_sync, args, ctx)


_result_caches: weakref.WeakKeyDictionary[SyncStoreTool, _ResultCache] = weakref.WeakKeyDictionary()


class MaskedTeaser(BaseModel):
    """The masked-teaser envelope for an unentitled `sponsor` call — rendered blurred/馬賽克
    with an upgrade CTA by the frontend. Never a hard error/exception; this is a normal,
    successful tool result shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    locked: Literal[True] = True
    required_tier: Tier
    preview: str = Field(min_length=1)
    tool_name: str


_ENTITLED_TIERS: frozenset[Tier] = frozenset({Tier.PREMIUM, Tier.PRO})


def is_entitled(data_class: DataClass, tier: Tier) -> bool:
    """Pure predicate: does `tier` satisfy `data_class`'s access rule?

    `public` is free for everyone (incl. anonymous/`Tier.FREE`); `sponsor` requires
    Premium/Pro. This has no side effects and performs no lookup of its own — `tier` is
    supplied by the caller (see `dispatch_tool`).
    """
    if data_class is DataClass.PUBLIC:
        return True
    return tier in _ENTITLED_TIERS


async def dispatch_tool(tool: Tool, args: BaseModel, ctx: ToolContext) -> BaseModel | MaskedTeaser:
    """The `pre_tool`-shaped entitlement gate.

    A tool always runs — there is no approval/confirmation step. The *only* substitution this
    function can make instead of running the tool is a `MaskedTeaser`, and only when
    `tool.data_class == DataClass.SPONSOR` and `ctx.tier` isn't entitled.

    **Phase-9 seam:** `accounts/entitlements.py` (not yet built) will eventually resolve
    `ctx.user_id` to a live per-user tier and either construct `ctx` with that real tier before
    calling this function, or replace `is_entitled(...)` with its own lookup — this function's
    signature and masked-teaser shape are the stable contract that lookup plugs into. Nothing
    here queries a database; `ctx.tier` is trusted as given (entitlement is meant to be resolved
    per tool call, not cached for the turn — the *caller* re-resolves it each call).
    """
    if tool.data_class is DataClass.SPONSOR and not is_entitled(tool.data_class, ctx.tier):
        if tool.supports_row_preview:
            # Row-preview opt-in (governance.md §4 "top-N-preview exception"): the tool itself
            # runs and is responsible for nulling every row's value fields beyond its own
            # `free_row_count` — `ctx.tier` (still unentitled) is passed through unchanged so
            # the tool can make that call; this function never inspects the tool's own output.
            logger.info("tool_entitlement_row_preview", tool=tool.name, tier=ctx.tier.value)
            return await tool.run(args, ctx)
        logger.info("tool_entitlement_masked", tool=tool.name, tier=ctx.tier.value)
        return MaskedTeaser(
            required_tier=Tier.PREMIUM,
            # User-facing text — never the raw internal tool name (`tool.name` still travels in
            # the `tool_name` field below for the frontend/telemetry to key off of, but is not
            # meant to be displayed verbatim to a user).
            preview="This data is available on a Premium or Pro subscription.",
            tool_name=tool.name,
        )
    return await tool.run(args, ctx)


__all__ = [
    "BaseTool",
    "MaskedTeaser",
    "Tool",
    "ToolContext",
    "dispatch_tool",
    "is_entitled",
]
