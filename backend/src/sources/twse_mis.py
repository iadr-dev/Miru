"""TWSE MIS adapter — the browser-observed quote snapshot (best-5 bid/ask + last trade), the
one free, keyless intraday observation endpoint. Free tier, `public` data_class.

**This adapter is single-shot, never a poll/stream, by hard rule.** `fetch()` makes exactly one
upstream GET and returns exactly one snapshot — no internal loop, no timer, no retry-with-
backoff. Callers wanting a fresher read simply call `fetch()` again themselves; nothing in this
module ever calls itself in a loop. This matches the "single explicit check" calling convention
`context/observation.py`'s bounded-observation runner expects from every live-ish source: one
`DataAccessRouter.fetch()` call in, one `SourceResult` out, no background work started.

The upstream response is a delayed snapshot (commonly quoted at several-seconds lag vs the
matching engine) surfaced through the exchange's own market-information-system pages — not a
tick stream. Every result's caveat says so explicitly, honestly, on every call, regardless of
subscription tier (governance: "real-time" is a label, not a guarantee).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import httpx
import structlog

from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_BASE_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"

# TWSE-listed symbols query under the "tse" exchange prefix, OTC/TPEX symbols under "otc" —
# both are served by the same MIS endpoint; this adapter's declared coverage is TW-wide,
# listed + OTC, one snapshot per call.
_TSE_PREFIX = "tse"
_OTC_PREFIX = "otc"

_SHARES_PER_LOT = 1000  # MIS reports volume in lots (張)

_CAVEAT = (
    "TWSE MIS is a delayed, browser-observed snapshot (commonly several seconds behind the "
    "matching engine) — a single bounded observation, never a live tick stream; not a "
    "real-time guarantee at any subscription tier."
)


class TwseMisAdapter:
    """The `TWSE_MIS` source adapter — one bounded intraday snapshot per `fetch()` call."""

    source_id = "TWSE_MIS"
    timing_class = TimingClass.LIVE_ISH

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.PRICE_INTRADAY, Market.TW)}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        if "msgArray" not in raw:
            raise ValueError("TWSE MIS response missing 'msgArray'")
        rows_field = raw["msgArray"]
        if not isinstance(rows_field, list):
            raise ValueError("TWSE MIS 'msgArray' is not a list")
        untyped_rows = cast("list[object]", rows_field)
        for row in untyped_rows:
            if not isinstance(row, dict):
                raise ValueError("TWSE MIS 'msgArray' row is not an object")
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        """Exactly one upstream GET, exactly one snapshot returned — no loop, no polling."""
        if cap is not Capability.PRICE_INTRADAY or sym.market is not Market.TW:
            return _unavailable(cap, sym)

        exchange_prefix = str(params.get("exchange_prefix") or _TSE_PREFIX)
        if exchange_prefix not in (_TSE_PREFIX, _OTC_PREFIX):
            exchange_prefix = _TSE_PREFIX
        ex_ch = f"{exchange_prefix}_{sym.id}.tw"

        try:
            async with build_async_client(tls_policy=tls_policy) as client:
                response = await client.get(_BASE_URL, params={"ex_ch": ex_ch})
                response.raise_for_status()
                body: object = response.json()
        except httpx.HTTPError as exc:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
                ),
            )

        if not isinstance(body, dict):
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNREACHABLE, detail="TWSE MIS response is not an object"
                ),
            )

        try:
            validated = self.validate(cast("dict[str, object]", body))
        except ValueError as exc:
            return _result(
                payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
            )

        rows_field = validated["msgArray"]
        if not isinstance(rows_field, list):  # pragma: no cover — validate() guarantees this
            raise TypeError("validate() contract violated: 'msgArray' is not a list")
        rows = cast("list[dict[str, object]]", rows_field)
        if not rows:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE,
                    detail=f"no snapshot row returned for {sym.id} (market closed or bad code)",
                ),
            )

        match = rows[0]
        snapshot: dict[str, object] = {
            "symbol": sym.id,
            "market": sym.market.value,
            "name": match.get("n"),
            "last_price": _last_trade_price(match) or 0.0,
            "open": _to_float(match.get("o")),
            "high": _to_float(match.get("h")),
            "low": _to_float(match.get("l")),
            "volume": _to_int(match.get("v")),
            "best_bid": match.get("b"),
            "best_ask": match.get("a"),
            # `g`/`f` are the per-tier order VOLUME strings paired with `b`/`a`'s per-tier PRICE
            # strings — same `_`-delimited, same tier order (confirmed live: `b`/`g` and `a`/`f`
            # each have matching tier counts). Without these, a five-tier depth chart has prices
            # but no bar length to size each tier by.
            "best_bid_volume": match.get("g"),
            "best_ask_volume": match.get("f"),
            "observed_at": match.get("t"),
        }
        return _result(
            payload={"snapshot": snapshot}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
        )


# -- watchlist batch (策略盯盤 intraday) -------------------------------------------------------

# One request carries both exchange prefixes for each code (the exchange of a code is not known
# here; the wrong prefix returns an empty stub row, dropped below), so 40 codes is 80 `ex_ch`
# items - well inside what the endpoint accepts in one GET.
_CODES_PER_REQUEST = 40
_INDEX_CODE = "t00"  # 加權指數, asked with the first request so the market filter has a reference


@dataclass(frozen=True)
class MisQuote:
    """One stock's intraday state as the exchange reports it. `last` is `None` before the first
    trade of the day; `volume_shares` is the cumulative day volume (MIS reports lots of 1,000)."""

    symbol: str
    last: float | None
    prev_close: float | None
    open: float | None
    high: float | None
    low: float | None
    volume_shares: int
    trade_date: str  # YYYY-MM-DD
    observed_at: str  # HH:MM:SS, Taipei


@dataclass(frozen=True)
class MisQuotes:
    quotes: tuple[MisQuote, ...]
    index: MisQuote | None
    health: SourceHealth


def _last_trade_price(row: dict[str, object]) -> float | None:
    """The last trade price. Between ticks the top-level `z` is "-" and the price lives in the
    nested `trade` object; before the first trade of the day neither is a number."""
    trade = row.get("trade")
    nested = cast("dict[str, object]", trade).get("z") if isinstance(trade, dict) else None
    for raw in (row.get("z"), nested):
        value = _to_float(raw)
        if value > 0:
            return value
    return None


def _positive(raw: object) -> float | None:
    value = _to_float(raw)
    return value if value > 0 else None


def parse_quote(row: dict[str, object]) -> MisQuote | None:
    """A `MisQuote` from one `msgArray` row, or `None` for the empty stub the endpoint returns for
    a wrong exchange prefix or an unknown code (no previous close)."""
    code, prev_close = row.get("c"), _positive(row.get("y"))
    raw_date = str(row.get("d") or "")
    if not isinstance(code, str) or prev_close is None or len(raw_date) != 8:  # noqa: PLR2004 - YYYYMMDD
        return None
    return MisQuote(
        symbol=code,
        last=_last_trade_price(row),
        prev_close=prev_close,
        open=_positive(row.get("o")),
        high=_positive(row.get("h")),
        low=_positive(row.get("l")),
        volume_shares=_to_int(row.get("v")) * _SHARES_PER_LOT,
        trade_date=f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}",
        observed_at=str(row.get("t") or ""),
    )


async def _get_once_more_on_timeout(
    client: httpx.AsyncClient, params: dict[str, str]
) -> httpx.Response:
    """One GET, repeated once straight away if it times out: the endpoint occasionally stalls on
    a single request, and a slot is only 10 minutes long. No delay, no further attempts."""
    try:
        return await client.get(_BASE_URL, params=params)
    except httpx.TimeoutException:
        return await client.get(_BASE_URL, params=params)


async def fetch_watchlist_quotes(symbols: Sequence[str], *, tls_policy: TLSPolicy) -> MisQuotes:
    """Quotes for `symbols` (listed and OTC) plus the index: a bounded handful of GETs, one per 40
    codes, in order - no loop on a timer, no retry, no stream. A transport error on any request
    makes the whole batch `unreachable` (a half-read watchlist would skew every comparison)."""
    codes = sorted({code for code in symbols if code})
    chunks = [codes[i : i + _CODES_PER_REQUEST] for i in range(0, len(codes), _CODES_PER_REQUEST)]
    quotes: list[MisQuote] = []
    index: MisQuote | None = None
    try:
        async with build_async_client(tls_policy=tls_policy) as client:
            for position, chunk in enumerate(chunks or [[]]):
                ex_ch = [f"{prefix}_{c}.tw" for c in chunk for prefix in (_TSE_PREFIX, _OTC_PREFIX)]
                if position == 0:
                    ex_ch.append(f"{_TSE_PREFIX}_{_INDEX_CODE}.tw")
                response = await _get_once_more_on_timeout(
                    client, {"ex_ch": "|".join(ex_ch), "json": "1", "delay": "0"}
                )
                response.raise_for_status()
                body: object = response.json()
                rows = (
                    cast("dict[str, object]", body).get("msgArray")
                    if isinstance(body, dict)
                    else None
                )
                if not isinstance(rows, list):
                    raise ValueError("TWSE MIS response has no msgArray")
                for raw in cast("list[object]", rows):
                    quote = (
                        parse_quote(cast("dict[str, object]", raw))
                        if isinstance(raw, dict)
                        else None
                    )
                    if quote is None:
                        continue
                    if quote.symbol == _INDEX_CODE:
                        index = quote
                    else:
                        quotes.append(quote)
    except (httpx.HTTPError, ValueError) as exc:
        return MisQuotes(
            quotes=(),
            index=None,
            health=SourceHealth(
                status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
            ),
        )
    if not quotes:
        return MisQuotes(
            quotes=(),
            index=index,
            health=SourceHealth(status=HealthStatus.STALE, detail="no quote returned for the list"),
        )
    return MisQuotes(
        quotes=tuple(quotes), index=index, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


def _to_float(value: object) -> float:
    if value is None or value in ("-", ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0.0


def _to_int(value: object) -> int:
    return int(_to_float(value))


def _unavailable(cap: Capability, sym: Symbol) -> SourceResult:
    return _result(
        payload={},
        health=SourceHealth(
            status=HealthStatus.UNAVAILABLE_TIER,
            detail=f"TWSE_MIS does not serve {cap.value} for {sym.market.value}",
        ),
    )


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=TwseMisAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.LIVE_ISH,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


__all__ = ["MisQuote", "MisQuotes", "TwseMisAdapter", "fetch_watchlist_quotes", "parse_quote"]
