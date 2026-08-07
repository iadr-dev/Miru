"""Opt-in live PTT verification. Never run in the default suite.

Run manually:
    uv run pytest -m network tests/sources/test_ptt_network.py
"""

from __future__ import annotations

import pytest

from schemas.enums import TLSPolicy
from schemas.source import HealthStatus
from sources.ptt import PttAdapter

pytestmark = pytest.mark.network


async def test_live_fetch_stock_board() -> None:
    adapter = PttAdapter()

    result = await adapter.fetch_board(board="Stock", tls_policy=TLSPolicy.STRICT)

    assert result.health.status in {HealthStatus.OK, HealthStatus.STALE}
