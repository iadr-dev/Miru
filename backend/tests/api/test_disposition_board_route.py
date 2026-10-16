"""The `/market/disposition-board` REST route, driven through the REAL FastAPI route via
`TestClient` — same stub-router-swap + `PARQUET_ROOT`-env pattern as
`test_material_announcement_feed_route.py`. `get_disposition_board` is `DataClass.PUBLIC` (it is
built on free exchange data), so a free caller gets the whole board.
"""

from __future__ import annotations

from datetime import date as date_
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from ingest.jobs import DISPOSITION_EVENTS_DATASET
from ingest.store import ParquetStore
from schemas.enums import Market, Tier
from schemas.market import Symbol
from tests.api.conftest import build_test_client, sign_in
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
    client.__enter__()
    stub_router, _ = make_router(Symbol(id="3008", market=Market.TW), {})
    registry = build_default_tool_registry(stub_router)
    object.__setattr__(client.app.state.app_state, "tool_registry", registry)
    return client


class TestDispositionBoardRoute:
    def test_free_caller_gets_the_whole_board(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            DISPOSITION_EVENTS_DATASET,
            "TW",
            date_(2026, 9, 4),
            [
                {
                    "symbol": "3008",
                    "market": "TW",
                    "company_name": "大立光",
                    "announced_date": "2026-09-02",
                    "reason": "連續三次",
                    "period_start": "2026-09-03",
                    "period_end": "2026-09-20",
                    "measure": "第一次處置",
                    "detail": None,
                    "security_type": "twse",
                }
            ],
        )

        resp = client.get("/api/v1/market/disposition-board")

        assert resp.status_code == 200
        body = resp.json()
        assert "locked" not in body
        assert body["data_available"] is True
        assert body["cards"][0]["symbol"] == "3008"
        assert "locked" not in body["cards"][0]

    def test_reports_unavailable_when_no_partition_for_entitled_caller(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)
        headers, _ = sign_in(client, settings, tier=Tier.PREMIUM)

        resp = client.get("/api/v1/market/disposition-board", headers=headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["cards"] == []

    def test_entitled_caller_sees_real_board(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)
        headers, _ = sign_in(client, settings, tier=Tier.PREMIUM)
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            DISPOSITION_EVENTS_DATASET,
            "TW",
            date_(2026, 9, 4),
            [
                {
                    "symbol": "3008",
                    "market": "TW",
                    "company_name": "大立光",
                    "announced_date": "2026-09-02",
                    "reason": "連續三次",
                    "period_start": "2026-09-03",
                    "period_end": "2026-09-20",
                    "measure": "第一次處置",
                    "detail": None,
                    "security_type": "twse",
                }
            ],
        )

        resp = client.get("/api/v1/market/disposition-board", headers=headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        assert body["cards"][0]["symbol"] == "3008"
        assert body["cards"][0]["bucket"] == "in_disposition"
        assert body["cards"][0]["projection_caveat"]
