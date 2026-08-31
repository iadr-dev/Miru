"""TWSE MIS ETF NAV/premium-discount adapter — closes the "NAV/IOPV" gap `sources/etf_pcf.py`'s
own docstring flags as unfilled.

**Whole-market, not per-symbol.** `https://mis.twse.com.tw/stock/data/all_etf.txt` is a free,
keyless, single-request endpoint that returns EVERY listed ETF's premium/discount snapshot in
one call (357 rows across 24 issuer groups, confirmed live) — the genuine ingest-layer
whole-market carve-out this dataset needs, mirroring `FinMindAdapter.fetch_whole_market`'s shape
even though this is a different upstream with no such method of its own.

**Field mapping** (confirmed live against the page's own rendered column headers AND by
cross-checking 0050's `e` value against our own ingested `TaiwanStockPrice` close for the same
date — not guessed): `a`=symbol, `b`=fund name, `c`=已發行受益權單位數 (units outstanding),
`d`=與前日已發行受益單位差異數 (day-over-day unit change), `e`=成交價 (close price),
`f`=投信或總代理人預估淨值 (estimated NAV), `g`=預估折溢價幅度 (TWSE's own published
premium/discount %), `h`=前一營業日單位淨值 (prior-day NAV), `i`=as-of date (`YYYYMMDD`),
`j`=as-of time. `premium_discount_pct` (`g`) is passed through as TWSE's own published figure
rather than recomputed from `e`/`f` here, so this row never silently diverges from what the
source page itself shows.

Every numeric field in the raw response is inconsistently typed as either a JSON number or a
string across the 24 issuer groups (a real upstream quirk, not a bug in this adapter) — every
parse here is defensive against both shapes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import cast

import httpx
import structlog

from schemas.enums import DataClass, Market, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_NAV_URL = "https://mis.twse.com.tw/stock/data/all_etf.txt"

_CAVEAT = (
    "ETF premium/discount snapshot from TWSE MIS's issuer-relayed feed — the estimated NAV and "
    "premium/discount % are provided by each ETF's issuer/total agent, merely relayed by TWSE "
    "(not TWSE's own calculation); provisional/intraday, subject to issuer revision. Never a "
    "trading signal."
)


def _to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _to_int(value: object) -> int | None:
    as_float = _to_float(value)
    return int(as_float) if as_float is not None else None


def _parse_as_of(raw: object) -> date | None:
    """`i` is `YYYYMMDD` — the upstream feed's own as-of date, not `datetime.now()`, so a stale
    cached response is never mistaken for today's snapshot."""
    text = str(raw) if raw is not None else ""
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _row_from_raw(raw: dict[str, object]) -> dict[str, object] | None:
    symbol = raw.get("a")
    if not isinstance(symbol, str) or not symbol:
        return None
    as_of = _parse_as_of(raw.get("i"))
    if as_of is None:
        return None
    return {
        "symbol": symbol,
        "market": Market.TW.value,
        "as_of": as_of.isoformat(),
        "fund_name": str(raw.get("b") or ""),
        "units_outstanding": _to_int(raw.get("c")),
        "units_outstanding_change": _to_int(raw.get("d")),
        "close_price": _to_float(raw.get("e")),
        "estimated_nav": _to_float(raw.get("f")),
        "premium_discount_pct": _to_float(raw.get("g")),
        "prior_day_nav": _to_float(raw.get("h")),
    }


async def fetch_whole_market_etf_nav(*, tls_policy: TLSPolicy) -> SourceResult:
    """One whole-market call, every listed ETF's premium/discount row in one response — the
    ingest layer's sole legitimate caller of this function (mirrors
    `FinMindAdapter.fetch_whole_market`'s own governance framing: never a per-symbol fan-out)."""
    try:
        async with build_async_client(tls_policy=tls_policy) as client:
            response = await client.get(_NAV_URL)
            response.raise_for_status()
            body: object = response.json()
    except httpx.HTTPError as exc:
        return _result(
            rows=[],
            health=SourceHealth(
                status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
            ),
        )

    if not isinstance(body, dict) or "a1" not in body:
        return _result(
            rows=[],
            health=SourceHealth(
                status=HealthStatus.UNREACHABLE, detail="TWSE all_etf.txt response missing 'a1'"
            ),
        )
    typed_body = cast("dict[str, object]", body)

    groups_field = typed_body["a1"]
    if not isinstance(groups_field, list):
        return _result(
            rows=[],
            health=SourceHealth(
                status=HealthStatus.UNREACHABLE, detail="TWSE all_etf.txt 'a1' is not a list"
            ),
        )

    rows: list[dict[str, object]] = []
    for group in cast("list[object]", groups_field):
        if not isinstance(group, dict):
            continue
        typed_group = cast("dict[str, object]", group)
        msg_array = typed_group.get("msgArray")
        if not isinstance(msg_array, list):
            continue
        for raw_row in cast("list[object]", msg_array):
            if not isinstance(raw_row, dict):
                continue
            parsed = _row_from_raw(cast("dict[str, object]", raw_row))
            if parsed is not None:
                rows.append(parsed)

    if not rows:
        return _result(
            rows=[],
            health=SourceHealth(
                status=HealthStatus.STALE, detail="no ETF rows parsed from all_etf.txt"
            ),
        )
    return _result(rows=rows, health=SourceHealth(status=HealthStatus.OK, detail="ok"))


def _result(*, rows: list[dict[str, object]], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id="TWSE_MIS",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.LIVE_ISH,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload={"rows": rows},
        health=health,
    )


__all__ = ["fetch_whole_market_etf_nav"]
