"""Characterization of `report_tools._load_etf_extras` — the ETF-only facts of a comparison side,
each field `None` when its source does not carry it. Pins the TW branch (holdings info, expense
ratio, premium/discount via the store) and the non-TW branch (top-10 weight, profile)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from schemas.capabilities import EtfNavRow, FundProfileRow
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools import report_tools
from tools.base import ToolContext
from tools.etf_pcf_tools import GetEtfPremiumDiscountOutput
from tools.report_tools import _EtfExtras, _load_etf_extras


def _res(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="stub",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


_RETURNS: dict[str, object] = {
    "returns": {
        "symbol": "X",
        "market": "TW",
        "as_of": "2026-10-01",
        "return_1y_pct": 10.0,
        "return_3y_pct": 30.0,
        "return_5y_pct": 50.0,
    }
}
_TW = Symbol(id="0050", market=Market.TW)
_US = Symbol(id="SPY", market=Market.US)


def _tw_results(
    *, info: bool = True, expense: bool = True, returns: bool = True
) -> dict[Capability, SourceResult]:
    info_payload: dict[str, object] | None = (
        {
            "symbol": "0050",
            "market": "TW",
            "fund_name": "n",
            "fund_type": "t",
            "tracked_index": "TW50",
        }
        if info
        else None
    )
    expense_payload: dict[str, object] | None = (
        {
            "symbol": "0050",
            "market": "TW",
            "management_fee_pct": 0.3,
            "custody_fee_pct": 0.1,
            "total_expense_ratio_pct_approx": 0.4,
        }
        if expense
        else None
    )
    return {
        Capability.ETF_TRAILING_RETURNS: _res(_RETURNS if returns else {}),
        Capability.ETF_HOLDINGS: _res({"info": info_payload}),
        Capability.ETF_EXPENSE_RATIO: _res({"info": expense_payload}),
    }


def _us_results(holdings: object, *, returns: bool = True) -> dict[Capability, SourceResult]:
    return {
        Capability.ETF_TRAILING_RETURNS: _res(_RETURNS if returns else {}),
        Capability.FUND_HOLDINGS: _res({"holdings": holdings}),
    }


def _holdings(weights: list[float]) -> list[dict[str, object]]:
    return [
        {
            "symbol": "SPY",
            "market": "US",
            "holding_symbol": f"H{i}",
            "holding_name": f"H{i}",
            "weight": w,
        }
        for i, w in enumerate(weights)
    ]


def _profile(*, total_assets: float, category: str) -> FundProfileRow:
    return FundProfileRow(
        symbol="SPY",
        market=Market.US,
        as_of=date(2026, 10, 1),
        total_assets=total_assets,
        category=category,
    )


async def test_tw_without_store_has_trailing_info_and_expense_only() -> None:
    router, _ = make_router(_TW, _tw_results())
    out = await _load_etf_extras(router, None, ToolContext(), _TW, None)
    assert out == _EtfExtras(
        tracked="TW50",
        expense_pct=0.4,
        management_fee_pct=0.3,
        custody_fee_pct=0.1,
        return_1y=10.0,
        return_3y=30.0,
        return_5y=50.0,
    )


async def test_tw_with_missing_payloads_leaves_fields_none() -> None:
    router, _ = make_router(_TW, _tw_results(info=False, expense=False, returns=False))
    out = await _load_etf_extras(router, None, ToolContext(), _TW, None)
    assert out == _EtfExtras()


async def test_tw_with_store_adds_aum_and_latest_premium(monkeypatch: pytest.MonkeyPatch) -> None:
    def nav(pct: float) -> EtfNavRow:
        return EtfNavRow(
            symbol="0050",
            market=Market.TW,
            as_of=date(2026, 10, 1),
            fund_name="n",
            premium_discount_pct=pct,
        )

    outputs: list[object] = [
        GetEtfPremiumDiscountOutput(
            symbol="0050", market=Market.TW, current_aum=123.0, rows=(nav(0.1), nav(-0.2))
        ),
        GetEtfPremiumDiscountOutput(symbol="0050", market=Market.TW, current_aum=None, rows=()),
        "not-an-output",
    ]
    seen: list[int] = []

    class _FakeTool:
        def __init__(self, store: object) -> None:
            pass

        async def run(self, args: object, ctx: object) -> object:
            seen.append(args.days)  # type: ignore[attr-defined]
            return outputs[len(seen) - 1]

    monkeypatch.setattr(report_tools, "GetEtfPremiumDiscountTool", _FakeTool)
    router, _ = make_router(_TW, _tw_results())
    store = object()
    first = await _load_etf_extras(router, store, ToolContext(), _TW, None)  # type: ignore[arg-type]
    assert (first.aum, first.premium_pct) == (123.0, -0.2)
    second = await _load_etf_extras(router, store, ToolContext(), _TW, None)  # type: ignore[arg-type]
    assert (second.aum, second.premium_pct) == (None, None)
    third = await _load_etf_extras(router, store, ToolContext(), _TW, None)  # type: ignore[arg-type]
    assert (third.aum, third.premium_pct) == (None, None)
    assert seen == [5, 5, 5]


async def test_non_tw_fraction_weights_scale_to_percent_and_profile_fills_aum() -> None:
    router, _ = make_router(_US, _us_results(_holdings([0.1, 0.05, 0.05])))
    profile = _profile(total_assets=9.0, category="Large Blend")
    out = await _load_etf_extras(router, None, ToolContext(), _US, profile)
    assert out.top10_weight_pct == 20.0
    assert (out.aum, out.tracked) == (9.0, "Large Blend")
    assert (out.return_1y, out.expense_pct, out.premium_pct) == (10.0, None, None)


async def test_non_tw_percent_weights_kept_and_only_top_ten_summed() -> None:
    weights = [10.0] * 10 + [50.0]
    router, _ = make_router(_US, _us_results(_holdings(weights)))
    out = await _load_etf_extras(router, None, ToolContext(), _US, None)
    assert out.top10_weight_pct == 100.0
    assert (out.aum, out.tracked) == (None, None)


async def test_non_tw_fraction_limit_boundary_is_inclusive() -> None:
    router, _ = make_router(_US, _us_results(_holdings([1.5])))
    out = await _load_etf_extras(router, None, ToolContext(), _US, None)
    assert out.top10_weight_pct == 150.0  # 1.5 <= limit -> treated as a fraction


@pytest.mark.parametrize("holdings", [[], None, "x"])
async def test_non_tw_without_holdings_has_no_top10(holdings: object) -> None:
    router, _ = make_router(_US, _us_results(holdings, returns=False))
    out = await _load_etf_extras(router, None, ToolContext(), _US, None)
    assert out == _EtfExtras()
