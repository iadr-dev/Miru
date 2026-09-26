"""Tests for the screener carve-out (`run_screener`) — query-shaped, not a `fetch()`
capability. Recorded fixtures, no live network."""

from __future__ import annotations

import anyio.to_thread
import pytest

from schemas.screener import (
    ScreenerAssetKind,
    ScreenerCondition,
    ScreenerOperator,
    ScreenerQuery,
)
from sources.yfinance import run_predefined_screener, run_screener
from sources.yfinance.screener import _build_query


def _patch_screen(monkeypatch: pytest.MonkeyPatch, response: dict[str, object]) -> None:
    async def fake_run_sync(fn: object, *args: object) -> dict[str, object]:
        assert getattr(fn, "__name__", "") == "_run_screener_blocking"
        return response

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)


async def test_run_screener_equity_real_match(monkeypatch: pytest.MonkeyPatch) -> None:
    response = {
        "total": 1,
        "quotes": [
            {
                "symbol": "MSFT",
                "shortName": "Microsoft Corporation",
                "longName": "Microsoft Corporation",
                "fullExchangeName": "NasdaqGS",
                "market": "us_market",
                "marketCap": 3_621_067_423_744.0,
                "sector": "Technology",
            }
        ],
    }
    _patch_screen(monkeypatch, response)
    query = ScreenerQuery(
        asset_kind=ScreenerAssetKind.EQUITY,
        conditions=(
            ScreenerCondition(
                field="intradaymarketcap", op=ScreenerOperator.GT, value=10_000_000_000
            ),
            ScreenerCondition(field="sector", op=ScreenerOperator.EQ, value="Technology"),
        ),
    )

    result = await run_screener(query)

    assert result.detail == "ok"
    assert result.total_matches == 1
    assert result.hits[0].symbol == "MSFT"
    assert result.hits[0].name == "Microsoft Corporation"
    assert result.hits[0].exchange == "NasdaqGS"
    assert result.hits[0].matched_fields["sector"] == "Technology"


async def test_run_screener_empty_match_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_screen(monkeypatch, {"total": 0, "quotes": []})
    query = ScreenerQuery(
        asset_kind=ScreenerAssetKind.EQUITY,
        conditions=(
            ScreenerCondition(
                field="intradaymarketcap", op=ScreenerOperator.GT, value=999_999_999_999_999
            ),
        ),
    )

    result = await run_screener(query)

    assert result.total_matches == 0
    assert result.hits == ()
    assert "no symbols matched" in result.detail
    # Never fabricate a screener result on empty/failed query.
    assert result.detail != ""


async def test_run_screener_degrades_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    async def raise_run_sync(fn: object, *args: object) -> dict[str, object]:
        raise ValueError("Invalid field for EquityQuery 'not_a_real_field'")

    monkeypatch.setattr(anyio.to_thread, "run_sync", raise_run_sync)
    query = ScreenerQuery(
        asset_kind=ScreenerAssetKind.EQUITY,
        conditions=(ScreenerCondition(field="not_a_real_field", op=ScreenerOperator.GT, value=1),),
    )

    result = await run_screener(query)

    # An operator/field mismatch degrades honestly, never raises out of run_screener.
    assert result.total_matches == 0
    assert result.hits == ()
    assert "not_a_real_field" in result.detail


async def test_run_screener_etf_between_operator_maps_to_btwn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator-mapping edge case: our `between` maps onto yfinance's `btwn`, and takes both
    `value`/`value2` as the low/high bound — asserted by inspecting the built query tree rather
    than the (irrelevant here) screen response body."""
    captured: dict[str, object] = {}

    def fake_run_sync_blocking(
        asset_kind: ScreenerAssetKind, q: ScreenerQuery
    ) -> dict[str, object]:
        built = _build_query(asset_kind, q)
        captured["operator"] = built.operator
        captured["operands"] = built.operands
        return {"total": 0, "quotes": []}

    async def fake_run_sync(fn: object, *args: object) -> dict[str, object]:
        return fake_run_sync_blocking(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)
    query = ScreenerQuery(
        asset_kind=ScreenerAssetKind.ETF,
        conditions=(
            ScreenerCondition(
                field="fundnetassets",
                op=ScreenerOperator.BETWEEN,
                value=1_000_000,
                value2=1_000_000_000,
            ),
        ),
    )

    await run_screener(query)

    assert captured["operator"] == "BTWN"
    assert captured["operands"] == ["fundnetassets", 1_000_000, 1_000_000_000]


def _patch_predefined_screen(monkeypatch: pytest.MonkeyPatch, response: dict[str, object]) -> None:
    async def fake_run_sync(fn: object, *args: object) -> dict[str, object]:
        assert getattr(fn, "__name__", "") == "_run_predefined_screener_blocking"
        return response

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)


async def test_run_predefined_screener_real_match(monkeypatch: pytest.MonkeyPatch) -> None:
    response = {
        "total": 1,
        "quotes": [
            {
                "symbol": "INTC",
                "shortName": "Intel Corporation",
                "longName": "Intel Corporation",
                "fullExchangeName": "NasdaqGS",
                "regularMarketVolume": 96982345.0,
                "regularMarketChangePercent": -3.44611,
            }
        ],
    }
    _patch_predefined_screen(monkeypatch, response)

    result = await run_predefined_screener("most_actives", 10)

    assert result.detail == "ok"
    assert result.asset_kind == ScreenerAssetKind.EQUITY
    assert result.hits[0].symbol == "INTC"
    assert result.hits[0].matched_fields["regularMarketVolume"] == 96982345.0


async def test_run_predefined_screener_empty_match_is_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_predefined_screen(monkeypatch, {"total": 0, "quotes": []})

    result = await run_predefined_screener("most_actives", 10)

    assert result.total_matches == 0
    assert result.hits == ()
    assert "most_actives" in result.detail


async def test_run_predefined_screener_degrades_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raise_run_sync(fn: object, *args: object) -> dict[str, object]:
        raise ValueError("unknown predefined screen 'not_a_real_screen'")

    monkeypatch.setattr(anyio.to_thread, "run_sync", raise_run_sync)

    result = await run_predefined_screener("not_a_real_screen", 10)

    assert result.total_matches == 0
    assert result.hits == ()
    assert "not_a_real_screen" in result.detail
