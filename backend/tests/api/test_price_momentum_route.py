"""The `/market/price-momentum` REST route, driven through the REAL FastAPI route via
`TestClient` — same pattern as `test_sector_heatmap_route.py`. `get_price_momentum`'s tool is
`DataClass.PUBLIC`, so no masked-teaser assertion is needed here.
"""

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


class TestPriceMomentumRoute:
    def test_reports_unavailable_when_no_partition(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/price-momentum")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["unavailable_reason"]
        assert body["top_gainers"] == []
        assert body["top_losers"] == []

    def test_ranks_gainers_and_losers_over_one_trading_week(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        week_ago_day = date_(2025, 5, 7)
        trading_day = date_(2025, 5, 14)
        # `GetPriceMomentumTool` builds its calendar from the store's own actually-ingested
        # partition dates, not a naive Mon-Fri guess — every trading day in the window needs a
        # real partition for `shift_business_days` to land on the intended week-ago date.
        for day in (date_(2025, 5, 8), date_(2025, 5, 9), date_(2025, 5, 12), date_(2025, 5, 13)):
            store.write_partition(
                "TaiwanStockPrice", "TW", day, [{"symbol": "2330", "trade_date": day.isoformat()}]
            )
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            week_ago_day,
            [
                {"symbol": "2330", "trade_date": "2025-05-07", "close": 100.0},
                {"symbol": "2454", "trade_date": "2025-05-07", "close": 100.0},
            ],
        )
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            trading_day,
            [
                {"symbol": "2330", "trade_date": "2025-05-14", "close": 120.0},
                {"symbol": "2454", "trade_date": "2025-05-14", "close": 90.0},
            ],
        )
        store.write_partition(
            "TaiwanStockInfo",
            "TW",
            trading_day,
            [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "name": "台積電",
                    "industry_category": "半導體業",
                    "security_type": "twse",
                    "as_of": "2025-05-14",
                },
                {
                    "symbol": "2454",
                    "market": "TW",
                    "name": "聯發科",
                    "industry_category": "半導體業",
                    "security_type": "twse",
                    "as_of": "2025-05-14",
                },
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/price-momentum",
            params={"period": "week", "trade_date": trading_day.isoformat(), "top_n": 1},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        assert body["period"] == "week"
        assert body["top_gainers"][0]["symbol"] == "2330"
        assert body["top_gainers"][0]["pct_change"] == 20.0
        assert body["top_losers"][0]["symbol"] == "2454"
        assert body["top_losers"][0]["pct_change"] == -10.0
        assert "locked" not in body

    def test_day_period_query_param(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        prior_day = date_(2025, 5, 13)
        trading_day = date_(2025, 5, 14)
        store.write_partition(
            "TaiwanStockPrice", "TW", prior_day, [{"symbol": "2330", "close": 100.0}]
        )
        store.write_partition(
            "TaiwanStockPrice", "TW", trading_day, [{"symbol": "2330", "close": 105.0}]
        )
        store.write_partition(
            "TaiwanStockInfo",
            "TW",
            trading_day,
            [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "name": "台積電",
                    "industry_category": "半導體業",
                    "security_type": "twse",
                    "as_of": "2025-05-14",
                }
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/price-momentum",
            params={"period": "day", "trade_date": trading_day.isoformat()},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        assert body["period"] == "day"
        assert body["period_ago_trade_date"] == prior_day.isoformat()
