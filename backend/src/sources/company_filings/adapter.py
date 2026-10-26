"""The official company-filings adapter: datasets the companies themselves report (monthly
revenue, financial statements) from MOPS's free report pages.

An ingest-only whole-market source with the call shape of `FinMindAdapter.fetch_whole_market`, so
the runner is source-agnostic; not a `SourceAdapter`, never reachable through the per-symbol
router. Unlike the daily exchange reports, these datasets are keyed by a reporting period, not by
the date of a trading day, so each capability says which run dates it owns:

- a date that holds no rows BY DEFINITION (monthly revenue is filed under the 1st of a month, a
  statement under its quarter end) is answered `HealthStatus.OK` with no rows and costs no
  request: the runner reads that as "the source knows there is nothing" and does not ask the next
  source;
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
from . import statements

logger = structlog.get_logger(__name__)

SOURCE_ID: Final = "OFFICIAL_FILINGS"
# MOPS is a small, rate-limited site and some of its pages sit behind a challenge: one request
# every 3 s, a handful of pages per period, and a failure is never retried in a tight loop.
MOPS_MIN_GAP_SECONDS: Final = 3.0
_TAIPEI: Final = ZoneInfo("Asia/Taipei")
# The payload key each capability's rows go under (the key FinMind's own mapper uses).
_ROW_KEY: Final = {
    Capability.MONTH_REVENUE: "rows",
    Capability.FINANCIALS: "lines",
    Capability.BALANCE_SHEET: "lines",
    Capability.CASH_FLOW_STATEMENT: "lines",
}
_CAVEAT: Final = (
    "Company filings republished by MOPS, free and as last amended: a figure restated after the "
    "filing shows its restated value. Monthly revenue is complete from the 11th of the month "
    "after the revenue month (the statutory deadline is the 10th); statements are the "
    "aggregate lines of the summary reports, complete after the last statutory deadline."
)

type Clock = Callable[[], datetime]


@dataclass(frozen=True)
class _Part:
    """One market's contribution to a period: its rows, or why it could not be had."""

    market_dir: str
    label: str
    rows: list[Row] | None = None
    error: str | None = None
    companies: int = 0


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
        today = self._now().astimezone(_TAIPEI).date()
        if cap is Capability.MONTH_REVENUE:
            result = await self._month_revenue(run_date, today, tls_policy)
        else:
            result = await self._statement(cap, run_date, today, tls_policy)
        logger.info(
            "company_filings_fetched",
            capability=cap.value,
            run_date=run_date.isoformat(),
            status=result.health.status.value,
            detail=result.health.detail,
        )
        return result

    # ----------------------------------------------------------------- monthly revenue

    async def _month_revenue(
        self, run_date: date, today: date, tls_policy: TLSPolicy
    ) -> SourceResult:
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
        if not revenue.is_complete(period, today):
            return _result(
                cap,
                [],
                HealthStatus.STALE,
                f"revenue for {period.year}-{period.month:02d} is due by the 10th: "
                "not complete yet",
            )
        parts = [
            await self._revenue_part(market_dir, label, period, tls_policy)
            for market_dir, label in revenue.MARKETS
        ]
        return _combine(
            cap,
            parts,
            required=revenue.REQUIRED_MARKETS,
            key=lambda row: (row["symbol"],),
            what=f"MOPS t21sc03 {period.year}-{period.month:02d}",
            unit="rows",
        )

    async def _revenue_part(
        self, market_dir: str, label: str, period: revenue.RevenuePeriod, tls_policy: TLSPolicy
    ) -> _Part:
        try:
            html = await self._mops.get_text(
                revenue.page_url(market_dir, period),
                label=f"t21sc03 {market_dir}",
                tls_policy=tls_policy,
                encoding=revenue.ENCODING,
            )
            rows = revenue.parse_page(html, market_dir, period)
            return _Part(market_dir, label, rows=rows, companies=len(rows))
        except ExchangeFetchError as exc:
            return _Part(market_dir, label, error=exc.detail)

    # --------------------------------------------------------------------- statements

    async def _statement(
        self, cap: Capability, run_date: date, today: date, tls_policy: TLSPolicy
    ) -> SourceResult:
        period = statements.period_for(run_date)
        if period is None:
            return _result(
                cap,
                [],
                HealthStatus.OK,
                f"{run_date.isoformat()} is not a quarter end: statements are filed under the "
                "end of their quarter, so this date holds none",
            )
        if not statements.is_complete(period, today):
            return _result(
                cap,
                [],
                HealthStatus.STALE,
                f"statements for {period.label} are not complete before the last filing deadline",
            )
        report = statements.REPORTS[cap]
        parts = [
            await self._statement_part(report, period, market_dir, label, tls_policy)
            for market_dir, label, _ in statements.MARKETS
        ]
        return _combine(
            cap,
            parts,
            required=statements.REQUIRED_MARKETS,
            key=lambda row: (row["symbol"], row["item"]),
            what=f"MOPS {report.endpoint} {period.label}",
            unit="companies",
        )

    async def _statement_part(
        self,
        report: statements.Report,
        period: statements.StatementPeriod,
        market_dir: str,
        label: str,
        tls_policy: TLSPolicy,
    ) -> _Part:
        try:
            current = await self._report_table(
                report, market_dir, (period.roc_year, period.season), tls_policy
            )
            earlier: list[statements.Table] = []
            if report.single_quarter:
                # Walk back through the year only while some company still has no earlier
                # filing: a half-yearly filer has none in the previous quarter.
                for season in statements.earlier_seasons(period):
                    if not statements.has_unresolved(current, earlier):
                        break
                    earlier.append(
                        await self._report_table(
                            report, market_dir, (period.roc_year, season), tls_policy
                        )
                    )
        except ExchangeFetchError as exc:
            return _Part(market_dir, label, error=exc.detail)
        rows = statements.statement_rows(report, period, current, earlier)
        return _Part(market_dir, label, rows=rows, companies=len({r["symbol"] for r in rows}))

    async def _report_table(
        self,
        report: statements.Report,
        market_dir: str,
        season_key: tuple[int, int],
        tls_policy: TLSPolicy,
    ) -> statements.Table:
        roc_year, season = season_key
        html = await self._mops.get_text(
            statements.endpoint_url(report),
            label=f"{report.endpoint} {market_dir} {roc_year}/{season}",
            tls_policy=tls_policy,
            encoding=statements.ENCODING,
            form=statements.form(market_dir, roc_year, season),
        )
        return statements.parse_page(html, market_dir, season, report)


