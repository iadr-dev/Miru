"""Tests for the `ETF_TRAILING_RETURNS` capability domain — recorded/synthetic price series, no
live network. See `trailing_returns.py`'s own module docstring for why this reads a dedicated
split/dividend-adjusted history fetch rather than yfinance's `.info` return fields or this
codebase's ingested (unadjusted) `price_eod` bars."""

from __future__ import annotations

import pandas as pd
import pytest

from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.yfinance import YFinanceAdapter

from ._helpers import patch_run_sync


def _history(closes: list[float]) -> pd.DataFrame:
    index = pd.date_range("2020-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=index)


async def test_fetch_trailing_returns_computes_every_horizon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1300 flat-then-doubling closes: enough rows for all 6 horizons (longest lookback is 1260).
    closes = [100.0] * 1299 + [200.0]
    patch_run_sync(monkeypatch, _fetch_adjusted_history_blocking=_history(closes))
    adapter = YFinanceAdapter()
    sym = Symbol(id="0050", market=Market.TW, yahoo="0050.TW")

    result = await adapter.fetch(Capability.ETF_TRAILING_RETURNS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "YFINANCE"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    returns = result.payload["returns"]
    assert returns["symbol"] == "0050"  # type: ignore[index]
    assert returns["return_1m_pct"] == pytest.approx(100.0)  # type: ignore[index]
    assert returns["return_5y_pct"] == pytest.approx(100.0)  # type: ignore[index]


async def test_fetch_trailing_returns_none_for_horizons_beyond_history_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only 30 closes: 1M (21td) resolves, everything longer (1Q=63td and up) has no data yet —
    # e.g. a fund listed a month ago has no real 1-year return, and must not fabricate one.
    closes = [100.0] * 29 + [110.0]
    patch_run_sync(monkeypatch, _fetch_adjusted_history_blocking=_history(closes))
    adapter = YFinanceAdapter()
    sym = Symbol(id="00981A", market=Market.TW, yahoo="00981A.TW")

    result = await adapter.fetch(Capability.ETF_TRAILING_RETURNS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    returns = result.payload["returns"]
    assert returns["return_1m_pct"] == pytest.approx(10.0)  # type: ignore[index]
    assert returns["return_1q_pct"] is None  # type: ignore[index]
    assert returns["return_5y_pct"] is None  # type: ignore[index]


async def test_fetch_trailing_returns_empty_history_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_run_sync(monkeypatch, _fetch_adjusted_history_blocking=pd.DataFrame())
    adapter = YFinanceAdapter()
    sym = Symbol(id="NOPE", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_TRAILING_RETURNS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_trailing_returns_degrades_honestly_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_error(*_args: object) -> pd.DataFrame:
        raise ConnectionError("boom")

    patch_run_sync(monkeypatch, _fetch_adjusted_history_blocking=raise_error)
    adapter = YFinanceAdapter()
    sym = Symbol(id="0050", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_TRAILING_RETURNS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload == {}


async def test_fetch_unsupported_capability_for_tw_returns_unavailable_tier() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id="0050", market=Market.TW)

    result = await adapter.fetch(Capability.OPTIONS_CHAIN, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
