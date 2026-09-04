"""The new `/market/dividend-calendar` REST route, driven through the REAL FastAPI route via
`TestClient` — same stub-router-swap pattern as `test_etf_list_route.py`. `get_dividend_calendar`'s
tool is `DataClass.PUBLIC`, so no masked-teaser assertion is needed here.
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
    stub_router, _ = make_router(Symbol(id="0050", market=Market.TW), {})
    registry = build_default_tool_registry(stub_router)
    object.__setattr__(client.app.state.app_state, "tool_registry", registry)
    return client


class TestDividendCalendarRoute:
    def test_reports_unavailable_when_no_partition(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/dividend-calendar")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["unavailable_reason"]
        assert body["events"] == []

    def test_lists_etf_events_landing_in_the_requested_month_only(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        # A real trading-day calendar so `previous_trading_day` has ground truth to walk from —
        # a handful of real May 2025 TW trading days, ex-date included.
        for trade_date in ("2025-05-05", "2025-05-06", "2025-05-07", "2025-05-08", "2025-05-09"):
            store.write_partition(
                "TaiwanStockPrice",
                "TW",
                date_.fromisoformat(trade_date),
                [{"symbol": "0050", "trade_date": trade_date, "close": 100.0}],
            )
        store.write_partition(
            "etf_nav",
            "TW",
            date_(2025, 5, 7),
            [
                {
                    "symbol": "0050",
                    "market": "TW",
                    "as_of": "2025-05-07",
                    "fund_name": "元大台灣50",
                    "units_outstanding": 1000,
                    "estimated_nav": 109.5,
                }
            ],
        )
        # An ETF event whose ex-date lands in May 2025 — must appear.
        store.write_partition(
            "TaiwanStockDividend",
            "TW",
            date_(2025, 5, 8),
            [
                {
                    "symbol": "0050",
                    "market": "TW",
                    "announced_date": "2025-04-01",
                    "fiscal_year": "2025",
                    "cash_dividend": 1.2,
                    "cash_ex_dividend_date": "2025-05-08",
                    "cash_dividend_payment_date": "2025-06-10",
                }
            ],
        )
        # A plain-equity event in the same month — must never leak into an ETF-only calendar.
        store.write_partition(
            "TaiwanStockDividend",
            "TW",
            date_(2025, 5, 9),
            [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "announced_date": "2025-04-01",
                    "fiscal_year": "2025",
                    "cash_dividend": 11.0,
                    "cash_ex_dividend_date": "2025-05-09",
                }
            ],
        )
        # An ETF event whose ex-date lands OUTSIDE May — must never appear in the May response.
        store.write_partition(
            "TaiwanStockDividend",
            "TW",
            date_(2025, 6, 5),
            [
                {
                    "symbol": "0050",
                    "market": "TW",
                    "announced_date": "2025-05-01",
                    "fiscal_year": "2025",
                    "cash_dividend": 0.8,
                    "cash_ex_dividend_date": "2025-06-05",
                }
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/dividend-calendar",
            params={"market": "TW", "year": 2025, "month": 5},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        assert body["year"] == 2025
        assert body["month"] == 5
        assert len(body["events"]) == 1
        event = body["events"][0]
        assert event["symbol"] == "0050"
        assert event["name"] == "元大台灣50"
        assert event["ex_dividend_date"] == "2025-05-08"
        assert event["last_buy_date"] == "2025-05-07"  # the trading day strictly before ex-date
        assert event["payment_date"] == "2025-06-10"
        assert event["cash_dividend"] == pytest.approx(1.2)

    def test_event_with_no_published_payment_date_is_none_not_fabricated(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        for trade_date in ("2025-05-06", "2025-05-07", "2025-05-08"):
            store.write_partition(
                "TaiwanStockPrice",
                "TW",
                date_.fromisoformat(trade_date),
                [{"symbol": "0056", "trade_date": trade_date, "close": 40.0}],
            )
        store.write_partition(
            "etf_nav",
            "TW",
            date_(2025, 5, 7),
            [
                {
                    "symbol": "0056",
                    "market": "TW",
                    "as_of": "2025-05-07",
                    "fund_name": "元大高股息",
                    "units_outstanding": 1000,
                    "estimated_nav": 39.5,
                }
            ],
        )
        store.write_partition(
            "TaiwanStockDividend",
            "TW",
            date_(2025, 5, 8),
            [
                {
                    "symbol": "0056",
                    "market": "TW",
                    "announced_date": "2025-04-01",
                    "fiscal_year": "2025",
                    "cash_dividend": 0.5,
                    "cash_ex_dividend_date": "2025-05-08",
                    # No cash_dividend_payment_date at all — FinMind hasn't published one yet.
                }
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/dividend-calendar",
            params={"market": "TW", "year": 2025, "month": 5},
        )

        assert resp.status_code == 200
        event = next(e for e in resp.json()["events"] if e["symbol"] == "0056")
        assert event["payment_date"] is None

    def test_event_with_no_etf_nav_name_falls_back_to_the_symbol(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`dividends` and `etf_nav` are two independent ingest jobs — a real dividend event
        must still appear (named by its own symbol) even when `etf_nav` has zero ingested
        partitions at all, the exact gap a fresh/partially-backfilled dev store hits. This
        mirrors `test_etf_list_route.py`'s own `test_missing_dataset_fields_are_none_not_
        fabricated` convention (fall back to the symbol, never drop the row)."""
        store = ParquetStore(root=tmp_path)
        for trade_date in ("2025-05-06", "2025-05-07", "2025-05-08"):
            store.write_partition(
                "TaiwanStockPrice",
                "TW",
                date_.fromisoformat(trade_date),
                [{"symbol": "00929", "trade_date": trade_date, "close": 20.0}],
            )
        # Deliberately NO `etf_nav` partition written at all.
        store.write_partition(
            "TaiwanStockDividend",
            "TW",
            date_(2025, 5, 8),
            [
                {
                    "symbol": "00929",
                    "market": "TW",
                    "announced_date": "2025-04-01",
                    "fiscal_year": "2025",
                    "cash_dividend": 0.1,
                    "cash_ex_dividend_date": "2025-05-08",
                }
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/dividend-calendar",
            params={"market": "TW", "year": 2025, "month": 5},
        )

        assert resp.status_code == 200
        event = next(e for e in resp.json()["events"] if e["symbol"] == "00929")
        assert event["name"] == "00929"  # falls back to the symbol, never dropped

    def test_year_and_month_must_both_be_given_or_both_omitted(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pins `market_rest.py`'s `_dispatch` catching `pydantic.ValidationError` and
        rendering it as a 422 `invalid_request` envelope (matching `fund_data.py`'s own
        convention) rather than falling through to the catch-all 500 handler."""
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/dividend-calendar", params={"year": 2025})

        assert resp.status_code == 422
        assert resp.json()["code"] == "invalid_request"
