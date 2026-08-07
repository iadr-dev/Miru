"""Opt-in live NCDR closure-feed verification. Never run in the default suite.

Run manually: uv run pytest -m network tests/sources/test_ncdr_closure_network.py
"""

from __future__ import annotations

import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.ncdr_closure import NcdrClosureAdapter

pytestmark = pytest.mark.network


async def test_live_closure_feed() -> None:
    adapter = NcdrClosureAdapter()
    sym = Symbol(id="", market=Market.TW)

    result = await adapter.fetch(Capability.CALENDAR_EVENTS, sym, tls_policy=TLSPolicy.STRICT)

    # OK when at least one alert is currently active, STALE when the feed is empty (no active
    # closures right now) — both are a real, successful fetch. Only UNREACHABLE is a failure.
    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)
    assert isinstance(result.payload.get("closure_dates"), list)
    assert isinstance(result.payload.get("events"), list)
