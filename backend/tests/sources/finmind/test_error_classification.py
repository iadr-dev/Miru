"""How the adapter classifies FinMind's refusals: a plan-level denial is the only one that is
`UNAVAILABLE_TIER`; a bad key, a ban, the rate limit, a server error and a dead network stay
`UNREACHABLE`; an allowed dataset with no rows on a date is `STALE`, not an error."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import sources.finmind as finmind_module
from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceResult
from sources.finmind import FinMindAdapter

from ._helpers import patch_client, settings
from ._responses import (
    BAD_TOKEN_400,
    IP_BANNED_403,
    NO_DATA_200,
    PLAN_DENIED_400,
    RATE_LIMIT_402,
)


async def _whole_market(monkeypatch: pytest.MonkeyPatch, body: Any, status: int) -> SourceResult:
    patch_client(monkeypatch, body, status_code=status)
    return await FinMindAdapter(settings=settings()).fetch_whole_market(
        Capability.MARKET_VALUE, tls_policy=TLSPolicy.STRICT, start_date="2026-10-20"
    )


async def test_a_dataset_outside_the_plan_is_unavailable_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _whole_market(monkeypatch, PLAN_DENIED_400, 400)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
    assert result.payload == {}


async def test_a_plan_denial_on_the_per_symbol_path_is_unavailable_tier_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_client(monkeypatch, PLAN_DENIED_400, status_code=400)
    result = await FinMindAdapter(settings=settings()).fetch(
        Capability.PRICE_EOD, Symbol(id="2330", market=Market.TW), tls_policy=TLSPolicy.STRICT
    )

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_a_plan_denial_wrapped_in_http_200_is_unavailable_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _whole_market(monkeypatch, {**PLAN_DENIED_400, "data": []}, 200)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


@pytest.mark.parametrize(
    ("body", "status"),
    [
        pytest.param(BAD_TOKEN_400, 400, id="bad-key"),
        pytest.param(IP_BANNED_403, 403, id="ip-banned"),
        pytest.param(RATE_LIMIT_402, 402, id="rate-limit"),
        pytest.param({"msg": "internal error", "status": 500}, 500, id="server-error"),
        pytest.param({"msg": "bad gateway"}, 502, id="gateway-error"),
        pytest.param({"msg": "Parameter error", "status": 400}, 400, id="other-400"),
    ],
)
async def test_every_other_refusal_stays_unreachable(
    monkeypatch: pytest.MonkeyPatch, body: Any, status: int
) -> None:
    result = await _whole_market(monkeypatch, body, status)

    assert result.health.status is HealthStatus.UNREACHABLE


async def test_a_network_error_stays_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(
        finmind_module,
        "build_async_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await FinMindAdapter(settings=settings()).fetch_whole_market(
        Capability.MARKET_VALUE, tls_policy=TLSPolicy.STRICT, start_date="2026-10-20"
    )

    assert result.health.status is HealthStatus.UNREACHABLE


async def test_a_date_with_no_data_is_stale_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await _whole_market(monkeypatch, NO_DATA_200, 200)

    assert result.health.status is HealthStatus.STALE


async def test_the_router_hands_a_tool_the_unavailable_tier_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-symbol read that falls to FinMind keeps working as before: the router returns the
    last attempt, so a tool (and the empty-card handling that treats unavailable-tier as no data)
    sees the plan denial as that health."""
    from dataaccess.router import DataAccessRouter  # noqa: PLC0415

    patch_client(monkeypatch, PLAN_DENIED_400, status_code=400)
    router = DataAccessRouter(
        {"finmind": FinMindAdapter(settings=settings())},
        {"TW": {Capability.PRICE_EOD.value: ["finmind"]}},
    )

    result = await router.fetch(
        Capability.PRICE_EOD, Symbol(id="2330", market=Market.TW), tls_policy=TLSPolicy.STRICT
    )

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
    assert result.payload == {}
