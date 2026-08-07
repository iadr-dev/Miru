"""Opt-in live TWSE MIS verification. Never run in the default suite.

Run manually: uv run pytest -m network tests/sources/test_twse_mis_network.py
"""

from __future__ import annotations

import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.twse_mis import TwseMisAdapter

pytestmark = pytest.mark.network


# Large/mid/small/micro-cap spread. TWSE MIS serves both the "tse" (listed) and "otc" (TPEx)
# exchange prefixes, and `4578` (emerging-board/興櫃) is quoted through TPEx's own market-
# information feed under the same "otc" prefix, so it is in scope here — unlike the TWSE-listed-
# only MOPS/TWSE-OpenAPI adapters.
_LARGE_CAP = "2330"  # TSMC (tse)
_MID_CAP = "2385"  # Chicony Electronics (tse)
_SMALL_CAP = "8464"  # Nien Made (tse)
_MICRO_CAP_EMERGING = "4578"  # emerging-board (otc)

_EXCHANGE_PREFIX = {
    _LARGE_CAP: "tse",
    _MID_CAP: "tse",
    _SMALL_CAP: "tse",
    _MICRO_CAP_EMERGING: "otc",
}


@pytest.mark.parametrize("stock_id", [_LARGE_CAP, _MID_CAP, _SMALL_CAP, _MICRO_CAP_EMERGING])
async def test_live_single_snapshot(stock_id: str) -> None:
    adapter = TwseMisAdapter()
    sym = Symbol(id=stock_id, market=Market.TW)

    result = await adapter.fetch(
        Capability.PRICE_INTRADAY,
        sym,
        tls_policy=TLSPolicy.STRICT,
        exchange_prefix=_EXCHANGE_PREFIX[stock_id],
    )

    # Graceful degradation (STALE, e.g. market closed or a quiet emerging-board code) is
    # correct for every symbol here — never a crash — matching what 2330 alone already proved.
    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)
