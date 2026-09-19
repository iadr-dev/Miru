"""The `/market/holder-accumulation` REST route, driven through the REAL FastAPI route via
`TestClient` — same pattern as `test_price_momentum_route.py`. `get_holder_accumulation` is
`DataClass.SPONSOR` but opted into the governance.md §4 row-level preview exception
(`supports_row_preview=True`), so an unentitled caller gets this shape directly (row-masked
`locked=True` rows beyond `free_row_count`), never a whole-tool masked-teaser envelope.
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


class TestHolderAccumulationRoute:
    def test_reports_unavailable_when_no_snapshot_ingested(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/holder-accumulation")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["unavailable_reason"]
        assert body["rows"] == []

    def test_ranks_by_percent_of_custody_change_over_one_week(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        week_ago = date_(2026, 8, 14)
        this_week = date_(2026, 8, 21)
        store.write_partition(
            "TaiwanStockHoldingSharesPer",
            "TW",
            week_ago,
            [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "holding_shares_level": "400,001-600,000",
                    "people": 1,
                    "percent": 10.0,
                    "unit": 500_000,
                },
                {
                    "symbol": "2454",
                    "market": "TW",
                    "holding_shares_level": "400,001-600,000",
                    "people": 1,
                    "percent": 20.0,
                    "unit": 900_000,
                },
            ],
        )
        store.write_partition(
            "TaiwanStockHoldingSharesPer",
            "TW",
            this_week,
            [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "holding_shares_level": "400,001-600,000",
                    "people": 1,
                    "percent": 15.0,
                    "unit": 700_000,
                },
                {
                    "symbol": "2454",
                    "market": "TW",
                    "holding_shares_level": "400,001-600,000",
                    "people": 1,
                    "percent": 19.0,
                    "unit": 850_000,
                },
            ],
        )
        store.write_partition(
            "TaiwanStockInfo",
            "TW",
            this_week,
            [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "name": "台積電",
                    "industry_category": "半導體業",
                    "security_type": "twse",
                    "as_of": "2026-08-21",
                },
                {
                    "symbol": "2454",
                    "market": "TW",
                    "name": "聯發科",
                    "industry_category": "半導體業",
                    "security_type": "twse",
                    "as_of": "2026-08-21",
                },
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/holder-accumulation",
            params={"threshold": 400, "window": 1, "as_of": this_week.isoformat()},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        # Anonymous/Free caller: 2 rows total, both within `free_row_count` — stay real.
        assert body["rows"][0]["symbol"] == "2330"
        assert body["rows"][0]["locked"] is False
        assert body["rows"][0]["percent_of_custody_change"] == pytest.approx(5.0)
