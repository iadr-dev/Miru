"""The new `/market/sector-constituents` REST route, driven through the REAL FastAPI route via
`TestClient` — same stub-router-swap pattern as `test_sector_heatmap_route.py`.
`get_sector_constituents`'s tool is `DataClass.PUBLIC`, so no masked-teaser assertion is needed
here (see `test_phase11_governance.py` for the Sponsor-tier masking coverage).
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


def _seed_two_symbols(tmp_path: Path) -> tuple[date_, date_]:
    store = ParquetStore(root=tmp_path)
    prior_day = date_(2025, 5, 6)
    trading_day = date_(2025, 5, 7)
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        prior_day,
        [
            {"symbol": "2330", "trade_date": "2025-05-06", "close": 100.0, "volume": 1000},
            {"symbol": "2881", "trade_date": "2025-05-06", "close": 100.0, "volume": 2000},
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
                "volume": 1500,
                "turnover": 165_000.0,
            },
            {
                "symbol": "2881",
                "trade_date": "2025-05-07",
                "close": 96.0,
                "volume": 2500,
                "turnover": 240_000.0,
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
    return prior_day, trading_day


class TestSectorConstituentsRoute:
    def test_reports_unavailable_when_no_partition(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/sector-constituents")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["unavailable_reason"]
        assert body["rows"] == []

    def test_returns_every_symbol_when_no_industry_filter(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, trading_day = _seed_two_symbols(tmp_path)
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/sector-constituents",
            params={"trade_date": trading_day.isoformat()},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        by_symbol = {row["symbol"]: row for row in body["rows"]}
        assert by_symbol["2330"]["industry"] == "半導體業"
        assert by_symbol["2330"]["pct_change"] == 10.0
        assert by_symbol["2330"]["turnover"] == 165_000.0
        assert by_symbol["2881"]["pct_change"] == -4.0
        # Ranked pct_change descending: 2330 (+10%) ahead of 2881 (-4%).
        assert [row["symbol"] for row in body["rows"]] == ["2330", "2881"]
        assert "locked" not in body

    def test_industry_filter_restricts_to_that_industry_only(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, trading_day = _seed_two_symbols(tmp_path)
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/sector-constituents",
            params={"trade_date": trading_day.isoformat(), "industry": "半導體業"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        assert [row["symbol"] for row in body["rows"]] == ["2330"]
        assert body["industry"] == "半導體業"
