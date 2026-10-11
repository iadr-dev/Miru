"""TAIFEX OpenAPI adapter — official derivatives (futures/options) EOD, latest snapshot only,
no auth, no params.

Same shape as the TWSE/TPEX OpenAPI adapters: the upstream endpoint has no query parameters —
it dumps the whole current day's futures daily-market-report table in one response. This
adapter still serves **one contract per `fetch()` call** — it makes exactly one upstream GET and
filters the response to the requested contract code client-side. There is no history here:
FinMind's `TaiwanFuturesDaily`/`TaiwanFutOptDailyInfo` datasets are the backfill engine for this
family.

There is no `Capability` enum value for a generic "derivatives EOD price/volume" bar — the
closest existing entry is `DERIVATIVES_OI` (open interest), which this endpoint's daily report
also carries per contract, so that is the capability declared here. A more precise
`derivatives_eod` capability may be worth adding later if the derivatives surface grows past
this one adapter; that decision is deliberately left to whoever builds that surface out, not
invented here.

The literal endpoint path below follows this source family's documented `snake_case` naming
convention for its "daily market report, futures" dataset; it should be re-verified against the
live `openapi.taifex.com.tw/swagger.json` spec before production use, per this adapter's
operator-preflight test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import httpx
import structlog

from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.parse import to_float
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_BASE_URL = "https://openapi.taifex.com.tw/v1"
_DAILY_MARKET_REPORT_FUT = f"{_BASE_URL}/DailyMarketReportFut"

_CAVEAT = (
    "TAIFEX OpenAPI is the official latest end-of-day derivatives snapshot only — no "
    "parameters, no backfill; it reflects the most recent published trading session, not a "
    "live quote."
)


class TaifexOpenApiAdapter:
    """The `TAIFEX_OPENAPI` source adapter."""

    source_id = "TAIFEX_OPENAPI"
    timing_class = TimingClass.OFFICIAL_EOD

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.DERIVATIVES_OI, Market.TW)}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        if "rows" not in raw:
            raise ValueError("TAIFEX DailyMarketReportFut response missing 'rows'")
        rows_field = raw["rows"]
        if not isinstance(rows_field, list):
            raise ValueError("TAIFEX DailyMarketReportFut 'rows' is not a list")
        untyped_rows = cast("list[object]", rows_field)
        for row in untyped_rows:
            if not isinstance(row, dict):
                raise ValueError("TAIFEX DailyMarketReportFut row is not an object")
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        if cap is not Capability.DERIVATIVES_OI or sym.market is not Market.TW:
            return _unavailable(cap, sym)

        try:
            async with build_async_client(tls_policy=tls_policy) as client:
                response = await client.get(_DAILY_MARKET_REPORT_FUT)
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
        match = next((row for row in rows if row.get("ContractCode") == sym.id), None)
        if match is None:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE,
                    detail=f"contract {sym.id} not found in today's dump",
                ),
            )

        record: dict[str, object] = {
            "symbol": sym.id,
            "market": sym.market.value,
            "trade_date": match.get("Date"),
            "contract_month": match.get("ContractMonth"),
            "settlement_price": to_float(match.get("SettlementPrice")),
            "volume": _to_int(match.get("Volume")),
            "open_interest": _to_int(match.get("OpenInterest")),
        }
        return _result(
            payload={"record": record}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
        )


def _to_int(value: object) -> int:
    return int(to_float(value))


def _unavailable(cap: Capability, sym: Symbol) -> SourceResult:
    return _result(
        payload={},
        health=SourceHealth(
            status=HealthStatus.UNAVAILABLE_TIER,
            detail=f"TAIFEX_OPENAPI does not serve {cap.value} for {sym.market.value}",
        ),
    )


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=TaifexOpenApiAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


__all__ = ["TaifexOpenApiAdapter"]
