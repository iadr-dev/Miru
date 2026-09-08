"""The new `/market/sector-rotation` REST route, driven through the REAL FastAPI route via
`TestClient` — same stub-router-swap pattern as `test_sector_heatmap_route.py`.
`get_sector_rotation`'s tool is `DataClass.PUBLIC`, so no masked-teaser assertion is needed here
(see `test_phase11_governance.py` for the Sponsor-tier masking coverage).
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


def _seed_two_days(tmp_path: Path) -> tuple[date_, date_]:
    store = ParquetStore(root=tmp_path)
    day1 = date_(2026, 9, 3)
    day2 = date_(2026, 9, 4)
    store.write_partition(
        "TaiwanStockInstitutionalInvestorsBuySell",
        "TW",
        day1,
        [
            {
                "symbol": "2330",
                "trade_date": "2026-09-03",
                "investor": "Foreign_Investor",
                "buy": 100,
                "sell": 10,
            },
            {
                "symbol": "2881",
                "trade_date": "2026-09-03",
                "investor": "Foreign_Investor",
                "buy": 5,
                "sell": 50,
            },
        ],
    )
    store.write_partition(
        "TaiwanStockInstitutionalInvestorsBuySell",
        "TW",
        day2,
        [
            {
                "symbol": "2330",
                "trade_date": "2026-09-04",
                "investor": "Foreign_Investor",
                "buy": 5,
                "sell": 60,
            },
            {
                "symbol": "2881",
                "trade_date": "2026-09-04",
                "investor": "Foreign_Investor",
                "buy": 80,
                "sell": 10,
            },
        ],
    )
    store.write_partition(
        "TaiwanStockInfo",
        "TW",
        day2,
        [
            {
                "symbol": "2330",
                "market": "TW",
                "name": "台積電",
                "industry_category": "半導體業",
                "security_type": "twse",
                "as_of": "2026-09-04",
            },
            {
                "symbol": "2881",
                "market": "TW",
                "name": "富邦金",
                "industry_category": "金融保險業",
                "security_type": "twse",
                "as_of": "2026-09-04",
            },
        ],
    )
    return day1, day2


class TestSectorRotationRoute:
    def test_reports_unavailable_when_no_partition(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/sector-rotation")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["unavailable_reason"]
        assert body["rows"] == []

    def test_ranks_industries_by_net_flow_per_day(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        day1, day2 = _seed_two_days(tmp_path)
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/sector-rotation",
            params={"end_date": day2.isoformat()},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        assert body["trade_dates"] == [day1.isoformat(), day2.isoformat()]

        by_date = {}
        for row in body["rows"]:
            by_date.setdefault(row["trade_date"], {})[row["industry"]] = row["rank"]

        # day1: 2330 (半導體業) net +90 ranks 1st; 2881 (金融保險業) net -45 ranks 2nd.
        assert by_date[day1.isoformat()]["半導體業"] == 1
        assert by_date[day1.isoformat()]["金融保險業"] == 2
        # day2: 2881 net +70 ranks 1st; 2330 net -55 ranks 2nd — order flipped.
        assert by_date[day2.isoformat()]["金融保險業"] == 1
        assert by_date[day2.isoformat()]["半導體業"] == 2
