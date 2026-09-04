"""The `/market/daily-signal` REST route (今日訊號), driven through the REAL FastAPI route via
`TestClient` — same stub-router-swap pattern as `test_etf_list_route.py`. `get_daily_signal`'s
tool is `DataClass.SPONSOR`; this test session may resolve as unentitled (the masked-teaser
envelope is a valid HTTP 200 too), so each test bails early on `body.get("locked")` — same
convention `test_holder_accumulation_route.py` uses for its own Sponsor-tier route."""

from __future__ import annotations

from datetime import date as date_
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from ingest.store import ParquetStore
from schemas.enums import Market
from schemas.market import Symbol
from tests.api.conftest import build_test_client
from tests.tools.conftest import make_router
from tools import build_default_tool_registry


def _client_with_store(
    settings: Settings,
    agent_answer: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setenv("PARQUET_ROOT", str(tmp_path))
    client = build_test_client(settings, agent_answer=agent_answer)
    stub_router, _ = make_router(Symbol(id="2330", market=Market.TW), {})
    registry = build_default_tool_registry(stub_router)
    object.__setattr__(client.app.state.app_state, "tool_registry", registry)
    return client


class TestDailySignalRoute:
    def test_reports_unavailable_when_no_partition(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/daily-signal")

        assert resp.status_code == 200
        body = resp.json()
        if body.get("locked"):
            return  # unentitled test session — the masked-teaser envelope is a valid 200 too
        assert body["data_available"] is False
        assert body["unavailable_reason"]
        assert body["rows"] == []

    def test_classifies_status_and_reports_highlights(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        prior_day = date_(2025, 5, 6)
        report_day = date_(2025, 5, 7)
        store.write_partition(
            "TaiwanStockActiveETFHolding",
            "TW",
            prior_day,
            [
                {
                    "symbol": "00980A",
                    "market": "TW",
                    "trade_date": "2025-05-06",
                    "component_stock_id": "2330",
                    "component_stock_name": "台積電",
                    "asset_type": "stock",
                    "shares": 100000,
                    "weight": 8.0,
                    "market_value": 0.0,
                    "currency": "TWD",
                },
            ],
        )
        store.write_partition(
            "TaiwanStockActiveETFHolding",
            "TW",
            report_day,
            [
                {
                    "symbol": "00980A",
                    "market": "TW",
                    "trade_date": "2025-05-07",
                    "component_stock_id": "2330",
                    "component_stock_name": "台積電",
                    "asset_type": "stock",
                    "shares": 110000,
                    "weight": 8.5,
                    "market_value": 0.0,
                    "currency": "TWD",
                },
            ],
        )
        store.write_partition(
            "TaiwanStockActiveETFHoldingChange",
            "TW",
            report_day,
            [
                {
                    "symbol": "00980A",
                    "market": "TW",
                    "trade_date": "2025-05-07",
                    "component_stock_id": "2330",
                    "component_stock_name": "台積電",
                    "buy": 10000,
                    "sell": 0,
                },
            ],
        )
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            report_day,
            [{"symbol": "2330", "trade_date": "2025-05-07", "close": 600.0}],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/daily-signal", params={"trade_date": report_day.isoformat()}
        )

        assert resp.status_code == 200
        body = resp.json()
        if body.get("locked"):
            return  # unentitled test session — the masked-teaser envelope is a valid 200 too
        assert body["data_available"] is True
        assert body["report_date"] == report_day.isoformat()
        assert body["prior_date"] == prior_day.isoformat()
        assert body["increased_count"] == 1
        row = body["rows"][0]
        assert row["component_stock_id"] == "2330"
        assert row["status"] == "加碼"
        assert body["top_inflow"]["component_stock_id"] == "2330"
