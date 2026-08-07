"""US symbol-universe discovery — a monthly reference-data job, the prerequisite for the US
whole-market price/fundamentals jobs in `ingest/us_jobs.py`.

Unlike TW (where FinMind's `TaiwanStockInfo` dataset is the roster), there is no whole-market
symbol-list endpoint for yfinance — `sources.yfinance.screener.run_screener` is a *query*
surface (matching symbols for a condition), not a full-roster dump. This module runs one broad,
US-scoped equity query and treats the result as the tracked universe.

**Bounded by construction, not just convention.** Yahoo hard-caps a custom screen at
`ScreenerQuery.size <= 250` (`schemas/screener.py`), and `run_screener` exposes no pagination
(no `offset` param) — so one query is the entire universe this module can ever produce. Sorted
by market cap descending, this is "the ~250 largest US equities," not "every US-listed symbol."
Widening this later means adding real pagination to `run_screener` (multiple queries, e.g. by
market-cap band) — deliberately out of scope here rather than faked with a client-side loop that
still only ever sees Yahoo's return-250-per-request ceiling per call.

Writes the resulting symbol list to its own Parquet partition (`US_SYMBOL_UNIVERSE_DATASET`)
rather than a relational table — nothing here needs relational joins/queries, so this stays
consistent with how every other whole-market dataset lands (`ingest/store.py`).
"""

from __future__ import annotations

from datetime import date as date_

import structlog

from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run
from ingest.store import ParquetStore
from schemas.enums import Market
from schemas.screener import ScreenerAssetKind, ScreenerCondition, ScreenerOperator, ScreenerQuery
from sources.yfinance import run_screener

logger = structlog.get_logger(__name__)

US_SYMBOL_UNIVERSE_DATASET = "us_symbol_universe"

# Yahoo's own hard cap for a custom screen (`schemas/screener.py::ScreenerQuery.size`) — the
# largest single query can ever return, and therefore the largest universe this module tracks.
_UNIVERSE_SIZE = 250


async def discover_us_symbols() -> list[dict[str, object]]:
    """Run the bounded US equity screen and return `(symbol, name, exchange)` rows.

    Never raises — `run_screener` itself degrades to an empty `ScreenerResult` with an honest
    `detail` on any failure (bad query, upstream error); this function passes that through as
    an empty row list rather than inventing symbols.
    """
    query = ScreenerQuery(
        asset_kind=ScreenerAssetKind.EQUITY,
        conditions=(ScreenerCondition(field="region", op=ScreenerOperator.EQ, value="us"),),
        size=_UNIVERSE_SIZE,
        sort_field="intradaymarketcap",
        sort_ascending=False,
    )
    result = await run_screener(query)
    if not result.hits:
        logger.warning("us_universe_discovery_empty", detail=result.detail)
        return []
    return [
        {
            "symbol": hit.symbol,
            "name": hit.name,
            "exchange": hit.exchange,
        }
        for hit in result.hits
        if hit.symbol
    ]


async def job_us_symbol_universe(run_date: date_, ctx: IngestContext) -> JobRun:
    """Monthly job: refresh the tracked US symbol universe. Not calendar-gated (reference data,
    not a trading-day pull) — mirrors `analytics.themes.seed.seed_industry_themes`'s own
    trading-day-independent cadence. Leader-gated like every other whole-market write."""
    if not ctx.leader.is_leader():
        return journal_run(
            US_SYMBOL_UNIVERSE_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    rows = await discover_us_symbols()
    if not rows:
        return journal_run(
            US_SYMBOL_UNIVERSE_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail="screener returned no symbols — see us_universe_discovery_empty log",
        )

    rows_written = ctx.store.write_partition(
        US_SYMBOL_UNIVERSE_DATASET, Market.US.value, run_date, rows
    )
    return journal_run(
        US_SYMBOL_UNIVERSE_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail="ok",
    )


def latest_us_symbols(store: ParquetStore) -> list[str]:
    """Read back the most recently written US symbol-universe partition's symbol list.

    Returns `[]` if the universe job has never run for this store yet — the natural
    "nothing to backfill against" state for a fresh deployment, not an error; the price/
    fundamentals jobs treat this the same way any other empty-source case degrades (a
    journaled no-op, never a crash).
    """
    market_dir = store.root / f"dataset={US_SYMBOL_UNIVERSE_DATASET}" / f"market={Market.US.value}"
    if not market_dir.exists():
        return []
    candidates: list[date_] = []
    for child in market_dir.iterdir():
        if not child.is_dir() or not child.name.startswith("date="):
            continue
        try:
            candidates.append(date_.fromisoformat(child.name.removeprefix("date=")))
        except ValueError:
            continue
    if not candidates:
        return []
    latest = max(candidates)
    if not store.has_partition(US_SYMBOL_UNIVERSE_DATASET, Market.US.value, latest):
        return []
    rows = store.read_partition(US_SYMBOL_UNIVERSE_DATASET, Market.US.value, latest)
    return [str(row["symbol"]) for row in rows if row.get("symbol")]


__all__ = [
    "US_SYMBOL_UNIVERSE_DATASET",
    "discover_us_symbols",
    "job_us_symbol_universe",
    "latest_us_symbols",
]
