"""The exchange event-history adapter: TWSE (listed) + TPEx (OTC) announcements over a date range.

Ingest-only and free, like `ExchangeWholeMarketAdapter` (same throttled client, same typed
no-data answer), but one request per exchange covers a whole range, so a month of history is two
requests, not one per day. Not a `SourceAdapter`: never reachable through the per-symbol router.

**Never half a market.** Both exchanges are asked for the same range and the answer is only
usable if both could be read: one failing (a block page, a bad status, a layout change, a cut-off
report) fails the whole range. An exchange that answers explicitly that it published nothing for
the range (`NoDataPublishedError`) is an empty side, which is an ordinary month for a list as
sparse as the dispositions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as date_
from typing import Final

import structlog

from schemas.enums import TLSPolicy
from sources.exchange_whole_market._client import ThrottledJsonClient
from sources.exchange_whole_market._common import (
    ExchangeFetchError,
    JsonObject,
    NoDataPublishedError,
    Row,
)

from . import tpex, twse

logger = structlog.get_logger(__name__)

# The same discipline as the whole-market reports: TWSE bans an IP above about 3 requests per 5 s.
TWSE_MIN_GAP_SECONDS: Final = 2.5
TPEX_MIN_GAP_SECONDS: Final = 2.0

type _Parse = Callable[[JsonObject, date_, date_], list[Row]]


@dataclass(frozen=True)
class EventHistory:
    """The announcements of one kind over one range, both exchanges together (rows in the stored
    row shape; a period or flag that touches the range twice appears once per query that returned
    it, so the caller deduplicates). `listed` / `otc` count each side's rows; `no_data` is True
    when both exchanges answered, explicitly, that they published nothing."""

    rows: list[Row]
    listed: int
    otc: int
    no_data: bool


@dataclass(frozen=True)
class _Report:
    url: str
    label: str
    parse: _Parse


@dataclass(frozen=True)
class _Side:
    rows: list[Row] | None = None
    error: str | None = None


_TWSE_DISPOSITION = _Report(
    twse.DISPOSITION_URL, "punish", lambda body, _s, _e: twse.parse_disposition(body)
)
_TWSE_ATTENTION = _Report(
    twse.ATTENTION_URL, "notice", lambda body, _s, _e: twse.parse_attention(body)
)
_TPEX_DISPOSITION = _Report(tpex.DISPOSITION_URL, "disposal", tpex.parse_disposition)
_TPEX_ATTENTION = _Report(tpex.ATTENTION_URL, "attention", tpex.parse_attention)


class ExchangeEventHistoryAdapter:
    """TWSE + TPEx attention-stock and disposition history by date range."""

    def __init__(
        self,
        *,
        twse_client: ThrottledJsonClient | None = None,
        tpex_client: ThrottledJsonClient | None = None,
    ) -> None:
        self._twse = twse_client or ThrottledJsonClient(
            host=twse.HOST, min_gap_seconds=TWSE_MIN_GAP_SECONDS
        )
        self._tpex = tpex_client or ThrottledJsonClient(
            host=tpex.HOST, min_gap_seconds=TPEX_MIN_GAP_SECONDS
        )

    async def fetch_disposition(
        self, start: date_, end: date_, *, tls_policy: TLSPolicy
    ) -> EventHistory:
        """Every disposition whose period overlaps `start..end` (both ends inclusive)."""
        return await self._fetch(
            start, end, tls_policy, listed=_TWSE_DISPOSITION, otc=_TPEX_DISPOSITION
        )

    async def fetch_attention(
        self, start: date_, end: date_, *, tls_policy: TLSPolicy
    ) -> EventHistory:
        """Every attention-stock flag announced inside `start..end` (both ends inclusive)."""
        return await self._fetch(
            start, end, tls_policy, listed=_TWSE_ATTENTION, otc=_TPEX_ATTENTION
        )

    async def _fetch(
        self, start: date_, end: date_, tls_policy: TLSPolicy, *, listed: _Report, otc: _Report
    ) -> EventHistory:
        if end < start:
            raise ValueError(f"empty range {start.isoformat()}..{end.isoformat()}")
        listed_side, otc_side = await asyncio.gather(
            self._side(self._twse, listed, twse.query(start, end), (start, end), tls_policy),
            self._side(self._tpex, otc, tpex.query(start, end), (start, end), tls_policy),
        )
        if listed_side.rows is None or otc_side.rows is None:
            reasons = [
                f"{name}: {side.error}"
                for name, side in (("TWSE", listed_side), ("TPEx", otc_side))
                if side.rows is None
            ]
            raise ExchangeFetchError("; ".join(reasons))
        history = EventHistory(
            rows=[*listed_side.rows, *otc_side.rows],
            listed=len(listed_side.rows),
            otc=len(otc_side.rows),
            no_data=not listed_side.rows and not otc_side.rows,
        )
        logger.info(
            "exchange_event_history_fetched",
            report=listed.label,
            start=start.isoformat(),
            end=end.isoformat(),
            listed=history.listed,
            otc=history.otc,
        )
        return history

    @staticmethod
    async def _side(
        client: ThrottledJsonClient,
        report: _Report,
        params: dict[str, str],
        window: tuple[date_, date_],
        tls_policy: TLSPolicy,
    ) -> _Side:
        try:
            body = await client.get_json(
                report.url, params, label=report.label, tls_policy=tls_policy
            )
            return _Side(rows=report.parse(body, *window))
        except NoDataPublishedError:
            return _Side(rows=[])
        except ExchangeFetchError as exc:
            return _Side(error=exc.detail)
