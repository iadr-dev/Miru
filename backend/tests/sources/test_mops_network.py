"""Opt-in live MOPS OpenAPI verification. Never run in the default suite.

Run manually: uv run pytest -m network tests/sources/test_mops_network.py
"""

from __future__ import annotations

import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.mops import MopsAdapter

pytestmark = pytest.mark.network


# Large/mid/small-cap spread. MOPS's two disclosure dumps (t187ap04_L material announcements,
# t187ap11_L director/supervisor shareholding) are published by TWSE for listed companies —
# `4578` (emerging-board/興櫃) is not a TWSE-listed company, so it is legitimately absent from
# both dumps; that absence must show up as graceful degradation (STALE/empty), never a crash.
_LARGE_CAP = "2330"  # TSMC
_MID_CAP = "2385"  # Chicony Electronics
_SMALL_CAP = "8464"  # Nien Made
_EMERGING_OUT_OF_SCOPE = "4578"  # emerging-board — not TWSE-listed, out of scope for this source


@pytest.mark.parametrize("stock_id", [_LARGE_CAP, _MID_CAP, _SMALL_CAP])
async def test_live_material_announcements(stock_id: str) -> None:
    adapter = MopsAdapter()
    sym = Symbol(id=stock_id, market=Market.TW)

    result = await adapter.fetch(Capability.CORPORATE_ACTIONS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)


async def test_live_material_announcements_emerging_board_degrades_gracefully() -> None:
    """`4578` is emerging-board — not a TWSE-listed company — so it is expected to be absent
    from the material-announcements dump. This must be a graceful STALE/empty result, not a
    crash, proving the adapter's scope boundary is handled honestly rather than assumed."""
    adapter = MopsAdapter()
    sym = Symbol(id=_EMERGING_OUT_OF_SCOPE, market=Market.TW)

    result = await adapter.fetch(Capability.CORPORATE_ACTIONS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)


async def test_live_director_shareholding_1101() -> None:
    adapter = MopsAdapter()
    sym = Symbol(id="1101", market=Market.TW)

    result = await adapter.fetch(Capability.SHAREHOLDING, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)


@pytest.mark.parametrize("stock_id", [_LARGE_CAP, _MID_CAP, _SMALL_CAP])
async def test_live_company_profile(stock_id: str) -> None:
    adapter = MopsAdapter()
    sym = Symbol(id=stock_id, market=Market.TW)

    result = await adapter.fetch(Capability.PROFILE, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)
    if result.health.status is HealthStatus.OK:
        profile = result.payload["profile"]
        assert isinstance(profile, dict)
        assert profile["chairman"]
        assert profile["capital"] is not None
