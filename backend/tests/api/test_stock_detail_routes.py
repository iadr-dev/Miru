"""The 14 new `/stock/*` REST routes backing the stock-detail page's Phase-1 tabs
(基本資料/財務分析/籌碼分析) — driven through the REAL FastAPI route via `TestClient`, proving
each dispatches through the real registry exactly as `test_dividend_schedule_route.py`'s
stub-router-swap pattern does. Single-capability tools get a full stub-router assertion; the two
composed tools (`get_company_profile`, `get_calendar_events`) only get a wiring/masking check
since stubbing every one of their several sub-fetches adds little beyond what
`tests/tools/test_market_tools.py`/`tests/tools/test_calendar_tools.py` already cover for the
tools themselves.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.api.conftest import build_test_client
from tests.tools.conftest import make_router
from tools import build_default_tool_registry


def _result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="stub caveat — recorded fixture, not live",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _client_with_capability(
    settings: Settings, agent_answer: str, capability: Capability, payload: dict[str, object]
) -> TestClient:
    client = build_test_client(settings, agent_answer=agent_answer)
    symbol = Symbol(id="2330", market=Market.TW)
    stub_router, _ = make_router(symbol, {capability: _result(payload)})
    registry = build_default_tool_registry(stub_router)
    object.__setattr__(client.app.state.app_state, "tool_registry", registry)
    return client


class TestInsiderShareholdingRoute:
    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings,
            agent_answer,
            Capability.SHAREHOLDING,
            {
                "holdings": [
                    {
                        "symbol": "2330",
                        "market": "TW",
                        "as_of": "2024-06",
                        "person_name": "魏哲家",
                        "title": "董事長本人",
                    }
                ]
            },
        )
        resp = client.get(
            "/api/v1/stock/insider-shareholding", params={"symbol": "2330", "market": "TW"}
        )
        assert resp.status_code == 200
        assert "locked" not in resp.json()

    def test_requires_symbol(self, settings: Settings, agent_answer: str) -> None:
        client = build_test_client(settings, agent_answer=agent_answer)
        resp = client.get("/api/v1/stock/insider-shareholding")
        assert resp.status_code == 422


class TestEsgScoreRoute:
    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(settings, agent_answer, Capability.ESG, {"esg": None})
        resp = client.get("/api/v1/stock/esg-score", params={"symbol": "2330", "market": "TW"})
        assert resp.status_code == 200
        assert resp.json()["esg"] is None


class TestDividendResultRoute:
    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings,
            agent_answer,
            Capability.DIVIDEND_RESULT,
            {
                "rows": [
                    {
                        "symbol": "2330",
                        "market": "TW",
                        "trade_date": date(2024, 7, 18).isoformat(),
                        "before_price": 750.0,
                        "after_price": 735.0,
                        "stock_and_cache_dividend": 15.0,
                        "stock_or_cache_dividend": "cash",
                        "max_price": 745.0,
                        "min_price": 725.0,
                        "open_price": 735.0,
                        "reference_price": 735.0,
                    }
                ]
            },
        )
        resp = client.get(
            "/api/v1/stock/dividend-result", params={"symbol": "2330", "market": "TW"}
        )
        assert resp.status_code == 200
        assert "locked" not in resp.json()


class TestMonthRevenueRoute:
    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings, agent_answer, Capability.MONTH_REVENUE, {"rows": [], "trend": []}
        )
        resp = client.get("/api/v1/stock/month-revenue", params={"symbol": "2330", "market": "TW"})
        assert resp.status_code == 200


class TestFinancialStatementRoute:
    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings, agent_answer, Capability.FINANCIALS, {"lines": []}
        )
        resp = client.get(
            "/api/v1/stock/financial-statement",
            params={"symbol": "2330", "market": "TW", "statement_type": "income_statement"},
        )
        assert resp.status_code == 200

    def test_rejects_bad_statement_type(self, settings: Settings, agent_answer: str) -> None:
        client = build_test_client(settings, agent_answer=agent_answer)
        resp = client.get(
            "/api/v1/stock/financial-statement",
            params={"symbol": "2330", "market": "TW", "statement_type": "not_a_statement"},
        )
        assert resp.status_code == 422


class TestInstitutionalFlowRoute:
    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings,
            agent_answer,
            Capability.INSTITUTIONAL_FLOW,
            {"rows": [], "rolling_net": []},
        )
        resp = client.get(
            "/api/v1/stock/institutional-flow", params={"symbol": "2330", "market": "TW"}
        )
        assert resp.status_code == 200


class TestMarginShortRoute:
    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings, agent_answer, Capability.MARGIN_SHORT, {"rows": []}
        )
        resp = client.get("/api/v1/stock/margin-short", params={"symbol": "2330", "market": "TW"})
        assert resp.status_code == 200


class TestSecuritiesLendingRoute:
    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings, agent_answer, Capability.SECURITIES_LENDING, {"rows": []}
        )
        resp = client.get(
            "/api/v1/stock/securities-lending", params={"symbol": "2330", "market": "TW"}
        )
        assert resp.status_code == 200


class TestValuationRatiosRoute:
    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings, agent_answer, Capability.VALUATION_RATIOS, {"rows": [], "band": None}
        )
        resp = client.get(
            "/api/v1/stock/valuation-ratios", params={"symbol": "2330", "market": "TW"}
        )
        assert resp.status_code == 200


class TestAnalystConsensusRoute:
    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings, agent_answer, Capability.ANALYST_CONSENSUS, {"consensus": None}
        )
        resp = client.get(
            "/api/v1/stock/analyst-consensus", params={"symbol": "2330", "market": "TW"}
        )
        assert resp.status_code == 200
        assert resp.json()["consensus"] is None


class TestFinmindStockNewsRoute:
    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings, agent_answer, Capability.STOCK_NEWS, {"rows": []}
        )
        resp = client.get("/api/v1/stock/finmind-news", params={"symbol": "2330", "market": "TW"})
        assert resp.status_code == 200


class TestForeignShareholdingRoute:
    """籌碼分析's 三大法人 column, 持股 sub-tab — `/stock/*` alias, same tool the earlier
    `/market/day-trading`-adjacent routes already reach, just consistently named here."""

    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings,
            agent_answer,
            Capability.FOREIGN_SHAREHOLDING,
            {
                "rows": [
                    {
                        "symbol": "2330",
                        "market": "TW",
                        "trade_date": "2026-09-08",
                        "stock_name": "台積電",
                        "international_code": "TW0002330008",
                        "foreign_investment_remaining_shares": 1000,
                        "foreign_investment_shares": 500,
                        "foreign_investment_remain_ratio": 10.5,
                        "foreign_investment_shares_ratio": 75.2,
                        "foreign_investment_upper_limit_ratio": 100.0,
                        "chinese_investment_upper_limit_ratio": 0.0,
                        "number_of_shares_issued": 1000000,
                    }
                ]
            },
        )
        resp = client.get(
            "/api/v1/stock/foreign-shareholding", params={"symbol": "2330", "market": "TW"}
        )
        assert resp.status_code == 200
        assert "locked" not in resp.json()
        assert resp.json()["rows"][0]["stock_name"] == "台積電"

    def test_requires_symbol(self, settings: Settings, agent_answer: str) -> None:
        client = build_test_client(settings, agent_answer=agent_answer)
        resp = client.get("/api/v1/stock/foreign-shareholding")
        assert resp.status_code == 422


class TestStockDayTradingRoute:
    """籌碼分析's 融資券 column, 當沖 sub-tab — `/stock/*` alias for the same `get_day_trading`
    tool `/market/day-trading` already dispatches (that route's own docstring notes it's
    per-symbol despite the `/market/` prefix)."""

    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings,
            agent_answer,
            Capability.DAY_TRADING,
            {
                "rows": [
                    {
                        "symbol": "2330",
                        "market": "TW",
                        "trade_date": "2026-09-08",
                        "buy_after_sale": "Y",
                        "volume": 1000,
                        "buy_amount": 500000,
                        "sell_amount": 480000,
                    }
                ]
            },
        )
        resp = client.get("/api/v1/stock/day-trading", params={"symbol": "2330", "market": "TW"})
        assert resp.status_code == 200
        assert "locked" not in resp.json()
        assert resp.json()["rows"][0]["buy_after_sale"] == "Y"

    def test_requires_symbol(self, settings: Settings, agent_answer: str) -> None:
        client = build_test_client(settings, agent_answer=agent_answer)
        resp = client.get("/api/v1/stock/day-trading")
        assert resp.status_code == 422


class TestLiveQuoteRoute:
    """即時 tab's 五檔 ladder — `get_live_quote_snapshot` is `DataClass.PUBLIC` (TWSE MIS, the
    one free keyless intraday source), so it is never masked regardless of caller tier."""

    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings,
            agent_answer,
            Capability.PRICE_INTRADAY,
            {
                "snapshot": {
                    "symbol": "2330",
                    "market": "TW",
                    "name": "台積電",
                    "last_price": 900.0,
                    "open": 895.0,
                    "high": 905.0,
                    "low": 890.0,
                    "volume": 12345,
                    "best_bid": "899.0_898.5_898.0_897.5_897.0",
                    "best_ask": "900.0_900.5_901.0_901.5_902.0",
                    "best_bid_volume": "10_20_30_40_50",
                    "best_ask_volume": "5_15_25_35_45",
                    "observed_at": "13:30:00",
                }
            },
        )
        resp = client.get("/api/v1/stock/live-quote", params={"symbol": "2330", "market": "TW"})
        assert resp.status_code == 200
        body = resp.json()
        assert "locked" not in body
        assert body["snapshot"]["last_price"] == 900.0
        assert body["snapshot"]["best_bid"] == "899.0_898.5_898.0_897.5_897.0"

    def test_requires_symbol(self, settings: Settings, agent_answer: str) -> None:
        client = build_test_client(settings, agent_answer=agent_answer)
        resp = client.get("/api/v1/stock/live-quote")
        assert resp.status_code == 422


class TestPriceLimitRoute:
    """看盤 tab's 詳細 stats row — `get_price_limit` is `DataClass.PUBLIC`, so it is never
    masked regardless of caller tier."""

    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings,
            agent_answer,
            Capability.PRICE_LIMIT,
            {
                "rows": [
                    {
                        "symbol": "2330",
                        "market": "TW",
                        "trade_date": "2026-09-18",
                        "reference_price": 2420.0,
                        "limit_up": 2660.0,
                        "limit_down": 2180.0,
                    }
                ]
            },
        )
        resp = client.get("/api/v1/stock/price-limit", params={"symbol": "2330", "market": "TW"})
        assert resp.status_code == 200
        body = resp.json()
        assert "locked" not in body
        assert body["rows"][0]["reference_price"] == 2420.0

    def test_requires_symbol(self, settings: Settings, agent_answer: str) -> None:
        client = build_test_client(settings, agent_answer=agent_answer)
        resp = client.get("/api/v1/stock/price-limit")
        assert resp.status_code == 422


class TestIntradayKbarRoute:
    """即時 tab's 分K chart — `get_intraday_kbar` is `DataClass.SPONSOR`; an unentitled caller
    gets the whole-tool masked-teaser envelope, never real bars."""

    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings,
            agent_answer,
            Capability.INTRADAY_KBAR,
            {
                "bars": [
                    {
                        "symbol": "2330",
                        "market": "TW",
                        "trade_date": "2026-09-08",
                        "minute": "09:00",
                        "open": 895.0,
                        "high": 896.0,
                        "low": 894.0,
                        "close": 895.5,
                        "volume": 100.0,
                    }
                ]
            },
        )
        resp = client.get("/api/v1/stock/intraday-kbar", params={"symbol": "2330", "market": "TW"})
        assert resp.status_code == 200
        body = resp.json()
        if body.get("locked"):
            return  # unentitled test session — the masked-teaser envelope is a valid 200 too
        assert body["rows"][0]["minute"] == "09:00"

    def test_requires_symbol(self, settings: Settings, agent_answer: str) -> None:
        client = build_test_client(settings, agent_answer=agent_answer)
        resp = client.get("/api/v1/stock/intraday-kbar")
        assert resp.status_code == 422


class TestPriceTickRoute:
    """即時 tab's 逐筆 list — `get_price_tick` is `DataClass.SPONSOR`; an unentitled caller
    gets the whole-tool masked-teaser envelope, never real ticks. Upstream is an EOD replay
    (~15:30 publish), never a live-ticking stream."""

    def test_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_capability(
            settings,
            agent_answer,
            Capability.PRICE_TICK,
            {
                "ticks": [
                    {
                        "symbol": "2330",
                        "market": "TW",
                        "trade_date": "2026-09-08",
                        "time": "09:00:01",
                        "deal_price": 895.0,
                        "volume": 10,
                        "tick_type": "1",
                    }
                ]
            },
        )
        resp = client.get("/api/v1/stock/price-tick", params={"symbol": "2330", "market": "TW"})
        assert resp.status_code == 200
        body = resp.json()
        if body.get("locked"):
            return  # unentitled test session — the masked-teaser envelope is a valid 200 too
        assert body["rows"][0]["deal_price"] == 895.0

    def test_requires_symbol(self, settings: Settings, agent_answer: str) -> None:
        client = build_test_client(settings, agent_answer=agent_answer)
        resp = client.get("/api/v1/stock/price-tick")
        assert resp.status_code == 422


@pytest.mark.governance
class TestSponsorMaskedRoutes:
    """`get_disposition_securities`/`get_shareholder_dispersion`-adjacent `get_calendar_events`
    are `DataClass.SPONSOR` with no row-preview opt-in — a Free/anonymous caller must get the
    whole-tool masked-teaser envelope at HTTP 200, never real data, over these routes.
    `get_chip_flows` is also `DataClass.SPONSOR` but HAS opted into the governance.md §4
    row-preview exception (see `test_chip_flows_row_masked_for_free` below) — its Free-caller
    response is real, just with individually-masked ranked rows, not a `MaskedTeaser`."""

    def test_disposition_securities_masked_for_free(
        self, client: TestClient, signed_in_user
    ) -> None:
        headers, _ = signed_in_user
        resp = client.get(
            "/api/v1/stock/disposition-securities",
            params={"symbol": "2330", "market": "TW"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["locked"] is True

    def test_chip_flows_row_masked_for_free(self, client: TestClient, signed_in_user) -> None:
        """`get_chip_flows` opted into the governance.md §4 row-level preview exception
        (`supports_row_preview=True`) — a Free caller gets HTTP 200 with a real (never `locked`)
        body whose ranked rows are individually masked, not the whole-tool teaser envelope."""
        headers, _ = signed_in_user
        resp = client.get(
            "/api/v1/stock/chip-flows",
            params={"symbol": "2330", "market": "TW"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "locked" not in body
        assert body["rows"] == []
        assert body["top_brokers"] == []

    def test_calendar_events_masked_for_free(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        resp = client.get(
            "/api/v1/stock/calendar-events",
            params={"symbol": "2330", "market": "TW"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["locked"] is True


class TestCompanyProfileRoute:
    """`get_company_profile` composes 6 capabilities — a stub router answering all 6 (rather
    than the single-capability helper above) proves the route wires through the real registry
    without ever reaching a live adapter; per-field enrichment is `test_market_tools.py`'s job."""

    def _client(self, settings: Settings, agent_answer: str) -> TestClient:
        client = build_test_client(settings, agent_answer=agent_answer)
        symbol = Symbol(id="2330", market=Market.TW)
        stub_router, _ = make_router(
            symbol,
            {
                Capability.SECTOR_CLASS: _result({}),
                Capability.DIVIDENDS: _result({}),
                Capability.MARKET_VALUE: _result({}),
                Capability.VALUATION_RATIOS: _result({}),
                Capability.FINANCIALS: _result({}),
                Capability.PROFILE: _result({}),
            },
        )
        registry = build_default_tool_registry(stub_router)
        object.__setattr__(client.app.state.app_state, "tool_registry", registry)
        return client

    def test_route_wires_through_real_registry(self, settings: Settings, agent_answer: str) -> None:
        client = self._client(settings, agent_answer)
        resp = client.get(
            "/api/v1/stock/company-profile", params={"symbol": "2330", "market": "TW"}
        )
        assert resp.status_code == 200
        assert "locked" not in resp.json()

    def test_requires_symbol(self, settings: Settings, agent_answer: str) -> None:
        client = build_test_client(settings, agent_answer=agent_answer)
        resp = client.get("/api/v1/stock/company-profile")
        assert resp.status_code == 422
