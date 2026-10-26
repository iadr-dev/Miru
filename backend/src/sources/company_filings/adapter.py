"""The official company-filings adapter: datasets the companies themselves report (monthly
revenue, ...) from the exchanges' and MOPS's free pages.

An ingest-only whole-market source with the call shape of `FinMindAdapter.fetch_whole_market`, so
the runner is source-agnostic; not a `SourceAdapter`, never reachable through the per-symbol
router. Unlike the daily exchange reports, these datasets are keyed by a reporting period, not by
the date of a trading day, so each capability says which run dates it owns:

- a date that holds no rows BY DEFINITION (monthly revenue is filed under the 1st of a month)
  is answered `HealthStatus.OK` with no rows and costs no request: the runner reads that as "the
  source knows there is nothing" and does not ask the next source;
- a date whose period is not complete yet (before the filing deadline) is `HealthStatus.STALE`
  with no rows, again without a request: the next source is asked and the date stays a gap;
- otherwise the pages are fetched. **Never half a market**: every required page must answer,
  and a failure or a missing required market makes the whole answer UNREACHABLE, so a partition
  is never overwritten with one market's companies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final
from zoneinfo import ZoneInfo

import structlog

from schemas.enums import Capability, DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.exchange_whole_market._client import ThrottledJsonClient
from sources.exchange_whole_market._common import ExchangeFetchError, Row

from . import month_revenue as revenue

logger = structlog.get_logger(__name__)

SOURCE_ID: Final = "OFFICIAL_FILINGS"
# MOPS is a small, rate-limited site and some of its pages sit behind a challenge: one request
# every 3 s, three pages per month, and a failure is never retried in a tight loop.
MOPS_MIN_GAP_SECONDS: Final = 3.0
_TAIPEI: Final = ZoneInfo("Asia/Taipei")
_ROW_KEY: Final = {Capability.MONTH_REVENUE: "rows"}
_CAVEAT: Final = (
    "Company filings republished by MOPS, free and as last amended: a figure restated after the "
    "filing shows its restated value. Monthly revenue is complete from the 11th of the month "
    "after the revenue month (the statutory deadline is the 10th)."
)

type Clock = Callable[[], datetime]


@dataclass(frozen=True)
class _Page:
    market_dir: str
    rows: list[Row] | None = None
    error: str | None = None


class CompanyFilingsAdapter:
    """`OFFICIAL_FILINGS` - period-keyed company reports from MOPS."""

    source_id = SOURCE_ID
    timing_class = TimingClass.OFFICIAL_EOD

    def __init__(
        self,
        *,
        mops_client: ThrottledJsonClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._mops = mops_client or ThrottledJsonClient(
            host=revenue.HOST, min_gap_seconds=MOPS_MIN_GAP_SECONDS
        )
        self._now = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def supported_capabilities() -> frozenset[Capability]:
        return frozenset(_ROW_KEY)

    async def fetch_whole_market(
        self, cap: Capability, *, tls_policy: TLSPolicy, **params: object
    ) -> SourceResult:
        if cap not in _ROW_KEY:
            return _result(
                cap, [], HealthStatus.UNAVAILABLE_TIER, f"no official filing source for {cap.value}"
            )
        run_date = _requested_date(params.get("start_date"))
        if run_date is None:
            return _result(cap, [], HealthStatus.UNREACHABLE, "start_date (YYYY-MM-DD) required")
        return await self._month_revenue(run_date, tls_policy)

    async def _month_revenue(self, run_date: date, tls_policy: TLSPolicy) -> SourceResult:
        cap = Capability.MONTH_REVENUE
        period = revenue.period_for(run_date)
        if period is None:
            return _result(
                cap,
                [],
                HealthStatus.OK,
                f"{run_date.isoformat()} is not the 1st of a month: monthly revenue is filed "
                "under the 1st, so this date holds none",
            )
        today = self._now().astimezone(_TAIPEI).date()
        if not revenue.is_complete(period, today):
            return _result(
                cap,
                [],
                HealthStatus.STALE,
                f"revenue for {period.year}-{period.month:02d} is due by the 10th: "
                "not complete yet",
            )
        pages = [await self._page(m, period, tls_policy) for m, _ in revenue.MARKETS]
        result = _combine_revenue(period, pages)
        logger.info(
            "company_filings_fetched",
            capability=cap.value,
            run_date=run_date.isoformat(),
            status=result.health.status.value,
            detail=result.health.detail,
        )
        return result

    async def _page(
        self, market_dir: str, period: revenue.RevenuePeriod, tls_policy: TLSPolicy
    ) -> _Page:
        label = f"t21sc03 {market_dir}"
        try:
            html = await self._mops.get_text(
                revenue.page_url(market_dir, period),
                label=label,
                tls_policy=tls_policy,
                encoding=revenue.ENCODING,
            )
            return _Page(market_dir, rows=revenue.parse_page(html, market_dir, period))
        except ExchangeFetchError as exc:
            return _Page(market_dir, error=exc.detail)


def _combine_revenue(period: revenue.RevenuePeriod, pages: list[_Page]) -> SourceResult:
    cap = Capability.MONTH_REVENUE
    failed = [f"{p.market_dir}: {p.error}" for p in pages if p.rows is None]
    if failed:
        return _result(cap, [], HealthStatus.UNREACHABLE, "; ".join(failed))
    empty_required = [
        p.market_dir for p in pages if not p.rows and p.market_dir in revenue.REQUIRED_MARKETS
    ]
    if len(empty_required) == len(revenue.REQUIRED_MARKETS):
        return _result(cap, [], HealthStatus.STALE, "no revenue published (listed and OTC empty)")
    if empty_required:
        return _result(
            cap,
            [],
            HealthStatus.UNREACHABLE,
            f"partial: {', '.join(empty_required)} returned no rows while the other market did",
        )
    merged: list[Row] = []
    seen: set[object] = set()
    for page in pages:
        for row in page.rows or []:
            if row["symbol"] in seen:
                logger.warning("company_filings_duplicate_row", symbol=row["symbol"])
                continue
            seen.add(row["symbol"])
            merged.append(row)
    counts = " + ".join(
        f"{label} {len(p.rows or [])}" for p, (_, label) in zip(pages, revenue.MARKETS, strict=True)
    )
    return _result(
        cap,
        merged,
        HealthStatus.OK,
        f"MOPS t21sc03 {period.year}-{period.month:02d}: {counts} rows",
    )


def _requested_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _result(cap: Capability, rows: list[Row], status: HealthStatus, detail: str) -> SourceResult:
    return SourceResult(
        source_id=SOURCE_ID,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload={_ROW_KEY.get(cap, "rows"): rows},
        health=SourceHealth(status=status, detail=detail),
    )
