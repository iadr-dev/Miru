from __future__ import annotations

from datetime import UTC, datetime

from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tools.base import ToolContext, dispatch_tool
from tools.market_index_strip_tools import GetMarketIndexStripInput, GetMarketIndexStripTool


def _result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


class _BySymbolStubAdapter:
    """Unlike `tests.tools.conftest.StubAdapter` (keyed only by capability), this tool fetches
    `EXCHANGE_RATE`/`CRUDE_OIL_PRICES` twice each with different `id`s (USD/JPY, WTI/Brent) and
    expects a different result per call — so the stub here is keyed by `(capability, sym.id)`."""

    def __init__(self, results: dict[tuple[Capability, str], SourceResult]) -> None:
        self.source_id = "STUB"
        self.timing_class = TimingClass.OFFICIAL_EOD
        self._results = results

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(cap, Market.TW) for cap, _ in self._results} | {
            (cap, Market.GLOBAL) for cap, _ in self._results
        }

    async def fetch(
        self, cap: Capability, sym: Symbol, *, tls_policy: TLSPolicy, **params: object
    ) -> SourceResult:
        del tls_policy, params
        return self._results.get((cap, sym.id), _result({"rows": []}))

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        return raw


def _router(results: dict[tuple[Capability, str], SourceResult]) -> DataAccessRouter:
    adapter = _BySymbolStubAdapter(results)
    caps = {cap.value for cap, _ in results}
    priority = {
        "TW": {cap: ["STUB"] for cap in caps},
        "GLOBAL": {cap: ["STUB"] for cap in caps},
    }
    return DataAccessRouter({"STUB": adapter}, priority_config=priority)


async def test_get_market_index_strip_is_public_and_computes_change() -> None:
    results = {
        (Capability.TAIEX_5S_INDEX, ""): _result(
            {
                "rows": [
                    {"market": "TW", "trade_date": "2026-09-03", "taiex": 24000.0},
                    {"market": "TW", "trade_date": "2026-09-04", "taiex": 24240.0},
                ]
            }
        ),
        (Capability.TOTAL_RETURN_INDEX, "TPEx"): _result(
            {
                "rows": [
                    {"symbol": "TPEx", "market": "TW", "trade_date": "2026-09-03", "price": 300.0},
                    {"symbol": "TPEx", "market": "TW", "trade_date": "2026-09-04", "price": 297.0},
                ]
            }
        ),
        (Capability.EXCHANGE_RATE, "USD"): _result(
            {
                "rows": [
                    {
                        "symbol": "USD",
                        "market": "TW",
                        "trade_date": "2026-09-04",
                        "cash_buy": 31.0,
                        "cash_sell": 31.5,
                        "spot_buy": 31.2,
                        "spot_sell": 31.3,
                    }
                ]
            }
        ),
        (Capability.EXCHANGE_RATE, "JPY"): _result(
            {
                "rows": [
                    {
                        "symbol": "JPY",
                        "market": "TW",
                        "trade_date": "2026-09-03",
                        "cash_buy": 0.19,
                        "cash_sell": 0.2,
                        "spot_buy": 0.2,
                        "spot_sell": 0.204,
                    },
                    {
                        "symbol": "JPY",
                        "market": "TW",
                        "trade_date": "2026-09-04",
                        "cash_buy": 0.193,
                        "cash_sell": 0.206,
                        "spot_buy": 0.2003,
                        "spot_sell": 0.2043,
                    },
                ]
            }
        ),
        (Capability.GOLD_PRICE, ""): _result({"rows": []}),
        (Capability.CRUDE_OIL_PRICES, "WTI"): _result(
            {
                "rows": [
                    {"symbol": "WTI", "market": "GLOBAL", "date": "2026-08-28", "price": 70.0},
                    {"symbol": "WTI", "market": "GLOBAL", "date": "2026-08-29", "price": 71.5},
                ]
            }
        ),
        (Capability.CRUDE_OIL_PRICES, "Brent"): _result(
            {
                "rows": [
                    {"symbol": "Brent", "market": "GLOBAL", "date": "2026-08-28", "price": 74.0},
                    {"symbol": "Brent", "market": "GLOBAL", "date": "2026-08-29", "price": 73.0},
                ]
            }
        ),
    }
    tool = GetMarketIndexStripTool(_router(results))
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(GetMarketIndexStripInput(), ToolContext(tier=Tier.FREE))

    by_key = {row.key: row for row in output.rows}
    assert by_key["taiex"].value == 24240.0
    assert by_key["taiex"].change == 240.0
    assert by_key["otc"].value == 297.0
    assert by_key["otc"].change == -3.0
    assert by_key["usd_twd"].value == 31.2
    assert by_key["usd_twd"].change is None  # only one row — no prior to diff
    assert by_key["jpy_twd"].value == 0.2003
    assert round(by_key["jpy_twd"].change, 4) == 0.0003
    assert "gold" not in by_key  # empty rows -> omitted, never fabricated
    assert by_key["crude_oil_wti"].value == 71.5
    assert by_key["crude_oil_wti"].change == 1.5
    assert by_key["crude_oil_brent"].value == 73.0
    assert by_key["crude_oil_brent"].change == -1.0


