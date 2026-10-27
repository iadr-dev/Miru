"""Additive refresh of a period-keyed partition: add the companies the stored partition lacks.

A partition that already holds rows is never re-fetched by the gap-fill (`has_rows`), so one
written early in a filing season stays partial for good. Re-running the job would REPLACE the whole
partition, and the official reports carry only the aggregate lines (about 15 income / 15 balance
sheet / 7 cash flow lines per company) where the FinMind-era rows carry the full statement, so a
rewrite would lose detail. This operator-run pass instead fetches the official source and ADDS the
rows of every company that is absent from the stored partition. A stored row is never replaced,
edited or dropped, so FinMind-era detail lines survive.

A company is identified by `symbol` within the dataset. Each statement type is its own dataset
(income, balance sheet, cash flow), so "absent" is per company per statement type. The row's
`statement_type` is not part of the key on purpose: FinMind and the official mapping may spell it
differently, which would make every stored company look absent and duplicate it.

The write is the ordinary atomic partition swap (`ParquetStore.write_partition`); the pass is
leader-gated, journaled, and idempotent (a second run adds nothing and writes nothing). If the
official source fails or has nothing, the stored partition is left exactly as it was.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_

import structlog

from ingest.context import IngestContext
from ingest.jobs import ALL_WHOLE_MARKET_JOBS, WholeMarketJobSpec
from ingest.jobs.period_dates import filing_date_for
from ingest.jobs.source_chain import FILINGS_ID, result_rows
from ingest.jobs.whole_market import validate_rows
from ingest.journal import JobRun, RunStatus, journal_run
from ingest.rows import without_path_columns
from schemas.source import HealthStatus
from sources.base import WholeMarketSource

logger = structlog.get_logger(__name__)

# The company key inside a dataset (see the module docstring).
_COMPANY_KEY = "symbol"


def period_keyed_specs() -> tuple[WholeMarketJobSpec, ...]:
    """The datasets this pass can refresh: monthly revenue and the three statements."""
    return tuple(spec for spec in ALL_WHOLE_MARKET_JOBS if spec.period_anchor is not None)


@dataclass(frozen=True)
class RefreshOutcome:
    """The journal entry of one refresh and the company counts it moved."""

    run: JobRun
    companies_before: int
    companies_after: int

    @property
    def companies_added(self) -> int:
        return self.companies_after - self.companies_before


def _companies(rows: list[dict[str, object]]) -> set[str]:
    return {str(row[_COMPANY_KEY]) for row in rows if row.get(_COMPANY_KEY) is not None}


def _journal(
    spec: WholeMarketJobSpec, run_date: date_, status: RunStatus, detail: str, rows: int = 0
) -> JobRun:
    return journal_run(spec.dataset, run_date, rows_written=rows, status=status, detail=detail)


def _precheck(
    spec: WholeMarketJobSpec, run_date: date_, ctx: IngestContext
) -> tuple[date_, WholeMarketSource] | RefreshOutcome:
    """The nominal date to ask the official source for and that source, or the outcome that ends
    the pass early: not the leader, no period stored under `run_date`, or no official source."""
    if spec.period_anchor is None:
        raise ValueError(f"{spec.dataset} is not a period-keyed dataset")
    if not ctx.leader.is_leader():
        run = _journal(spec, run_date, RunStatus.SKIPPED, "not the leader - write skipped")
        return RefreshOutcome(run, 0, 0)
    nominal = filing_date_for(spec.period_anchor, run_date, ctx.calendar)
    if nominal is None:
        detail = f"{run_date.isoformat()} is not the partition of any period of {spec.dataset}"
        return RefreshOutcome(_journal(spec, run_date, RunStatus.SKIPPED, detail), 0, 0)
    if ctx.filings is None:
        detail = "no official filings source configured"
        return RefreshOutcome(_journal(spec, run_date, RunStatus.FAILED, detail), 0, 0)
    return nominal, ctx.filings


async def fill_missing_companies(
    spec: WholeMarketJobSpec, run_date: date_, ctx: IngestContext
) -> RefreshOutcome:
    """Add to partition `(spec.dataset, run_date)` the companies the official source has and the
    partition lacks. Never replaces or deletes a stored row. `rows_written` of the journal entry is
    the number of rows ADDED."""
    checked = _precheck(spec, run_date, ctx)
    if isinstance(checked, RefreshOutcome):
        return checked
    nominal, filings = checked

    market = ctx.market.value
    stored = without_path_columns(
        ctx.store.read_partition(spec.dataset, market, run_date), keep=frozenset({"market"})
    )
    stored_companies = _companies(stored)
    before = len(stored_companies)

    result = await filings.fetch_whole_market(
        spec.capability, tls_policy=ctx.tls_policy, start_date=nominal.isoformat()
    )
    if result.health.status not in (HealthStatus.OK, HealthStatus.STALE):
        detail = f"{FILINGS_ID} failed, partition left untouched: {result.health.detail}"
        logger.warning("ingest_refresh_source_failed", dataset=spec.dataset, detail=detail)
        return RefreshOutcome(_journal(spec, run_date, RunStatus.FAILED, detail), before, before)
    official = validate_rows(spec, result_rows(result), run_date)
    if not official:
        detail = f"{FILINGS_ID} has no rows ({result.health.detail}), partition left untouched"
        return RefreshOutcome(_journal(spec, run_date, RunStatus.SKIPPED, detail), before, before)

    added = [row for row in official if str(row.get(_COMPANY_KEY)) not in stored_companies]
    after = before + len(_companies(added))
    served = f"served by {FILINGS_ID}: {result.health.detail}"
    if not added:
        detail = f"added 0 companies ({before} stored, {len(_companies(official))} official); "
        run = _journal(spec, run_date, RunStatus.SUCCESS, detail + served)
        return RefreshOutcome(run, before, before)

    ctx.store.write_partition(spec.dataset, market, run_date, [*stored, *added])
    await ctx.data_update_publisher.publish_data_updated(
        capability=spec.capability,
        market=ctx.market,
        as_of=run_date.isoformat(),
        scope="whole-market",
    )
    detail = f"added {after - before} companies ({len(added)} rows), {before} -> {after}; "
    run = _journal(spec, run_date, RunStatus.SUCCESS, detail + served, len(added))
    return RefreshOutcome(run, before, after)


__all__ = ["RefreshOutcome", "fill_missing_companies", "period_keyed_specs"]
