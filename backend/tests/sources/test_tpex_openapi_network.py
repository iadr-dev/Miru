"""Opt-in live TPEX OpenAPI verification. Never run in the default suite.

Run manually: uv run pytest -m network tests/sources/test_tpex_openapi_network.py

Formerly known issue (now resolved, re-verified live this session): the upstream server's
certificate was missing a Subject Key Identifier extension, which OpenSSL 3.x's stricter
validation rejected under strict TLS — see `sources.tpex_openapi`'s module docstring for the
historical detail. Re-confirmed live: `tpex_mainboard_daily_close_quotes` and the sibling
`tpex_trading_warning_information`/`tpex_disposal_information` endpoints all now succeed under
`TLSPolicy.STRICT` with no certificate error, so this test asserts a normal successful fetch
again, not the honest-degradation fallback.
"""

from __future__ import annotations

import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.tpex_openapi import (
    TpexOpenApiAdapter,
    fetch_whole_market_disposition_events_tpex,
)

pytestmark = pytest.mark.network


async def test_live_mainboard_daily_close_quotes() -> None:
    adapter = TpexOpenApiAdapter()
    sym = Symbol(id="6547", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)


async def test_live_disposition_events_tpex() -> None:
    result = await fetch_whole_market_disposition_events_tpex(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
