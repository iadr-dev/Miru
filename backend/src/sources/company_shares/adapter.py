"""The official shares-outstanding snapshot: TWSE and TPEx company basics.

Two free open-data dumps, one request each, give the issued common shares of every listed and OTC
company as of the exchanges' own table date. This adapter is the network edge for the derived
market-value datasets (`ingest/jobs/market_weight.py`); it is not a `WholeMarketSource` itself
because a snapshot has no date to ask for - it is the current state, not a history.

**Never half a market.** Both dumps must answer and each must hold a plausible number of
companies; one failing is an `ExchangeFetchError`, so a derived partition is never built from the
listed board (or the OTC board) alone.

**At most one fetch a day.** Share counts change rarely (an issue, a cancellation), so a snapshot
read today is reused for every later request of the same Taipei day (a backfill walks many dates,
the daily jobs ask twice); the next day it is fetched again.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final
from zoneinfo import ZoneInfo

import structlog

from schemas.enums import TLSPolicy
from sources.exchange_whole_market._client import ThrottledJsonClient

from . import parse
from .parse import ShareEntry

logger = structlog.get_logger(__name__)

SOURCE_ID: Final = "COMPANY_SHARES"
TWSE_HOST: Final = "openapi.twse.com.tw"
TPEX_HOST: Final = "www.tpex.org.tw"
LISTED_URL: Final = f"https://{TWSE_HOST}/v1/opendata/t187ap03_L"
OTC_URL: Final = f"https://{TPEX_HOST}/openapi/v1/mopsfin_t187ap03_O"
# Politeness: the same gaps the exchange reports use; one request each per day.
TWSE_MIN_GAP_SECONDS: Final = 2.5
TPEX_MIN_GAP_SECONDS: Final = 2.0
_TAIPEI: Final = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class SharesSnapshot:
    """Issued common shares per company, as the exchanges' company-basics dumps state them."""

    as_of: date  # the older of the two dumps' own table dates
    entries: tuple[ShareEntry, ...]
    without_shares: int  # companies left out for a blank or zero count (never invented)
    depositary_receipts: int  # TDRs left out: the dump has the foreign original shares

    def count(self, listing_type: str) -> int:
        return sum(1 for entry in self.entries if entry.listing_type == listing_type)


class CompanySharesAdapter:
    """`COMPANY_SHARES` - shares outstanding from TWSE + TPEx company basics."""

    source_id = SOURCE_ID

    def __init__(
        self,
        *,
        twse_client: ThrottledJsonClient | None = None,
        tpex_client: ThrottledJsonClient | None = None,
        today: Callable[[], date] | None = None,
        min_listed: int = parse.MIN_LISTED_COMPANIES,
        min_otc: int = parse.MIN_OTC_COMPANIES,
    ) -> None:
        self._twse = twse_client or ThrottledJsonClient(
            host=TWSE_HOST, min_gap_seconds=TWSE_MIN_GAP_SECONDS
        )
        self._tpex = tpex_client or ThrottledJsonClient(
            host=TPEX_HOST, min_gap_seconds=TPEX_MIN_GAP_SECONDS
        )
        self._today = today or (lambda: datetime.now(UTC).astimezone(_TAIPEI).date())
        self._min_listed = min_listed
        self._min_otc = min_otc
        self._cache: tuple[date, SharesSnapshot] | None = None
        self._lock = asyncio.Lock()

    async def fetch_snapshot(self, *, tls_policy: TLSPolicy) -> SharesSnapshot:
        """The snapshot, fetched at most once per Taipei day; `ExchangeFetchError` when either
        dump cannot be read (nothing is cached then, so the next call asks again)."""
        async with self._lock:
            today = self._today()
            if self._cache is not None and self._cache[0] == today:
                return self._cache[1]
            snapshot = await self._fetch(tls_policy)
            self._cache = (today, snapshot)
            return snapshot

    async def _fetch(self, tls_policy: TLSPolicy) -> SharesSnapshot:
        listed_rows, otc_rows = await asyncio.gather(
            self._twse.get_json_array(LISTED_URL, label="t187ap03_L", tls_policy=tls_policy),
            self._tpex.get_json_array(OTC_URL, label="mopsfin_t187ap03_O", tls_policy=tls_policy),
        )
        listed = parse.parse_listed(listed_rows, minimum=self._min_listed)
        otc = parse.parse_otc(otc_rows, minimum=self._min_otc)
        snapshot = SharesSnapshot(
            as_of=min(listed.as_of, otc.as_of),
            entries=(*listed.entries, *otc.entries),
            without_shares=listed.without_shares + otc.without_shares,
            depositary_receipts=listed.depositary_receipts + otc.depositary_receipts,
        )
        logger.info(
            "company_shares_fetched",
            as_of=snapshot.as_of.isoformat(),
            listed=len(listed.entries),
            otc=len(otc.entries),
            without_shares=snapshot.without_shares,
        )
        return snapshot
