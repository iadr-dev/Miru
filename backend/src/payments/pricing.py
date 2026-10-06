"""Display prices for the plan catalog, read from Paddle so no amount is hardcoded or duplicated.

Paddle is the merchant of record and the only source of truth for what a customer pays; this module
only *shows* it. It reads each configured price (`GET /prices`), picks the country override when
the page is for a country Paddle prices separately (e.g. TWD for Taiwan), and returns the amount in
the currency's minor units exactly as Paddle states it — the client formats it. Tax is decided at
checkout from the billing address, so what is shown here is the price before tax.

A failed lookup never breaks the catalog: the plans still list, just without an amount.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import cast

import httpx
import structlog
from pydantic import BaseModel, ConfigDict

from payments.client import PaddleApiError
from payments.plans import PlanInfo

logger = structlog.get_logger(__name__)

PriceLookup = Callable[[list[str]], Awaitable[Sequence[Mapping[str, object]]]]

_TTL_SECONDS = 600.0
_FAILURE_BACKOFF_SECONDS = 30.0


class PlanPrice(BaseModel):
    """A price as Paddle states it: `amount` in the currency's minor units (a string)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: str
    currency_code: str


class PricedPlan(PlanInfo):
    price: PlanPrice | None = None


# (sorted price ids) -> (fetched at, price rows by id). Rows are cached raw; the country override is
# applied per request, so one fetch serves every country.
_cache: dict[tuple[str, ...], tuple[float, dict[str, Mapping[str, object]]]] = {}


# A failed lookup is remembered briefly so an unreachable Paddle isn't hit again by every request,
# and one lock makes concurrent cache misses share a single outbound fetch (the endpoint is public).
_failed_at: dict[tuple[str, ...], float] = {}
_fetch_lock = asyncio.Lock()


def clear_price_cache() -> None:
    _cache.clear()
    _failed_at.clear()


def _as_price(value: object) -> PlanPrice | None:
    if not isinstance(value, Mapping):
        return None
    entry = cast("Mapping[str, object]", value)
    amount, currency = entry.get("amount"), entry.get("currency_code")
    if isinstance(amount, str) and isinstance(currency, str):
        return PlanPrice(amount=amount, currency_code=currency)
    return None


def _pick_price(row: Mapping[str, object], country: str | None) -> PlanPrice | None:
    if country:
        overrides = row.get("unit_price_overrides")
        if isinstance(overrides, list):
            for item in cast("list[object]", overrides):
                if not isinstance(item, Mapping):
                    continue
                entry = cast("Mapping[str, object]", item)
                codes = entry.get("country_codes")
                if isinstance(codes, list) and country.upper() in cast("list[object]", codes):
                    override = _as_price(entry.get("unit_price"))
                    if override is not None:
                        return override
    return _as_price(row.get("unit_price"))


def _fresh(key: tuple[str, ...]) -> dict[str, Mapping[str, object]] | None:
    cached = _cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < _TTL_SECONDS:
        return cached[1]
    return None


async def _rows(price_ids: list[str], lookup: PriceLookup) -> dict[str, Mapping[str, object]]:
    key = tuple(sorted(price_ids))
    hit = _fresh(key)
    if hit is not None:
        return hit
    failed_at = _failed_at.get(key)
    if failed_at is not None and time.monotonic() - failed_at < _FAILURE_BACKOFF_SECONDS:
        return {}
    async with _fetch_lock:
        hit = _fresh(key)  # another request may have filled it while this one waited
        if hit is not None:
            return hit
        try:
            fetched = await lookup(price_ids)
        except (PaddleApiError, httpx.HTTPError):
            # Degrade to "no amount" rather than fail the whole catalog; retried after the backoff.
            logger.warning("paddle_price_lookup_failed")
            _failed_at[key] = time.monotonic()
            return {}
        rows = {str(row["id"]): row for row in fetched if isinstance(row.get("id"), str)}
        _cache[key] = (time.monotonic(), rows)
        _failed_at.pop(key, None)
        return rows


async def priced_plans(
    plans: list[PlanInfo], lookup: PriceLookup | None, *, country: str | None = None
) -> list[PricedPlan]:
    """The catalog with each plan's price attached when Paddle could be asked and answered."""
    rows = await _rows([p.price_id for p in plans], lookup) if plans and lookup else {}
    priced: list[PricedPlan] = []
    for plan in plans:
        row = rows.get(plan.price_id)
        price = _pick_price(row, country) if row is not None else None
        priced.append(PricedPlan(**plan.model_dump(), price=price))
    return priced


__all__ = ["PlanPrice", "PriceLookup", "PricedPlan", "clear_price_cache", "priced_plans"]
