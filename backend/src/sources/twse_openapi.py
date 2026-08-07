"""TWSE OpenAPI adapter — official listed-market EOD, latest snapshot only, no auth, no params.

The upstream endpoint (`STOCK_DAY_ALL`) has no query parameters — it dumps the whole current
day's listed-market EOD table in one response. This adapter still serves **one symbol per
`fetch()` call** (never a caller-facing whole-market method) — it makes exactly one upstream
GET and filters the response to the requested symbol client-side. There is no history: the
FinMind adapter is the backfill engine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import httpx
import structlog

from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_BASE_URL = "https://openapi.twse.com.tw/v1"
_STOCK_DAY_ALL = f"{_BASE_URL}/exchangeReport/STOCK_DAY_ALL"

_CAVEAT = (
    "TWSE OpenAPI is the official latest end-of-day snapshot only — no parameters, no "
    "backfill; it reflects the most recent published trading day, not a live quote."
)


class TwseOpenApiAdapter:
    """The `TWSE_OPENAPI` source adapter."""

    source_id = "TWSE_OPENAPI"
    timing_class = TimingClass.OFFICIAL_EOD

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.PRICE_EOD, Market.TW)}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        if "rows" not in raw:
            raise ValueError("TWSE STOCK_DAY_ALL response missing 'rows'")
        rows_field = raw["rows"]
        if not isinstance(rows_field, list):
            raise ValueError("TWSE STOCK_DAY_ALL 'rows' is not a list")
        untyped_rows = cast("list[object]", rows_field)
        for row in untyped_rows:
            if not isinstance(row, dict):
                raise ValueError("TWSE STOCK_DAY_ALL row is not an object")
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        if cap is not Capability.PRICE_EOD or sym.market is not Market.TW:
            return _unavailable(cap, sym)

        try:
            async with build_async_client(tls_policy=tls_policy) as client:
                response = await client.get(_STOCK_DAY_ALL)
                response.raise_for_status()
                body: object = response.json()
        except httpx.HTTPError as exc:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
                ),
            )

        try:
            validated = self.validate({"rows": body})
        except ValueError as exc:
            return _result(
                payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
            )

        rows_field = validated["rows"]
        if not isinstance(rows_field, list):  # pragma: no cover — validate() guarantees this
            raise TypeError("validate() contract violated: 'rows' is not a list")
        rows = cast("list[dict[str, object]]", rows_field)
        match = next((row for row in rows if row.get("Code") == sym.id), None)
        if match is None:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE, detail=f"symbol {sym.id} not found in today's dump"
                ),
            )

        bar: dict[str, object] = {
            "symbol": sym.id,
            "market": sym.market.value,
            "trade_date": match.get("Date"),
            "open": _to_float(match.get("OpeningPrice")),
            "high": _to_float(match.get("HighestPrice")),
            "low": _to_float(match.get("LowestPrice")),
            "close": _to_float(match.get("ClosingPrice")),
            "volume": _to_int(match.get("TradeVolume")),
            "turnover": _to_float(match.get("TradeValue")),
        }
        return _result(
            payload={"bar": bar}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
        )


def _to_float(value: object) -> float:
    if value is None:
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
            detail=f"TWSE_OPENAPI does not serve {cap.value} for {sym.market.value}",
        ),
    )


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=TwseOpenApiAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


__all__ = ["TwseOpenApiAdapter"]
