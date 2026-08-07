"""The new `/funds/*` REST routes driven through the REAL FastAPI route via `TestClient`, proving
each one dispatches through the real registry (`analyst_tools.py`/`etf_overlap_tools.py`) and
returns the expected shape — mirroring `test_themes_routes.py`'s stub-router-swap pattern."""

from __future__ import annotations

from datetime import UTC, date, datetime

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


_OPTIONS_PAYLOAD = _result(
    {
        "contracts": [
            {
                "symbol": "SPY",
                "market": "US",
                "expiry": "2025-01-17",
                "contract_symbol": "SPY250117C00500000",
                "side": "call",
                "strike": 500.0,
                "last_price": 12.5,
                "in_the_money": True,
            }
        ]
    }
)

_HOLDERS_PAYLOAD = _result(
    {
        "holders": [
            {
                "symbol": "SPY",
                "market": "US",
                "holder_type": "institutional",
                "holder_name": "Vanguard",
                "date_reported": "2024-12-31",
                "shares": 1000,
                "value": 500000.0,
                "pct_held": 0.1,
                "pct_change": 0.01,
            }
        ]
    }
)

_INSIDER_PAYLOAD = _result(
    {
        "transactions": [
            {
                "symbol": "AAPL",
                "market": "US",
                "insider_name": "Jane Doe",
                "position": "CFO",
                "transaction": "Sale",
                "shares": 500,
            }
        ]
    }
)

_HOLDINGS_PAYLOAD = _result(
    {
        "holdings": [
            {
                "symbol": "SPY",
                "market": "US",
                "holding_symbol": "AAPL",
                "holding_name": "Apple Inc.",
                "weight": 0.07,
            }
        ],
        "sector_weightings": [
            {"symbol": "SPY", "market": "US", "sector": "Technology", "weight": 0.3},
        ],
    }
)


def _client_with_stub(settings: Settings, agent_answer: str) -> TestClient:
    """A real `TestClient` whose tool registry answers every capability with a canned, healthy
    result — enough to prove the route wires through the real tool, not the tool's own business
    logic (that logic is covered by `tests/tools/test_analyst_tools.py` and
    `tests/tools/test_etf_overlap_tools.py`)."""
    client = build_test_client(settings, agent_answer=agent_answer)
    symbol = Symbol(id="SPY", market=Market.US)
    results = {
        Capability.OPTIONS_CHAIN: _OPTIONS_PAYLOAD,
        Capability.INSTITUTIONAL_HOLDERS: _HOLDERS_PAYLOAD,
        Capability.INSIDER_ACTIVITY: _INSIDER_PAYLOAD,
        Capability.FUND_HOLDINGS: _HOLDINGS_PAYLOAD,
        Capability.PRICE_EOD: _result(
            {
                "bars": [
                    {
                        "symbol": "SPY",
                        "market": "US",
                        "trade_date": date(2024, 1, 2).isoformat(),
                        "open": 100.0,
                        "high": 102.0,
                        "low": 99.0,
                        "close": 101.0,
                        "volume": 1000,
                    },
                    {
                        "symbol": "SPY",
                        "market": "US",
                        "trade_date": date(2024, 6, 2).isoformat(),
                        "open": 110.0,
                        "high": 112.0,
                        "low": 109.0,
                        "close": 111.0,
                        "volume": 1000,
                    },
                ]
            }
        ),
        Capability.DIVIDENDS: _result({"events": []}),
        Capability.ETF_HOLDINGS: _result({"info": None}),
    }
    stub_router, _ = make_router(symbol, results)
    registry = build_default_tool_registry(stub_router)
    object.__setattr__(client.app.state.app_state, "tool_registry", registry)
    return client


class TestPerSymbolRoutes:
    def test_options_chain_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_stub(settings, agent_answer)
        resp = client.get("/api/v1/funds/SPY/options-chain", params={"market": Market.US.value})
        assert resp.status_code == 200
        body = resp.json()
        assert body["contracts"][0]["contract_symbol"] == "SPY250117C00500000"

    def test_institutional_holders_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_stub(settings, agent_answer)
        resp = client.get(
            "/api/v1/funds/SPY/institutional-holders", params={"market": Market.US.value}
        )
        assert resp.status_code == 200
        assert resp.json()["holders"][0]["holder_name"] == "Vanguard"

    def test_insider_activity_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_stub(settings, agent_answer)
        resp = client.get("/api/v1/funds/AAPL/insider-activity", params={"market": Market.US.value})
        assert resp.status_code == 200
        assert resp.json()["transactions"][0]["insider_name"] == "Jane Doe"

    def test_fund_holdings_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_stub(settings, agent_answer)
        resp = client.get("/api/v1/funds/SPY/holdings", params={"market": Market.US.value})
        assert resp.status_code == 200
        body = resp.json()
        assert body["holdings"][0]["holding_symbol"] == "AAPL"
        assert body["sector_weightings"][0]["sector"] == "Technology"


class TestOverlapRoute:
    def test_overlap_happy_path(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_stub(settings, agent_answer)
        resp = client.get(
            "/api/v1/funds/overlap",
            params={"symbols": "SPY,VOO,IVV", "market": Market.US.value},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["requested_symbols"] == ["SPY", "VOO", "IVV"]

    def test_overlap_rejects_too_few_symbols(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_stub(settings, agent_answer)
        resp = client.get(
            "/api/v1/funds/overlap",
            params={"symbols": "SPY,VOO", "market": Market.US.value},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "invalid_request"
        assert body["retryable"] is False


class TestComparisonRoute:
    def test_comparison_happy_path(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_stub(settings, agent_answer)
        resp = client.get(
            "/api/v1/funds/comparison",
            params={"symbols": "SPY:US,VOO:US"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["rows"]) == 2
        assert body["rows"][0]["symbol"] == "SPY"

    def test_comparison_rejects_too_few_symbols(
        self, settings: Settings, agent_answer: str
    ) -> None:
        client = _client_with_stub(settings, agent_answer)
        resp = client.get("/api/v1/funds/comparison", params={"symbols": "SPY:US"})
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "invalid_request"
        assert body["retryable"] is False
