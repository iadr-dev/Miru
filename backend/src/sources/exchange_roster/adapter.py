"""The official stock roster: TWSE's ISIN lists of the listed, OTC and emerging boards.

An ingest-only whole-market source with the call shape of `FinMindAdapter.fetch_whole_market`, so
the runner is source-agnostic; not a `SourceAdapter`, never reachable through the per-symbol
router. It serves `Capability.STOCK_INFO` only, in the rows the FinMind roster had
(`isin.py` says how each field is rebuilt).

A roster is the current state, not a history: the lists carry no date to ask for, so the date the
runner passes only names the partition. **Never half a market**: the listed, the OTC AND the
emerging list must all answer and each must hold a plausible number of securities; one failing is
UNREACHABLE and the runner falls back, so a roster partition is never written without a board.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import structlog

from schemas.enums import Capability, DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.exchange_whole_market._client import RequestPolicy, ThrottledJsonClient
from sources.exchange_whole_market._common import ExchangeFetchError, Row

from . import isin

logger = structlog.get_logger(__name__)

SOURCE_ID: Final = "EXCHANGE_ROSTER"
# One request every 3 s to a small government site; the listed list is about 10 MB (the warrants),
# so the timeout is far above the exchange reports' 20 s.
ISIN_MIN_GAP_SECONDS: Final = 3.0
_POLICY: Final = RequestPolicy(timeout_seconds=90.0, max_attempts=3, backoff_seconds=5.0)
_ROW_KEY: Final = "rows"
_CAVEAT: Final = (
    "Official TWSE ISIN lists (listed, OTC and emerging boards), free: the current roster, "
    "refreshed by the exchange on each business day, without warrants, depositary receipts, "
    "REITs or asset-backed securities. `as_of` is the list's own last-update date."
)


class ExchangeRosterAdapter:
    """`EXCHANGE_ROSTER` - the stock/ETF roster from the TWSE ISIN lists."""

    source_id = SOURCE_ID
    timing_class = TimingClass.REFERENCE

    def __init__(
        self,
        *,
        client: ThrottledJsonClient | None = None,
        boards: tuple[isin.Board, ...] = isin.BOARDS,
    ) -> None:
        self._client = client or ThrottledJsonClient(
            host=isin.HOST, min_gap_seconds=ISIN_MIN_GAP_SECONDS, policy=_POLICY
        )
        self._boards = boards

    @staticmethod
    def supported_capabilities() -> frozenset[Capability]:
        return frozenset({Capability.STOCK_INFO})

    async def fetch_whole_market(
        self, cap: Capability, *, tls_policy: TLSPolicy, **_params: object
    ) -> SourceResult:
        if cap is not Capability.STOCK_INFO:
            return _result([], HealthStatus.UNAVAILABLE_TIER, f"no official roster for {cap.value}")
        started = datetime.now(UTC)
        entries: list[isin.IsinEntry] = []
        counts: list[str] = []
        for board in self._boards:
            try:
                html = await self._client.get_text(
                    board.url,
                    label=f"ISIN strMode={board.mode}",
                    tls_policy=tls_policy,
                    encoding=isin.ENCODING,
                )
                parsed = isin.parse_page(html, board)
            except ExchangeFetchError as exc:
                result = _result([], HealthStatus.UNREACHABLE, f"{board.label}: {exc.detail}")
                logger.warning("exchange_roster_failed", board=board.label, detail=exc.detail)
                return result
            entries.extend(parsed)
            counts.append(f"{board.label} {len(parsed)}")
        rows = isin.to_rows(entries)
        blank = sum(1 for row in rows if not row["industry_category"])
        result = _result(rows, HealthStatus.OK, _detail(entries, counts, blank))
        logger.info(
            "exchange_roster_fetched",
            rows=len(rows),
            without_industry=blank,
            seconds=round((datetime.now(UTC) - started).total_seconds(), 1),
        )
        return result


def _detail(entries: list[isin.IsinEntry], counts: list[str], blank: int) -> str:
    updated = max(entry.updated for entry in entries)
    detail = f"TWSE ISIN lists updated {updated.isoformat()}: {' + '.join(counts)} securities"
    return f"{detail}, {blank} without an industry" if blank else detail


def _result(rows: list[Row], status: HealthStatus, detail: str) -> SourceResult:
    return SourceResult(
        source_id=SOURCE_ID,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload={_ROW_KEY: rows},
        health=SourceHealth(status=status, detail=detail),
    )
