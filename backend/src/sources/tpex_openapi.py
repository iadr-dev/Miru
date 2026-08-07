"""TPEX OpenAPI adapter — official OTC (上櫃) + emerging (興櫃) market EOD, latest snapshot
only, no auth, but requires a `User-Agent` header (upstream rejects UA-less requests).

Mirrors the TWSE OpenAPI adapter's shape: the upstream endpoint has no query parameters — it
dumps the whole current day's OTC-market EOD table in one response. This adapter still serves
**one symbol per `fetch()` call** — it makes exactly one upstream GET and filters the response
to the requested symbol client-side. There is no history here: FinMind is the backfill engine.

The endpoint path below is confirmed correct against the live server (verified with `curl`: a
real 200 with the full current-day OTC dump). **Known issue — TLS-strict verification fails for
this specific server under Python's `ssl` module / OpenSSL 3.x**: the upstream's certificate
chain is missing a Subject Key Identifier extension, which OpenSSL 3.x's stricter X.509
validation rejects (`CERTIFICATE_VERIFY_FAILED: Missing Subject Key Identifier`) even though
Windows' native TLS stack (and therefore `curl` on Windows) tolerates the same certificate. This
is a real defect in the upstream server's certificate, not a stale local trust store — a fresh
`certifi` bundle reproduces the same rejection. `fetch()` therefore returns `UNREACHABLE` when
called with `TLSPolicy.STRICT` (the default and the correct behavior — this adapter must not
silently downgrade). Note `TLSPolicy.COMPATIBILITY` does **not** fix this: httpx has no
"relaxed chain leniency" knob distinct from full verification on/off, so `compatibility` still
sets `verify=True` and still rejects this certificate — only `TLSPolicy.UNSAFE_EXPLICIT`
(verification fully disabled, loudly logged) would let the request through. This adapter is
unusable in practice until the upstream fixes its certificate, or until this endpoint is
reachable through some other trusted path (e.g. proxied by a source that terminates TLS
correctly). Do not reach for `UNSAFE_EXPLICIT` casually — that trade-off is the caller's
decision to make deliberately, not this adapter's default.
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

_BASE_URL = "https://www.tpex.org.tw/openapi/v1"
_MAINBOARD_DAILY_CLOSE_QUOTES = f"{_BASE_URL}/tpex_mainboard_daily_close_quotes"

# Upstream rejects requests with no User-Agent (the "needs UA header" quirk this family is
# known for) — a plain, honest UA identifying this client, not a browser-spoofing one.
_HEADERS = {"User-Agent": "Miro-Backend/1.0 (+governed TW-stock/ETF context agent)"}

_CAVEAT = (
    "TPEX OpenAPI is the official latest end-of-day OTC/emerging-market snapshot only — no "
    "parameters, no backfill; it reflects the most recent published trading day, not a live "
    "quote."
)


class TpexOpenApiAdapter:
    """The `TPEX_OPENAPI` source adapter."""

    source_id = "TPEX_OPENAPI"
    timing_class = TimingClass.OFFICIAL_EOD

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.PRICE_EOD, Market.TW)}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        if "rows" not in raw:
            raise ValueError("TPEX mainboard daily close quotes response missing 'rows'")
        rows_field = raw["rows"]
        if not isinstance(rows_field, list):
            raise ValueError("TPEX mainboard daily close quotes 'rows' is not a list")
        untyped_rows = cast("list[object]", rows_field)
        for row in untyped_rows:
            if not isinstance(row, dict):
                raise ValueError("TPEX mainboard daily close quotes row is not an object")
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
            async with build_async_client(tls_policy=tls_policy, headers=_HEADERS) as client:
                response = await client.get(_MAINBOARD_DAILY_CLOSE_QUOTES)
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
        match = next((row for row in rows if row.get("SecuritiesCompanyCode") == sym.id), None)
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
            "open": _to_float(match.get("Open")),
            "high": _to_float(match.get("High")),
            "low": _to_float(match.get("Low")),
            "close": _to_float(match.get("Close")),
            "volume": _to_int(match.get("TradingShares")),
            "turnover": _to_float(match.get("TransactionAmount")),
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
            detail=f"TPEX_OPENAPI does not serve {cap.value} for {sym.market.value}",
        ),
    )


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=TpexOpenApiAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


__all__ = ["TpexOpenApiAdapter"]
