"""The new `/funds/*` REST routes driven through the REAL FastAPI route via `TestClient`, proving
each one dispatches through the real registry (`analyst_tools.py`/`etf_overlap_tools.py`) and
returns the expected shape — mirroring `test_themes_routes.py`'s stub-router-swap pattern."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from ingest.jobs import ETF_NAV_DATASET
from ingest.store import ParquetStore
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
        Capability.ETF_TRAILING_RETURNS: _result(
            {
                "returns": {
                    "symbol": "SPY",
                    "market": "US",
                    "as_of": date(2024, 6, 2).isoformat(),
                    "return_1m_pct": 2.1,
                    "return_1q_pct": 5.4,
                    "return_6m_pct": 9.8,
                    "return_1y_pct": 18.3,
                    "return_3y_pct": 40.0,
                    "return_5y_pct": 95.5,
                }
            }
        ),
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

    def test_trailing_returns_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_stub(settings, agent_answer)
        resp = client.get("/api/v1/funds/SPY/trailing-returns", params={"market": Market.US.value})
        assert resp.status_code == 200
        body = resp.json()
        assert body["returns"]["return_1y_pct"] == 18.3
        assert body["returns"]["return_5y_pct"] == 95.5


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

    def test_comparison_single_symbol_succeeds(
        self, settings: Settings, agent_answer: str
    ) -> None:
        """A single symbol is a legitimate call — e.g. the ETF detail page's own 總覽 tab uses
        this route for one fund's own since-inception/YTD return, not a multi-fund comparison."""
        client = _client_with_stub(settings, agent_answer)
        resp = client.get("/api/v1/funds/comparison", params={"symbols": "SPY:US"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["rows"]) == 1
        assert body["rows"][0]["symbol"] == "SPY"

    def test_comparison_rejects_zero_symbols(
        self, settings: Settings, agent_answer: str
    ) -> None:
        client = _client_with_stub(settings, agent_answer)
        resp = client.get("/api/v1/funds/comparison", params={"symbols": ""})
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "invalid_request"
        assert body["retryable"] is False


def _client_with_etf_nav_store(
    settings: Settings,
    agent_answer: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """`get_etf_premium_discount` reads a real `ParquetStore`, not the stub router — same
    env-var-move pattern `test_etf_list_route.py::_client_with_store` uses."""
    monkeypatch.setenv("PARQUET_ROOT", str(tmp_path))
    client = build_test_client(settings, agent_answer=agent_answer)
    stub_router, _ = make_router(Symbol(id="00981A", market=Market.TW), {})
    registry = build_default_tool_registry(stub_router)
    object.__setattr__(client.app.state.app_state, "tool_registry", registry)
    return client


class TestPremiumDiscountRoute:
    def test_reports_unavailable_when_no_partition(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_etf_nav_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/funds/00981A/premium-discount")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["rows"] == []

    def test_returns_bounded_history(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PARQUET_ROOT", str(tmp_path))
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            ETF_NAV_DATASET,
            "TW",
            date(2026, 8, 26),
            [
                {
                    "symbol": "00981A",
                    "market": "TW",
                    "as_of": "2026-08-26",
                    "fund_name": "統一台股增長",
                    "close_price": 29.25,
                    "estimated_nav": 29.10,
                    "premium_discount_pct": -0.03,
                    "prior_day_nav": 29.00,
                }
            ],
        )
        client = build_test_client(settings, agent_answer=agent_answer)
        stub_router, _ = make_router(Symbol(id="00981A", market=Market.TW), {})
        registry = build_default_tool_registry(stub_router)
        object.__setattr__(client.app.state.app_state, "tool_registry", registry)

        resp = client.get(
            "/api/v1/funds/00981A/premium-discount", params={"market": "TW", "days": 30}
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        assert body["rows"][0]["premium_discount_pct"] == -0.03

    def test_rejects_out_of_bounds_days(
        self, settings: Settings, agent_answer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client_with_etf_nav_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/funds/00981A/premium-discount", params={"days": 1})

        assert resp.status_code == 422