def _combine(  # noqa: PLR0913 - one argument per fact the answer is built from
    cap: Capability,
    parts: list[_Part],
    *,
    required: frozenset[str],
    key: Callable[[Row], tuple[object, ...]],
    what: str,
    unit: str,
) -> SourceResult:
    failed = [f"{p.market_dir}: {p.error}" for p in parts if p.rows is None]
    if failed:
        return _result(cap, [], HealthStatus.UNREACHABLE, "; ".join(failed))
    empty = [p.market_dir for p in parts if not p.rows and p.market_dir in required]
    if len(empty) == len(required):
        return _result(cap, [], HealthStatus.STALE, "nothing published (listed and OTC empty)")
    if empty:
        return _result(
            cap,
            [],
            HealthStatus.UNREACHABLE,
            f"partial: {', '.join(empty)} returned no rows while the other market did",
        )
    merged: list[Row] = []
    seen: set[tuple[object, ...]] = set()
    for part in parts:
        for row in part.rows or []:
            if key(row) in seen:
                logger.warning("company_filings_duplicate_row", key=key(row))
                continue
            seen.add(key(row))
            merged.append(row)
    counts = " + ".join(f"{p.label} {p.companies}" for p in parts)
    return _result(cap, merged, HealthStatus.OK, f"{what}: {counts} {unit}")


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
