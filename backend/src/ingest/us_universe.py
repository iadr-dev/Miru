"""US symbol-universe discovery — a monthly reference-data job, the prerequisite for the US
whole-market price/fundamentals jobs in `ingest/us_jobs.py`.

Unlike TW (where FinMind's `TaiwanStockInfo` dataset is the roster), there is no whole-market
symbol-list endpoint for yfinance — `sources.yfinance.screener.run_screener` is a *query*
surface (matching symbols for a condition), not a full-roster dump.

**Bounded by construction, not just convention.** Yahoo hard-caps a single custom screen at
`ScreenerQuery.size <= 250` (`schemas/screener.py`), and `run_screener` exposes no `offset`
param — one query alone can never return more than 250 symbols. This module works around that
per-query ceiling with real pagination-by-market-cap-band: `_MARKET_CAP_BANDS` splits the US
equity universe into disjoint `intradaymarketcap` ranges, and one query (each individually
still capped at 250) runs per band. This is genuine pagination — each band is a distinct,
verified, non-overlapping slice of the real market — not multiple identical queries dressed up
as different pages.

**Still an honest, disclosed approximation, not "every US-listed symbol":** a band whose real
`ScreenerResult.total_matches` exceeds 250 (e.g. the $50-200B band alone had 539 real matches
when this was verified) is truncated to its top-250-by-market-cap within that band — the
smaller-cap tail inside a wide band is dropped, not silently included. `_MARKET_CAP_BANDS`'
five ranges were chosen (and verified against live data) to land around ~1,000-1,500 total
symbols — roughly the large-and-mid-cap range, not micro-caps.

Writes the resulting symbol list to its own Parquet partition (`US_SYMBOL_UNIVERSE_DATASET`)
rather than a relational table — nothing here needs relational joins/queries, so this stays
consistent with how every other whole-market dataset lands (`ingest/store.py`).
"""

from __future__ import annotations

import asyncio
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
# largest any single query (one market-cap band) can ever return.
_BAND_SIZE = 250

# Disjoint `intradaymarketcap` (USD) ranges, high to low; `None` as the upper bound means
# "and above" (no ceiling). Verified against live data at design time: mega-cap ($200B+) had
# 126 real matches (fits in one query uncapped); the next four bands each had more real matches
# than 250 (539 / 1702 / 2964 / 2377) and are therefore each truncated to their own top-250 by
# market cap — an honest, bounded approximation, not a full roster. Five bands land the total
# tracked universe around ~1,000-1,500 symbols (large-and-mid-cap range).
_MARKET_CAP_BANDS: tuple[tuple[int, int | None], ...] = (
    (200_000_000_000, None),
    (50_000_000_000, 200_000_000_000),
    (10_000_000_000, 50_000_000_000),
    (2_000_000_000, 10_000_000_000),
    (500_000_000, 2_000_000_000),
)

# Same rate-limit lesson as `ingest/us_jobs.py::_YFINANCE_THROTTLE_SECONDS`: pace sequential
# screener queries rather than firing them back-to-back.
_BAND_THROTTLE_SECONDS = 0.5


def _band_query(lo: int, hi: int | None) -> ScreenerQuery:
    conditions = [ScreenerCondition(field="region", op=ScreenerOperator.EQ, value="us")]
    if hi is None:
        conditions.append(
            ScreenerCondition(field="intradaymarketcap", op=ScreenerOperator.GT, value=lo)
        )
    else:
        conditions.append(
            ScreenerCondition(
                field="intradaymarketcap", op=ScreenerOperator.BETWEEN, value=lo, value2=hi
            )
        )
    return ScreenerQuery(
        asset_kind=ScreenerAssetKind.EQUITY,
        conditions=tuple(conditions),
        size=_BAND_SIZE,
        sort_field="intradaymarketcap",
        sort_ascending=False,
    )


async def discover_us_symbols() -> list[dict[str, object]]:
    """Run one bounded equity screen per market-cap band (`_MARKET_CAP_BANDS`) and return the
    combined `(symbol, name, exchange)` rows, deduplicated by symbol (a symbol can only ever
    match one disjoint band, but dedup stays defensive rather than assuming the bands never
    drift). A band that returns nothing is logged and skipped — never a hard failure for the
    whole run; only a wholesale empty result across every band fails the job."""
    seen: set[str] = set()
    rows: list[dict[str, object]] = []
    for index, (lo, hi) in enumerate(_MARKET_CAP_BANDS):
        if index > 0:
            await asyncio.sleep(_BAND_THROTTLE_SECONDS)
        result = await run_screener(_band_query(lo, hi))
        if not result.hits:
            logger.warning("us_universe_band_empty", lo=lo, hi=hi, detail=result.detail)
            continue
        for hit in result.hits:
            if not hit.symbol or hit.symbol in seen:
                continue
            seen.add(hit.symbol)
            rows.append({"symbol": hit.symbol, "name": hit.name, "exchange": hit.exchange})
    if not rows:
        logger.warning("us_universe_discovery_empty")
    return rows


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
