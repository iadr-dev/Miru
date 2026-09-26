"""`get_us_market_rankings` — 熱門美股/市值/營收/ROE/成交量, each backed by a live (mocked here)
Yahoo screener call. One test per `RankingKind`, plus the currency-normalization edge case
(revenue/ROE sort fields report figures in each company's own `financialCurrency`, so a non-USD
reporter must be filtered out before truncating to `top_n` — see the tool module's own docstring
for the live finding this guards against)."""

from __future__ import annotations

import pytest

from schemas.screener import ScreenerAssetKind, ScreenerHit, ScreenerResult
from tools import market_ranking_tools
from tools.base import ToolContext
from tools.market_ranking_tools import (
    GetUsMarketRankingsInput,
    GetUsMarketRankingsTool,
    RankingKind,
)


def _hit(symbol: str, **fields: float | str | bool | None) -> ScreenerHit:
    return ScreenerHit(
        symbol=symbol,
        name=f"{symbol} Inc.",
        exchange="NasdaqGS",
        region="us_market",
        matched_fields=fields,
        rank=None,
        score=None,
    )


async def test_trending_uses_predefined_screener(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_predefined(name: str, size: int) -> ScreenerResult:
        captured["name"] = name
        captured["size"] = size
        return ScreenerResult(
            asset_kind=ScreenerAssetKind.EQUITY,
            total_matches=1,
            hits=(_hit("INTC", regularMarketPrice=30.0, regularMarketChangePercent=-3.4),),
            detail="ok",
        )

    monkeypatch.setattr(
        market_ranking_tools, "_run_predefined_equity_screener", fake_run_predefined
    )
    tool = GetUsMarketRankingsTool()

    output = await tool.run(
        GetUsMarketRankingsInput(kind=RankingKind.TRENDING, top_n=10), ToolContext()
    )

    assert captured["name"] == "most_actives"
    assert output.rows[0].symbol == "INTC"
    assert output.rows[0].price == 30.0
    assert output.rows[0].change_pct == -3.4


@pytest.mark.parametrize(
    ("kind", "expected_sort_field"),
    [
        (RankingKind.MARKET_CAP, "intradaymarketcap"),
        (RankingKind.REVENUE, "totalrevenues.lasttwelvemonths"),
        (RankingKind.ROE, "returnonequity.lasttwelvemonths"),
        (RankingKind.VOLUME, "dayvolume"),
    ],
)
async def test_custom_query_kinds_sort_by_the_right_field(
    monkeypatch: pytest.MonkeyPatch, kind: RankingKind, expected_sort_field: str
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_equity_screener(query: object) -> ScreenerResult:
        captured["query"] = query
        return ScreenerResult(
            asset_kind=ScreenerAssetKind.EQUITY,
            total_matches=1,
            hits=(
                _hit(
                    "AAPL",
                    regularMarketPrice=341.07,
                    regularMarketChange=5.2,
                    regularMarketChangePercent=1.53,
                    regularMarketVolume=29649787.0,
                    financialCurrency="USD",
                ),
            ),
            detail="ok",
        )

    monkeypatch.setattr(market_ranking_tools, "_run_equity_screener", fake_run_equity_screener)
    tool = GetUsMarketRankingsTool()

    output = await tool.run(GetUsMarketRankingsInput(kind=kind, top_n=5), ToolContext())

    assert captured["query"].sort_field == expected_sort_field  # type: ignore[attr-defined]
    assert output.rows[0].symbol == "AAPL"
    assert output.rows[0].price == 341.07
    assert output.rows[0].volume == 29649787.0


async def test_revenue_uses_a_much_higher_market_cap_floor_and_full_fetch_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real bug found live this session: at revenue's original (lower) market-cap floor,
    ZERO USD reporters appeared even within Yahoo's full 250-row screener cap — no
    fetch-more-and-filter multiplier could recover a USD-only top-N. The fix confirmed live: a
    much higher market-cap floor (mega-caps only) shrinks the non-USD noise enough for real USD
    revenue giants to surface, fetched at the full 250-row cap rather than a small multiplier."""
    captured: dict[str, object] = {}

    async def fake_run_equity_screener(query: object) -> ScreenerResult:
        captured["query"] = query
        return ScreenerResult(
            asset_kind=ScreenerAssetKind.EQUITY,
            total_matches=1,
            hits=(_hit("AMZN", regularMarketPrice=249.67, financialCurrency="USD"),),
            detail="ok",
        )

    monkeypatch.setattr(market_ranking_tools, "_run_equity_screener", fake_run_equity_screener)
    tool = GetUsMarketRankingsTool()

    await tool.run(GetUsMarketRankingsInput(kind=RankingKind.REVENUE, top_n=5), ToolContext())

    query = captured["query"]
    assert query.size == 250  # type: ignore[attr-defined]
    market_cap_condition = next(
        c
        for c in query.conditions
        if c.field == "intradaymarketcap"  # type: ignore[attr-defined]
    )
    assert market_cap_condition.value == 50_000_000_000


async def test_revenue_filters_out_non_usd_reporters(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real bug found live this session: Yahoo's revenue/ROE sort fields report each
    company's own reporting currency — a Korean filer (KRW) must not outrank a USD filer just
    because its raw sort-field number happens to be larger in its own currency."""

    async def fake_run_equity_screener(query: object) -> ScreenerResult:
        del query
        return ScreenerResult(
            asset_kind=ScreenerAssetKind.EQUITY,
            total_matches=2,
            hits=(
                _hit("SKHY", regularMarketPrice=191.56, financialCurrency="KRW"),
                _hit("WMT", regularMarketPrice=105.0, financialCurrency="USD"),
            ),
            detail="ok",
        )

    monkeypatch.setattr(market_ranking_tools, "_run_equity_screener", fake_run_equity_screener)
    tool = GetUsMarketRankingsTool()

    output = await tool.run(
        GetUsMarketRankingsInput(kind=RankingKind.REVENUE, top_n=5), ToolContext()
    )

    symbols = [row.symbol for row in output.rows]
    assert "SKHY" not in symbols
    assert symbols == ["WMT"]


async def test_market_cap_and_volume_do_not_filter_by_currency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Market cap and volume are already USD/share-count — confirmed live they need no
    currency filter, unlike revenue/ROE. A non-USD `financialCurrency` row must still survive."""

    async def fake_run_equity_screener(query: object) -> ScreenerResult:
        del query
        return ScreenerResult(
            asset_kind=ScreenerAssetKind.EQUITY,
            total_matches=1,
            hits=(_hit("SKHY", regularMarketPrice=191.56, financialCurrency="KRW"),),
            detail="ok",
        )

    monkeypatch.setattr(market_ranking_tools, "_run_equity_screener", fake_run_equity_screener)
    tool = GetUsMarketRankingsTool()

    output = await tool.run(
        GetUsMarketRankingsInput(kind=RankingKind.MARKET_CAP, top_n=5), ToolContext()
    )

    assert [row.symbol for row in output.rows] == ["SKHY"]


async def test_empty_result_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_equity_screener(query: object) -> ScreenerResult:
        del query
        return ScreenerResult(
            asset_kind=ScreenerAssetKind.EQUITY, total_matches=0, hits=(), detail="no match"
        )

    monkeypatch.setattr(market_ranking_tools, "_run_equity_screener", fake_run_equity_screener)
    tool = GetUsMarketRankingsTool()

    output = await tool.run(
        GetUsMarketRankingsInput(kind=RankingKind.VOLUME, top_n=5), ToolContext()
    )

    assert output.rows == ()
    assert output.detail == "no match"
