"""Opt-in live ETF_PCF verification. Never run in the default suite.

Run manually:
    uv run pytest -m network tests/sources/test_etf_pcf_network.py
"""

from __future__ import annotations

import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.etf_pcf import EtfPcfAdapter

pytestmark = pytest.mark.network


async def test_live_fetch_0050() -> None:
    adapter = EtfPcfAdapter()
    sym = Symbol(id="0050", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_HOLDINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status in {HealthStatus.OK, HealthStatus.STALE}
