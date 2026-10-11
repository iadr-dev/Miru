"""The new `/market/sector-heatmap` REST route, driven through the REAL FastAPI route via
`TestClient` — proving it dispatches through the real registry (`tools/market/`) exactly
as `tests/api/test_tag_screener_routes.py`'s stub-router-swap pattern does for the tag-screener
routes. `get_sector_heatmap`'s tool is `DataClass.PUBLIC`, so no masked-teaser assertion is
needed here (see `test_phase11_governance.py` for the Sponsor-tier masking coverage).
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
    """A real `TestClient` whose `get_sector_heatmap`/`get_advance_decline` family reads a real
    `ParquetStore` rooted at `tmp_path`. `tools.market.build_registration_table` builds its own
    `ParquetStore` straight from a fresh `config.settings.get_settings()` call (not the `settings`
    object handed to `build_test_client`), so the env var is what has to move, not a `Settings`
    field."""
    monkeypatch.setenv("PARQUET_ROOT", str(tmp_path))
    client = build_test_client(settings, agent_answer=agent_answer)
    stub_router, _ = make_router(Symbol(id="2330", market=Market.TW), {})
    registry = build_default_tool_registry(stub_router)
    object.__setattr__(client.app.state.app_state, "tool_registry", registry)
    return client


class TestSectorHeatmapRoute:
    def test_reports_unavailable_when_no_partition(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/sector-heatmap")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["unavailable_reason"]
        assert body["industries"] == []

    def test_groups_by_industry_for_one_trading_day(
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
                {"symbol": "2330", "trade_date": "2025-05-07", "close": 110.0},
                {"symbol": "2881", "trade_date": "2025-05-07", "close": 96.0},
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
            "/api/v1/market/sector-heatmap",
            params={"trade_date": trading_day.isoformat()},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        by_industry = {row["industry"]: row for row in body["industries"]}
        assert by_industry["半導體業"]["stock_count"] == 1
        assert by_industry["半導體業"]["avg_pct_change"] == 10.0
        assert by_industry["金融保險業"]["avg_pct_change"] == -4.0
        assert "locked" not in body

    def test_period_widens_the_lookback_window(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`period=week` must compare against 5 trading days back, not the immediate prior day —
        seed a run of daily partitions so the two periods land on genuinely different prior
        closes and assert the resulting avg_pct_change differs accordingly."""
        store = ParquetStore(root=tmp_path)
        days = [date_(2025, 5, d) for d in range(1, 9)]  # 8 daily partitions, 2025-05-01..08
        closes_by_day = {
            1: 100.0,
            2: 101.0,
            3: 102.0,
            4: 103.0,
            5: 104.0,
            6: 105.0,
            7: 106.0,
            8: 120.0,
        }
        for day in days:
            store.write_partition(
                "TaiwanStockPrice",
                "TW",
                day,
                [
                    {
                        "symbol": "2330",
                        "trade_date": day.isoformat(),
                        "close": closes_by_day[day.day],
                    }
                ],
            )
        store.write_partition(
            "TaiwanStockInfo",
            "TW",
            days[-1],
            [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "name": "台積電",
                    "industry_category": "半導體業",
                    "security_type": "twse",
                    "as_of": days[-1].isoformat(),
                }
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        immediate = client.get(
            "/api/v1/market/sector-heatmap",
            params={"trade_date": days[-1].isoformat(), "period": "immediate"},
        ).json()
        week = client.get(
            "/api/v1/market/sector-heatmap",
            params={"trade_date": days[-1].isoformat(), "period": "week"},
        ).json()

        assert immediate["data_available"] is True
        assert week["data_available"] is True
        immediate_pct = immediate["industries"][0]["avg_pct_change"]
        week_pct = week["industries"][0]["avg_pct_change"]
        # immediate: (120-106)/106*100 ≈ 13.21%; week (5 trading days back, 2025-05-02=101.0):
        # (120-101)/101*100 ≈ 18.81% — genuinely different windows, not the same number twice.
        assert immediate_pct != week_pct
        assert week["period"] == "week"
        assert immediate["period"] == "immediate"

    def test_size_by_reports_the_requested_metric(
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
            [{"symbol": "2330", "trade_date": "2025-05-06", "close": 100.0}],
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
                    "volume": 123456,
                    "turnover": 13580160.0,
                }
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
                }
            ],
        )
        store.write_partition(
            "TaiwanStockMarketValue",
            "TW",
            trading_day,
            [{"symbol": "2330", "trade_date": "2025-05-07", "market_value": 2_860_000_000}],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/sector-heatmap",
            params={"trade_date": trading_day.isoformat(), "size_by": "market_value"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["size_by"] == "market_value"
        row = body["industries"][0]
        assert row["total_volume"] == 123456
        assert row["total_market_value"] == 2_860_000_000
