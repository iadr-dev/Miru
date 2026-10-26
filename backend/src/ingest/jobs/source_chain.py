"""The ordered source chain behind the whole-market-by-date pulls.

`config/whole_market_source_priority.yaml` lists, per capability, the sources to ask in order
(`[exchange_official, finmind]`: the free official exchange reports first, FinMind only when they
fail or have nothing). `walk_chain` asks them one by one and stops at the first that is healthy
AND has rows; `run_whole_market_job` and the derived batches that need a day's whole-market rows
share it, so the order is decided in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import date as date_
from functools import lru_cache
from pathlib import Path
from typing import Final, cast
from zoneinfo import ZoneInfo

import structlog
import yaml

from ingest.context import IngestContext
from schemas.enums import Capability, Market
from schemas.source import HealthStatus, SourceResult
from sources.base import NO_DATA_PUBLISHED, WholeMarketSource

logger = structlog.get_logger(__name__)

_CONFIG_PATH: Final = (
    Path(__file__).resolve().parents[2] / "config" / "whole_market_source_priority.yaml"
)
FINMIND_ID: Final = "finmind"
EXCHANGE_ID: Final = "exchange_official"
_DEFAULT_CHAIN: Final = (FINMIND_ID,)
_TAIPEI: Final = ZoneInfo("Asia/Taipei")
# The journal detail of a day both exchanges said they published nothing for.
MARKET_CLOSED_DETAIL: Final = "market closed: TWSE and TPEx published no data"


@lru_cache(maxsize=1)
def load_whole_market_priority(path: Path = _CONFIG_PATH) -> dict[str, dict[str, list[str]]]:
    """`{market: {capability: [source_id, ...]}}` from the YAML. Data, not code."""
    with path.open(encoding="utf-8") as fh:
        raw: object = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} did not parse to a mapping")
    return cast("dict[str, dict[str, list[str]]]", raw)


def _sources(ctx: IngestContext) -> dict[str, WholeMarketSource | None]:
    return {FINMIND_ID: ctx.finmind, EXCHANGE_ID: ctx.exchange}


@dataclass(frozen=True)
class ChainLink:
    source_id: str
    source: WholeMarketSource


def resolve_chain(
    ctx: IngestContext, capability: Capability, market: Market | None = None
) -> list[ChainLink]:
    """The sources to ask for `capability`, in order. A source named in the config that this
    context does not carry (no exchange adapter wired) is skipped; if nothing is left the chain is
    FinMind alone, the behaviour before the official sources existed."""
    wanted = (
        load_whole_market_priority()
        .get((market or ctx.market).value, {})
        .get(capability.value, list(_DEFAULT_CHAIN))
    )
    available = _sources(ctx)
    chain: list[ChainLink] = []
    for source_id in wanted:
        if source_id not in available:
            logger.warning("whole_market_unknown_source_id", source_id=source_id)
            continue
        source = available[source_id]
        if source is not None:
            chain.append(ChainLink(source_id, source))
    return chain or [ChainLink(FINMIND_ID, ctx.finmind)]


def result_rows(result: SourceResult) -> list[dict[str, object]]:
    """The rows of a whole-market result (its payload's first, and only, row list)."""
    row_key = next(iter(result.payload), None)
    rows_obj: object = result.payload.get(row_key, []) if row_key else []
    return cast("list[dict[str, object]]", rows_obj) if isinstance(rows_obj, list) else []


def _failed(result: SourceResult) -> bool:
    return result.health.status not in (HealthStatus.OK, HealthStatus.STALE)


def taipei_today() -> date_:
    return datetime.now(UTC).astimezone(_TAIPEI).date()


def confirms_market_closure(
    source_id: str, result: SourceResult, run_date: date_, *, today: date_ | None = None
) -> bool:
    """Whether `result` proves `run_date` was a day the market was closed: the exchange adapter
    reports that TWSE AND TPEx each answered, explicitly, "no data published", and the date is
    strictly before today (Taipei). Today or later may simply not be published yet. Transport or
    parse trouble, or only one exchange being empty, never reaches this detail."""
    return (
        source_id == EXCHANGE_ID
        and result.health.status is HealthStatus.STALE
        and result.health.detail == NO_DATA_PUBLISHED
        and not result_rows(result)
        and run_date < (today or taipei_today())
    )


@dataclass
class ChainOutcome:
    """What walking the chain found."""

    chain_ids: list[str]
    winner_id: str | None = None
    winner: SourceResult | None = None
    # (source_id, reason) for every source tried before the winner / in total when none won.
    failures: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    empties: list[tuple[str, SourceResult]] = field(default_factory=list[tuple[str, SourceResult]])
    last: SourceResult | None = None
    # Both exchanges explicitly published no data for a date before today: a market closure, so
    # the chain stopped without asking the remaining sources.
    market_closed: bool = False

    @property
    def fell_back(self) -> bool:
        """Served by a source other than the first one asked."""
        return self.winner_id is not None and self.winner_id != self.chain_ids[0]

    def attempts_note(self) -> str:
        """Why the sources asked before the winner (or all of them) did not serve."""
        parts = [f"{source_id}: {reason}" for source_id, reason in self.failures]
        parts += [f"{source_id}: no rows" for source_id, _ in self.empties]
        return "; ".join(parts)


async def walk_chain(
    ctx: IngestContext, capability: Capability, run_date: date_, market: Market | None = None
) -> ChainOutcome:
    """Ask each source in turn for `run_date`; stop at the first healthy answer with rows."""
    chain = resolve_chain(ctx, capability, market)
    outcome = ChainOutcome(chain_ids=[link.source_id for link in chain])
    for link in chain:
        result = await link.source.fetch_whole_market(
            capability, tls_policy=ctx.tls_policy, start_date=run_date.isoformat()
        )
        outcome.last = result
        if confirms_market_closure(link.source_id, result, run_date):
            outcome.market_closed = True
            logger.info(
                "ingest_whole_market_closure_confirmed",
                capability=capability.value,
                run_date=run_date.isoformat(),
                source=link.source_id,
            )
            break
        if _failed(result):
            outcome.failures.append((link.source_id, result.health.detail))
            logger.warning(
                "ingest_whole_market_source_failed",
                capability=capability.value,
                run_date=run_date.isoformat(),
                source=link.source_id,
                detail=result.health.detail,
            )
        elif result_rows(result):
            outcome.winner_id, outcome.winner = link.source_id, result
            break
        else:
            outcome.empties.append((link.source_id, result))
    return outcome


async def fetch_whole_market_chained(
    ctx: IngestContext, capability: Capability, run_date: date_, market: Market | None = None
) -> SourceResult:
    """One date's whole-market rows through the chain, for a caller that does not journal or
    write (a derived batch): the winning result, else the last answer (a failure or an empty
    one) so the caller sees the same health a direct FinMind call would have shown."""
    outcome = await walk_chain(ctx, capability, run_date, market)
    result = outcome.winner or outcome.last
    if result is None:  # pragma: no cover - a chain always has a link
        raise RuntimeError(f"no whole-market source for {capability.value}")
    return result


__all__ = [
    "EXCHANGE_ID",
    "FINMIND_ID",
    "MARKET_CLOSED_DETAIL",
    "ChainLink",
    "ChainOutcome",
    "confirms_market_closure",
    "fetch_whole_market_chained",
    "load_whole_market_priority",
    "resolve_chain",
    "result_rows",
    "taipei_today",
    "walk_chain",
]
