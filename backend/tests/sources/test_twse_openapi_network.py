"""Opt-in live TWSE OpenAPI verification. Never run in the default suite.

Run manually: uv run pytest -m network tests/sources/test_twse_openapi_network.py
"""

from __future__ import annotations

import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.twse_openapi import (
    TwseOpenApiAdapter,
    fetch_whole_market_attention_stocks,
    fetch_whole_market_disposition_events,
)

pytestmark = pytest.mark.network


# Large/mid/small-cap spread. `STOCK_DAY_ALL` is TWSE-listed-market-only — `4578` (emerging-
# board/興櫃) is not TWSE-listed and is legitimately absent from this dump, so it is exercised
# as its own graceful-degradation case below rather than forced through the same "has real
# data" assertion as the listed-market symbols.
_LARGE_CAP = "2330"  # TSMC
_MID_CAP = "2385"  # Chicony Electronics
_SMALL_CAP = "8464"  # Nien Made
_EMERGING_OUT_OF_SCOPE = "4578"  # emerging-board — not TWSE-listed, out of scope for this source


@pytest.mark.parametrize("stock_id", [_LARGE_CAP, _MID_CAP, _SMALL_CAP])
async def test_live_stock_day_all(stock_id: str) -> None:
    adapter = TwseOpenApiAdapter()
    sym = Symbol(id=stock_id, market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)


async def test_live_stock_day_all_emerging_board_degrades_gracefully() -> None:
    """`4578` is emerging-board — not TWSE-listed — so absence from `STOCK_DAY_ALL` is the
    correct, expected outcome (STALE), not a bug; this proves the adapter degrades gracefully
    rather than crashing on a symbol outside its listed-market scope."""
    adapter = TwseOpenApiAdapter()
    sym = Symbol(id=_EMERGING_OUT_OF_SCOPE, market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)


async def test_live_attention_stocks() -> None:
    result = await fetch_whole_market_attention_stocks(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK


async def test_live_disposition_events() -> None:
    result = await fetch_whole_market_disposition_events(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
