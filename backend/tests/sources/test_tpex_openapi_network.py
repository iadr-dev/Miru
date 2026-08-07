"""Opt-in live TPEX OpenAPI verification. Never run in the default suite.

Run manually: uv run pytest -m network tests/sources/test_tpex_openapi_network.py

Known issue: the live endpoint is confirmed correct (verified via `curl`), but the upstream
server's certificate is missing a Subject Key Identifier extension, which OpenSSL 3.x's
stricter validation rejects under strict TLS — see `sources.tpex_openapi`'s module docstring.
This test asserts the honest-degradation contract (never a fabricated result under strict TLS),
not a successful fetch, until the upstream fixes its certificate.
"""

from __future__ import annotations

import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.tpex_openapi import TpexOpenApiAdapter

pytestmark = pytest.mark.network


async def test_live_mainboard_daily_close_quotes_degrades_honestly_under_strict_tls() -> None:
    adapter = TpexOpenApiAdapter()
    sym = Symbol(id="6547", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    # Flip this back to `assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)`
    # once the upstream's certificate is fixed (missing Subject Key Identifier extension).
    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload == {}
