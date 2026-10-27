"""The exchange whole-market adapter: TWSE (listed) + TPEx (OTC) merged into one payload per date.

An ingest-only whole-market-by-date source, like `FinMindAdapter.fetch_whole_market` (it has the
same call shape and returns the same payload shape, so the ingest runner is source-agnostic), but
free, keyless and official. Not a `SourceAdapter`: it is never reachable through the per-symbol
router.

**Never half a market.** The two exchanges are fetched together and the answer is only usable if
both are: one failing, or one returning nothing while the other has rows, is reported as
UNREACHABLE so the runner falls back instead of overwriting a whole-date partition with the
listed (or OTC) market alone. Both empty is the ordinary non-trading / not-yet-published day.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final

import structlog

from schemas.enums import Capability, DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.base import NO_DATA_PUBLISHED, ROUND_LOT_VOLUME

from . import tpex, twse
from ._client import ThrottledJsonClient
from ._common import Endpoint, ExchangeFetchError, NoDataPublishedError, Row, Supplement

logger = structlog.get_logger(__name__)

SOURCE_ID: Final = "EXCHANGE_OFFICIAL"
# TWSE bans an IP above about 3 requests per 5 s; stay well clear. TPEx is not documented to ban
# but is a small site: the same discipline, a little looser.
TWSE_MIN_GAP_SECONDS: Final = 2.5
TPEX_MIN_GAP_SECONDS: Final = 2.0

_ROW_KEY: Final = {
    Capability.PRICE_EOD: "bars",
    Capability.INSTITUTIONAL_FLOW: "rows",
    Capability.MARGIN_SHORT: "rows",
    Capability.VALUATION_RATIOS: "rows",
}
_CAVEAT: Final = (
    "Official TWSE + TPEx end-of-day reports, free and unadjusted: published after the close "
    "(margin balances in the evening), not a live quote. Warrants and CBBC are not included."
)
# Whole-market identity of a row, to drop a code both exchanges would list.
_IDENTITY: Final = ("symbol", "investor")


@dataclass(frozen=True)
class _Outcome:
    rows: list[Row] | None = None
    error: str | None = None
    # The exchange answered explicitly that it published nothing for the date (rows is `[]`).
    no_data: bool = False
    # Rows are usable but incomplete: why the supplementary report could not complete them.
    incomplete: str | None = None


class ExchangeWholeMarketAdapter:
    """`EXCHANGE_OFFICIAL` - the whole-market-by-date reports of TWSE and TPEx."""

    source_id = SOURCE_ID
    timing_class = TimingClass.OFFICIAL_EOD

    def __init__(
        self,
        *,
        twse_client: ThrottledJsonClient | None = None,
        tpex_client: ThrottledJsonClient | None = None,
    ) -> None:
        self._twse = twse_client or ThrottledJsonClient(
            host="www.twse.com.tw", min_gap_seconds=TWSE_MIN_GAP_SECONDS
        )
        self._tpex = tpex_client or ThrottledJsonClient(
            host="www.tpex.org.tw", min_gap_seconds=TPEX_MIN_GAP_SECONDS
        )

    @staticmethod
    def supported_capabilities() -> frozenset[Capability]:
        return frozenset(_ROW_KEY)

    async def fetch_whole_market(
        self, cap: Capability, *, tls_policy: TLSPolicy, **params: object
    ) -> SourceResult:
        if cap not in _ROW_KEY:
            return _result(
                cap, [], HealthStatus.UNAVAILABLE_TIER, f"no exchange report for {cap.value}"
            )
        run_date = _requested_date(params.get("start_date"))
        if run_date is None:
            return _result(cap, [], HealthStatus.UNREACHABLE, "start_date (YYYY-MM-DD) required")

        started = datetime.now(UTC)
        twse_out, tpex_out = await asyncio.gather(
            self._one(self._twse, twse.ENDPOINTS[cap], run_date, tls_policy),
            self._one(
                self._tpex, tpex.ENDPOINTS[cap], run_date, tls_policy, tpex.SUPPLEMENTS.get(cap)
            ),
        )
        result = _combine(cap, twse_out, tpex_out)
        logger.info(
            "exchange_whole_market_fetched",
            capability=cap.value,
            run_date=run_date.isoformat(),
            status=result.health.status.value,
            detail=result.health.detail,
            seconds=round((datetime.now(UTC) - started).total_seconds(), 1),
        )
        return result

    @staticmethod
    async def _one(
        client: ThrottledJsonClient,
        endpoint: Endpoint,
        run_date: date,
        tls_policy: TLSPolicy,
        supplement: Supplement | None = None,
    ) -> _Outcome:
        try:
            rows = await _read(client, endpoint, run_date, tls_policy)
        except NoDataPublishedError:
            return _Outcome(rows=[], no_data=True)
        except ExchangeFetchError as exc:
            return _Outcome(error=exc.detail)
        if supplement is None or not rows:
            return _Outcome(rows=rows)
        try:
            supplied = await _read(client, supplement.report, run_date, tls_policy)
            return _Outcome(rows=supplement.merge(rows, supplied))
        except (ExchangeFetchError, NoDataPublishedError) as exc:
            # A challenge page, an HTTP error, an unreadable or short report: the base rows stand
            # but are not whole. The request budget is the client's (throttled, bounded retries).
            reason = exc.detail if isinstance(exc, ExchangeFetchError) else "no rows published"
            logger.warning(
                "exchange_supplement_failed",
                report=supplement.report.label,
                run_date=run_date.isoformat(),
                reason=reason,
            )
            return _Outcome(rows=rows, incomplete=reason)


async def _read(
    client: ThrottledJsonClient, endpoint: Endpoint, run_date: date, tls_policy: TLSPolicy
) -> list[Row]:
    body = await client.get_json(
        endpoint.url, endpoint.query(run_date), label=endpoint.label, tls_policy=tls_policy
    )
    return endpoint.parse(body, run_date)


def _requested_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _combine(cap: Capability, listed: _Outcome, otc: _Outcome) -> SourceResult:
    if listed.rows is None or otc.rows is None:
        reasons = [
            f"{name}: {out.error}"
            for name, out in (("TWSE", listed), ("TPEx", otc))
            if out.rows is None
        ]
        return _result(cap, [], HealthStatus.UNREACHABLE, "; ".join(reasons))
    if not listed.rows and not otc.rows:
        # Explicit on both = a closure the caller may act on; an empty report that was not an
        # explicit "no data" answer stays an ordinary empty day.
        detail = (
            NO_DATA_PUBLISHED
            if listed.no_data and otc.no_data
            else "no rows published (TWSE and TPEx empty)"
        )
        return _result(cap, [], HealthStatus.STALE, detail)
    if not listed.rows or not otc.rows:
        empty, other = ("TWSE", "TPEx") if not listed.rows else ("TPEx", "TWSE")
        return _result(
            cap,
            [],
            HealthStatus.UNREACHABLE,
            f"partial: {empty} returned no rows while {other} did (not published yet?)",
        )
    merged: list[Row] = []
    seen: set[tuple[object, ...]] = set()
    for row in (*listed.rows, *otc.rows):
        key = tuple(row.get(field) for field in _IDENTITY)
        if key in seen:
            logger.warning("exchange_duplicate_row", capability=cap.value, key=key)
            continue
        seen.add(key)
        merged.append(row)
    detail = f"TWSE {len(listed.rows)} + TPEx {len(otc.rows)} rows"
    if otc.incomplete is not None:
        # The rows are all there but TPEx volume/turnover are round-lot only: not OK, so the chain
        # asks the next source first; the rows ride along for a chain that ends without one.
        return _result(
            cap,
            merged,
            HealthStatus.UNREACHABLE,
            f"{detail}, {ROUND_LOT_VOLUME} (TPEx: {otc.incomplete})",
        )
    return _result(cap, merged, HealthStatus.OK, detail)


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
