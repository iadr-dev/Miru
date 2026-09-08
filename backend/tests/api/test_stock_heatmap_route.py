"""The new `/market/stock-heatmap` REST route, driven through the REAL FastAPI route via
`TestClient` — same `TestClient` swap pattern as `tests/api/test_sector_heatmap_route.py`.
`get_stock_heatmap`'s tool is `DataClass.PUBLIC`, so no masked-teaser assertion is needed here.
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


class TestStockHeatmapRoute:
    def test_reports_unavailable_when_no_partition(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/stock-heatmap")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["unavailable_reason"]
        assert body["stocks"] == []

    def test_returns_one_row_per_stock_with_industry(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        prior_day = date_(2025, 5, 6)
        trading_day = date_(2025, 5, 7)
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            prior_day,
            [
                {"symbol": "2330", "trade_date": "2025-05-06", "close": 100.0},
                {"symbol": "2881", "trade_date": "2025-05-06", "close": 100.0},
            ],
        )
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            trading_day,
            [
                {
                    "symbol": "2330",
                    "trade_date": "2025-05-07",
                    "close": 110.0,
                    "volume": 1000,
                    "turnover": 110000.0,
                },
                {
                    "symbol": "2881",
                    "trade_date": "2025-05-07",
                    "close": 96.0,
                    "volume": 500,
                    "turnover": 48000.0,
                },
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
                    "as_of": "2025-05-07",
                },
                {
                    "symbol": "2881",
                    "market": "TW",
                    "name": "富邦金",
                    "industry_category": "金融保險業",
                    "security_type": "twse",
                    "as_of": "2025-05-07",
                },
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/stock-heatmap",
            params={"trade_date": trading_day.isoformat()},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        by_symbol = {row["symbol"]: row for row in body["stocks"]}
        assert by_symbol["2330"]["industry"] == "半導體業"
        assert by_symbol["2330"]["pct_change"] == 10.0
        assert by_symbol["2881"]["industry"] == "金融保險業"
        assert by_symbol["2881"]["pct_change"] == -4.0
        assert "locked" not in body

    def test_ranked_by_the_same_metric_size_by_requests(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        prior_day = date_(2025, 5, 6)
        trading_day = date_(2025, 5, 7)
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            prior_day,
            [
                {"symbol": "2330", "trade_date": "2025-05-06", "close": 100.0},
                {"symbol": "2881", "trade_date": "2025-05-06", "close": 100.0},
            ],
        )
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            trading_day,
            [
                {
                    "symbol": "2330",
                    "trade_date": "2025-05-07",
                    "close": 110.0,
                    "volume": 100,
                    "turnover": 11000.0,
                },
                {
                    "symbol": "2881",
                    "trade_date": "2025-05-07",
                    "close": 96.0,
                    "volume": 900,
                    "turnover": 86400.0,
                },
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
                    "as_of": "2025-05-07",
                },
                {
                    "symbol": "2881",
                    "market": "TW",
                    "name": "富邦金",
                    "industry_category": "金融保險業",
                    "security_type": "twse",
                    "as_of": "2025-05-07",
                },
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/stock-heatmap",
            params={"trade_date": trading_day.isoformat(), "size_by": "volume"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["size_by"] == "volume"
        # 2881 has the bigger volume (900 > 100), so ranked-by-volume must put it first even
        # though 2330 has the bigger turnover — proves the ranking key matches size_by, not
        # always turnover.
        assert body["stocks"][0]["symbol"] == "2881"
