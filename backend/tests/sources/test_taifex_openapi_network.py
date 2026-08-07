"""Opt-in live TAIFEX OpenAPI verification. Never run in the default suite.

Run manually: uv run pytest -m network tests/sources/test_taifex_openapi_network.py
"""

from __future__ import annotations

import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.taifex_openapi import TaifexOpenApiAdapter

pytestmark = pytest.mark.network


async def test_live_daily_market_report_fut() -> None:
    adapter = TaifexOpenApiAdapter()
    sym = Symbol(id="TX", market=Market.TW)

    result = await adapter.fetch(Capability.DERIVATIVES_OI, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)