def _series(prices: list[float], key: str = "price", **extra: object) -> SourceResult:
    return _result(
        {
            "rows": [
                {"symbol": "S", "market": "TW", "trade_date": f"2026-09-0{i + 1}", key: p, **extra}
                for i, p in enumerate(prices)
            ]
        }
    )


async def test_strip_item_order_labels_as_of_and_change_pct_are_pinned() -> None:
    fx = {"cash_buy": 1.0, "cash_sell": 1.0, "spot_sell": 1.0}
    results = {
        (Capability.TAIEX_5S_INDEX, ""): _result(
            {
                "rows": [
                    {"market": "TW", "trade_date": "2026-09-03", "taiex": 200.0},
                    {"market": "TW", "trade_date": "2026-09-04", "taiex": 210.0},
                ]
            }
        ),
        (Capability.TOTAL_RETURN_INDEX, "TPEx"): _series([0.0, 5.0]),  # zero prior: no pct
        (Capability.EXCHANGE_RATE, "USD"): _series([30.0, 31.5], "spot_buy", **fx),
        (Capability.EXCHANGE_RATE, "JPY"): _series([0.2], "spot_buy", **fx),
        (Capability.GOLD_PRICE, ""): _result(
            {
                "rows": [
                    {"market": "GLOBAL", "date": "2026-08-28", "price": 100.0},
                    {"market": "GLOBAL", "date": "2026-08-29", "price": 90.0},
                ]
            }
        ),
        (Capability.CRUDE_OIL_PRICES, "WTI"): _result(
            {"rows": [{"symbol": "WTI", "market": "GLOBAL", "date": "2026-08-29", "price": 71.5}]}
        ),
        (Capability.CRUDE_OIL_PRICES, "Brent"): _result({"rows": []}),
    }

    output = await GetMarketIndexStripTool(_router(results)).run(
        GetMarketIndexStripInput(), ToolContext(tier=Tier.FREE)
    )

    got = [
        (r.key, r.label, r.value, r.change, r.change_pct, r.as_of.isoformat()) for r in output.rows
    ]
    assert got == [
        ("taiex", "加權指數", 210.0, 10.0, 10.0 / 200.0 * 100, "2026-09-04"),
        ("otc", "櫃買指數", 5.0, 5.0, None, "2026-09-02"),
        ("usd_twd", "美元/台幣", 31.5, 1.5, 1.5 / 30.0 * 100, "2026-09-02"),
        ("jpy_twd", "日圓/台幣", 0.2, None, None, "2026-09-01"),
        ("gold", "黃金", 90.0, -10.0, -10.0, "2026-08-29"),
        ("crude_oil_wti", "原油 WTI", 71.5, None, None, "2026-08-29"),
    ]


async def test_get_market_index_strip_omits_every_item_when_all_rows_are_empty() -> None:
    tool = GetMarketIndexStripTool(_router({}))

    output = await tool.run(GetMarketIndexStripInput(), ToolContext(tier=Tier.FREE))

    assert output.rows == ()


async def test_get_market_index_strip_masked_teaser_never_applies() -> None:
    """`DataClass.PUBLIC` — `dispatch_tool` always runs it for real, never a masked teaser,
    even for an unentitled `Tier.FREE` caller."""
    tool = GetMarketIndexStripTool(_router({}))

    result = await dispatch_tool(tool, GetMarketIndexStripInput(), ToolContext(tier=Tier.FREE))

    assert not hasattr(result, "locked")
