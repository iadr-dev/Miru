"""Plan prices come from Paddle as stated (minor units), with the country override applied, cached,
and absent - never guessed - when Paddle can't be asked."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

import httpx
import pytest

from payments.client import PaddleApiError
from payments.plans import BillingInterval, PlanInfo
from payments.pricing import clear_price_cache, priced_plans
from schemas.enums import Tier

_PLANS = [
    PlanInfo(price_id="pri_m", tier=Tier.PREMIUM, interval=BillingInterval.MONTHLY, byok=False),
    PlanInfo(price_id="pri_a", tier=Tier.PREMIUM, interval=BillingInterval.ANNUAL, byok=False),
]
_ROWS: list[Mapping[str, object]] = [
    {
        "id": "pri_m",
        "unit_price": {"amount": "1099", "currency_code": "USD"},
        "unit_price_overrides": [
            {"country_codes": ["TW"], "unit_price": {"amount": "29900", "currency_code": "TWD"}}
        ],
    },
    {"id": "pri_a", "unit_price": {"amount": "10099", "currency_code": "USD"}},
]


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    clear_price_cache()


class _Lookup:
    def __init__(
        self, rows: Sequence[Mapping[str, object]] = _ROWS, error: Exception | None = None
    ):
        self.calls = 0
        self._rows = rows
        self._error = error

    async def __call__(self, price_ids: list[str]) -> Sequence[Mapping[str, object]]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._rows


async def test_base_price_is_shown_without_a_country() -> None:
    out = await priced_plans(_PLANS, _Lookup())
    assert [(p.price.amount, p.price.currency_code) for p in out if p.price] == [
        ("1099", "USD"),
        ("10099", "USD"),
    ]


async def test_a_country_override_wins_where_one_exists() -> None:
    out = await priced_plans(_PLANS, _Lookup(), country="tw")
    monthly, annual = out
    assert monthly.price
    assert (monthly.price.amount, monthly.price.currency_code) == ("29900", "TWD")
    assert annual.price
    assert annual.price.currency_code == "USD"
    other = await priced_plans(_PLANS, _Lookup(), country="JP")
    assert other[0].price
    assert other[0].price.currency_code == "USD"


async def test_prices_are_cached_across_requests_and_countries() -> None:
    lookup = _Lookup()
    await priced_plans(_PLANS, lookup)
    await priced_plans(_PLANS, lookup, country="TW")
    assert lookup.calls == 1


@pytest.mark.parametrize("error", [PaddleApiError("boom"), httpx.ConnectError("down")])
async def test_an_unreachable_paddle_leaves_the_plans_listed_without_prices(
    error: Exception,
) -> None:
    out = await priced_plans(_PLANS, _Lookup(error=error))
    assert [p.price_id for p in out] == ["pri_m", "pri_a"]
    assert all(p.price is None for p in out)


async def test_no_lookup_or_a_missing_or_malformed_row_means_no_price() -> None:
    assert all(p.price is None for p in await priced_plans(_PLANS, None))
    rows: list[Mapping[str, object]] = [{"id": "pri_m", "unit_price": {"amount": 1099}}]
    out = await priced_plans(_PLANS, _Lookup(rows))
    assert all(p.price is None for p in out)  # malformed amount / row absent -> never guessed


async def test_a_failed_lookup_is_not_retried_on_every_request() -> None:
    lookup = _Lookup(error=PaddleApiError("boom"))
    for _ in range(5):
        await priced_plans(_PLANS, lookup)
    assert lookup.calls == 1  # backed off; an unreachable Paddle is not hammered


async def test_concurrent_cache_misses_share_one_outbound_fetch() -> None:
    lookup = _Lookup()
    results = await asyncio.gather(*(priced_plans(_PLANS, lookup) for _ in range(20)))
    assert lookup.calls == 1
    assert all(r[0].price for r in results)
