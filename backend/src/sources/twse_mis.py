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
            "last_price": _to_float(match.get("z")),
            "open": _to_float(match.get("o")),
            "high": _to_float(match.get("h")),
            "low": _to_float(match.get("l")),
            "volume": _to_int(match.get("v")),
            "best_bid": match.get("b"),
            "best_ask": match.get("a"),
            "observed_at": match.get("t"),
        }
        return _result(
            payload={"snapshot": snapshot}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
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


__all__ = ["TwseMisAdapter"]
