"""The new `/market/material-announcement-feed` REST route, driven through the REAL FastAPI
route via `TestClient` — same stub-router-swap pattern as `test_dividend_calendar_route.py`.
`get_material_announcement_feed`'s tool is `DataClass.PUBLIC`, so no masked-teaser assertion is
needed here.
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


class TestMaterialAnnouncementFeedRoute:
    def test_reports_unavailable_when_no_partition(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/material-announcement-feed")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["unavailable_reason"]
        assert body["rows"] == []
        assert body["category_counts"] == {}

    def test_lists_every_row_and_buckets_category_counts(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "material_announcements",
            "TW",
            date_(2026, 9, 3),
            [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "company_name": "台積電",
                    "announced_date": "2026-09-03",
                    "announced_time": "09:11:07",
                    "subject": "公告本公司將於115年09月11日召開第1次法人說明會",
                    "detail": "法說會相關說明",
                    "qualifying_clause": "第12款",
                    "category": "法說會",
                },
                {
                    "symbol": "4155",
                    "market": "TW",
                    "company_name": "訊映",
                    "announced_date": "2026-09-03",
                    "announced_time": "18:28:00",
                    "subject": "公告本公司辦理庫藏股註銷減資變更登記完成",
                    "detail": "減資相關說明",
                    "qualifying_clause": "第36款",
                    "category": "庫藏股/減資",
                },
                {
                    "symbol": "1721",
                    "market": "TW",
                    "company_name": "國慶科技",
                    "announced_date": "2026-09-03",
                    "announced_time": "17:55:00",
                    "subject": "公告本公司名稱由「三晃股份有限公司」更名為「國慶科技股份有限公司」",
                    "detail": "更名相關說明",
                    "qualifying_clause": "第51款",
                    "category": "其他",
                },
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/material-announcement-feed",
            params={"market": "TW", "trade_date": "2026-09-03"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        assert body["report_date"] == "2026-09-03"
        assert body["available_dates"] == ["2026-09-03"]
        assert len(body["rows"]) == 3
        assert body["category_counts"] == {"法說會": 1, "庫藏股/減資": 1, "其他": 1}

    def test_filters_to_one_category(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "material_announcements",
            "TW",
            date_(2026, 9, 3),
            [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "company_name": "台積電",
                    "announced_date": "2026-09-03",
                    "announced_time": "09:11:07",
                    "subject": "法說會公告",
                    "detail": "說明",
                    "qualifying_clause": "第12款",
                    "category": "法說會",
                },
                {
                    "symbol": "4155",
                    "market": "TW",
                    "company_name": "訊映",
                    "announced_date": "2026-09-03",
                    "announced_time": "18:28:00",
                    "subject": "減資公告",
                    "detail": "說明",
                    "qualifying_clause": "第36款",
                    "category": "庫藏股/減資",
                },
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/material-announcement-feed",
            params={"market": "TW", "trade_date": "2026-09-03", "category": "法說會"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["rows"]) == 1
        assert body["rows"][0]["symbol"] == "2330"
        # category_counts always reflects the WHOLE day, never the filtered subset — a caller
        # with one chip active must still see every other chip's real count, so the chip row
        # itself never has to re-fetch to render.
        assert body["category_counts"] == {"法說會": 1, "庫藏股/減資": 1}

    def test_omitted_trade_date_falls_back_to_the_latest_available_day(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "material_announcements",
            "TW",
            date_(2026, 9, 1),
            [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "company_name": "台積電",
                    "announced_date": "2026-09-01",
                    "announced_time": "10:00:00",
                    "subject": "舊公告",
                    "detail": "說明",
                    "qualifying_clause": "第12款",
                    "category": "法說會",
                }
            ],
        )
        store.write_partition(
            "material_announcements",
            "TW",
            date_(2026, 9, 3),
            [
                {
                    "symbol": "4155",
                    "market": "TW",
                    "company_name": "訊映",
                    "announced_date": "2026-09-03",
                    "announced_time": "18:28:00",
                    "subject": "最新公告",
                    "detail": "說明",
                    "qualifying_clause": "第36款",
                    "category": "庫藏股/減資",
                }
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/material-announcement-feed", params={"market": "TW"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["report_date"] == "2026-09-03"  # the latest, not the first-written
        assert sorted(body["available_dates"]) == ["2026-09-01", "2026-09-03"]
        assert body["rows"][0]["symbol"] == "4155"
