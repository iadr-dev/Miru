"""The new `/market/insider-transfer-declarations` REST route, driven through the REAL FastAPI
route via `TestClient` — same stub-router-swap pattern as
`test_material_announcement_feed_route.py`. `get_insider_transfer_declarations`'s tool is
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
    stub_router, _ = make_router(Symbol(id="2884", market=Market.TW), {})
    registry = build_default_tool_registry(stub_router)
    object.__setattr__(client.app.state.app_state, "tool_registry", registry)
    return client


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "2884",
        "market": "TW",
        "company_name": "玉山金",
        "filer_role": "子公司經理人本人",
        "person_name": "鄭恩融",
        "transfer_method": "一般交易(每日得轉讓股數限制)",
        "transfer_shares": 220000,
        "max_daily_exchange_shares": 17753510,
        "transferee": None,
        "current_shares_own": 546279,
        "current_shares_trust": 0,
        "planned_shares_own": 220000,
        "planned_shares_trust": 0,
        "projected_shares_own_after": 326279,
        "projected_shares_trust_after": 0,
        "transfer_window_start": "2026-09-06",
        "transfer_window_end": "2026-10-05",
    }
    base.update(overrides)
    return base


class TestInsiderTransferDeclarationRoute:
    def test_reports_unavailable_when_no_partition(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/insider-transfer-declarations")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["unavailable_reason"]
        assert body["rows"] == []

    def test_lists_every_row_for_the_requested_day(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "insider_transfer_declaration",
            "TW",
            date_(2026, 9, 3),
            [
                _row(),
                _row(
                    symbol="6603",
                    company_name="聯友金屬-創",
                    person_name="王小明",
                    transfer_method="贈與",
                    transfer_shares=2000,
                    max_daily_exchange_shares=None,
                    transferee="張又仁",
                    current_shares_own=33406,
                    planned_shares_own=2000,
                    projected_shares_own_after=31406,
                ),
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/insider-transfer-declarations",
            params={"market": "TW", "trade_date": "2026-09-03"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        assert body["report_date"] == "2026-09-03"
        assert body["available_dates"] == ["2026-09-03"]
        assert len(body["rows"]) == 2
        by_symbol = {row["symbol"]: row for row in body["rows"]}
        assert by_symbol["2884"]["transferee"] is None
        assert by_symbol["6603"]["transferee"] == "張又仁"
        assert by_symbol["6603"]["transfer_method"] == "贈與"

    def test_omitted_trade_date_falls_back_to_the_latest_available_day(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "insider_transfer_declaration", "TW", date_(2026, 9, 1), [_row(symbol="0050")]
        )
        store.write_partition(
            "insider_transfer_declaration", "TW", date_(2026, 9, 3), [_row(symbol="2884")]
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/insider-transfer-declarations", params={"market": "TW"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["report_date"] == "2026-09-03"
        assert sorted(body["available_dates"]) == ["2026-09-01", "2026-09-03"]
        assert body["rows"][0]["symbol"] == "2884"


class TestStockInsiderTransferDeclarationRoute:
    """基本資料's 董監事持股 card, 持股轉讓 sub-tab — the new `/stock/*` route, same whole-market
    feed as above but filtered server-side to one `symbol`."""

    def test_filters_to_the_requested_symbol_only(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "insider_transfer_declaration",
            "TW",
            date_(2026, 9, 3),
            [
                _row(),
                _row(
                    symbol="6603",
                    company_name="聯友金屬-創",
                    person_name="王小明",
                    transferee="張又仁",
                ),
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/stock/insider-transfer-declarations",
            params={"symbol": "2884", "market": "TW", "trade_date": "2026-09-03"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        assert len(body["rows"]) == 1
        assert body["rows"][0]["symbol"] == "2884"
        assert body["rows"][0]["person_name"] == "鄭恩融"

    def test_no_rows_for_symbol_returns_empty_list_not_unavailable(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A day with real data, just none for THIS symbol, must stay `data_available: true`
        with an empty `rows` — never re-labeled as "unavailable" (that's reserved for "ingest
        has no partition for this day at all," a different, honest condition)."""
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "insider_transfer_declaration", "TW", date_(2026, 9, 3), [_row(symbol="6603")]
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/stock/insider-transfer-declarations",
            params={"symbol": "2884", "market": "TW", "trade_date": "2026-09-03"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        assert body["rows"] == []

    def test_reports_unavailable_when_no_partition_at_all(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/stock/insider-transfer-declarations",
            params={"symbol": "2884", "market": "TW"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["rows"] == []

    def test_requires_symbol(self, settings: Settings, agent_answer: str) -> None:
        client = build_test_client(settings, agent_answer=agent_answer)
        resp = client.get("/api/v1/stock/insider-transfer-declarations")
        assert resp.status_code == 422
