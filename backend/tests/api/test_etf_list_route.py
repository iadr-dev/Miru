"""The new `/market/etf-list` REST route, driven through the REAL FastAPI route via
`TestClient` — same stub-router-swap pattern as `test_sector_heatmap_route.py`. `get_etf_list`'s
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
    """A real `TestClient` whose `get_etf_list` reads a real `ParquetStore` rooted at `tmp_path`
    — see `test_sector_heatmap_route.py::_client_with_store`'s own docstring for why the env var
    (not the `settings` object) is what has to move."""
    monkeypatch.setenv("PARQUET_ROOT", str(tmp_path))
    client = build_test_client(settings, agent_answer=agent_answer)
    stub_router, _ = make_router(Symbol(id="0050", market=Market.TW), {})
    registry = build_default_tool_registry(stub_router)
    object.__setattr__(client.app.state.app_state, "tool_registry", registry)
    return client


class TestEtfListRoute:
    def test_reports_unavailable_when_no_partition(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/etf-list")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["unavailable_reason"]
        assert body["rows"] == []

    def test_lists_etf_symbols_only_with_joined_columns(
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
                {"symbol": "0050", "trade_date": "2025-05-06", "close": 100.0},
                # A plain-equity row — must never leak into an ETF-only list.
                {"symbol": "2330", "trade_date": "2025-05-06", "close": 500.0},
            ],
        )
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            trading_day,
            [
                {
                    "symbol": "0050",
                    "trade_date": "2025-05-07",
                    "close": 110.0,
                    "volume": 1000,
                    "turnover": 110_000.0,
                },
                {"symbol": "2330", "trade_date": "2025-05-07", "close": 510.0},
            ],
        )
        store.write_partition(
            "etf_nav",
            "TW",
            trading_day,
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
        store.write_partition(
            "etf_aum",
            "TW",
            trading_day,
            [{"symbol": "0050", "market": "TW", "as_of": "2025-05-07", "aum": 2_000_000_000.0}],
        )
        store.write_partition(
            "etf_expense_ratio",
            "TW",
            trading_day,
            [
                {
                    "symbol": "0050",
                    "market": "TW",
                    "as_of": "2025-05-07",
                    "management_fee_pct": 0.3,
                    "custody_fee_pct": 0.035,
                    "total_expense_ratio_pct_approx": 0.335,
                }
            ],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/etf-list",
            params={"trade_date": trading_day.isoformat()},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        by_symbol = {row["symbol"]: row for row in body["rows"]}
        assert "2330" not in by_symbol  # plain equity, never in an ETF-only list
        row = by_symbol["0050"]
        assert row["name"] == "元大台灣50"
        assert row["close"] == 110.0
        assert row["change_abs"] == pytest.approx(10.0)
        assert row["change_pct"] == pytest.approx(10.0)
        assert row["volume"] == 1000
        assert row["turnover"] == 110_000.0
        assert row["aum"] == 2_000_000_000.0
        assert row["expense_ratio_pct_approx"] == pytest.approx(0.335)
        assert row["symbol_type"] == "etf"
        assert "locked" not in body

    def test_missing_dataset_fields_are_none_not_fabricated(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A symbol with only a `price_eod` bar and no `etf_nav`/`etf_aum`/`etf_expense_ratio`
        row must still appear, with every unbacked field `None` — never a fabricated value."""
        store = ParquetStore(root=tmp_path)
        prior_day = date_(2025, 5, 6)
        trading_day = date_(2025, 5, 7)
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            prior_day,
            [{"symbol": "00685L", "trade_date": "2025-05-06", "close": 12.0}],
        )
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            trading_day,
            [{"symbol": "00685L", "trade_date": "2025-05-07", "close": 12.5}],
        )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/etf-list",
            params={"trade_date": trading_day.isoformat()},
        )

        assert resp.status_code == 200
        body = resp.json()
        row = next(r for r in body["rows"] if r["symbol"] == "00685L")
        assert row["symbol_type"] == "etf_leveraged"
        assert row["name"] == "00685L"  # falls back to the symbol when no etf_nav name exists
        assert row["aum"] is None
        assert row["expense_ratio_pct_approx"] is None
        assert row["distribution_yield_ttm_pct"] is None
        assert row["distribution_frequency"] is None

    def test_ttm_distribution_yield_and_frequency_from_real_dividend_events(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Four real quarterly cash-distribution events within the trailing 12 months must
        yield `distribution_frequency == "季配"` and a TTM yield computed from their sum ÷
        close — never asserted from the issuer's declared policy, only observed cadence."""
        store = ParquetStore(root=tmp_path)
        prior_day = date_(2025, 5, 6)
        trading_day = date_(2025, 5, 7)
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            prior_day,
            [{"symbol": "0056", "trade_date": "2025-05-06", "close": 40.0}],
        )
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            trading_day,
            [{"symbol": "0056", "trade_date": "2025-05-07", "close": 40.0}],
        )
        dividend_dates = [
            date_(2024, 7, 20),
            date_(2024, 10, 20),
            date_(2025, 1, 20),
            date_(2025, 4, 20),
        ]
        for ex_date in dividend_dates:
            store.write_partition(
                "TaiwanStockDividend",
                "TW",
                ex_date,
                [
                    {
                        "symbol": "0056",
                        "market": "TW",
                        "announced_date": ex_date.isoformat(),
                        "fiscal_year": "2025",
                        "cash_dividend": 0.5,
                        "cash_ex_dividend_date": ex_date.isoformat(),
                    }
                ],
            )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/etf-list",
            params={"trade_date": trading_day.isoformat()},
        )

        assert resp.status_code == 200
        row = next(r for r in resp.json()["rows"] if r["symbol"] == "0056")
        assert row["distribution_frequency"] == "季配"
        assert row["distribution_yield_ttm_pct"] == pytest.approx(0.5 * 4 / 40.0 * 100)

    def test_return_1w_pct_from_a_real_trailing_week_of_closes(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression guard: `_etf_week_closes` must read a row's `trade_date` field (a string),
        not `read_range`'s own `date` metadata column (a `datetime.date` object) — reading the
        latter silently dropped every row (an `isinstance(..., str)` check never matched a
        `date` object), so `return_1w_pct` came back `None` for every ETF. `2025-05-07`'s
        trailing 5-trading-day window (naive Mon-Fri calendar, no holiday overlay) is
        `2025-04-30 .. 2025-05-07`; first close 100.0, last close 110.0 -> +10%."""
        store = ParquetStore(root=tmp_path)
        week_closes = {
            date_(2025, 4, 30): 100.0,
            date_(2025, 5, 1): 102.0,
            date_(2025, 5, 2): 104.0,
            date_(2025, 5, 5): 106.0,
            date_(2025, 5, 6): 108.0,
            date_(2025, 5, 7): 110.0,
        }
        for day, close in week_closes.items():
            store.write_partition(
                "TaiwanStockPrice",
                "TW",
                day,
                [{"symbol": "0050", "trade_date": day.isoformat(), "close": close}],
            )
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/etf-list",
            params={"trade_date": "2025-05-07"},
        )

        assert resp.status_code == 200
        row = next(r for r in resp.json()["rows"] if r["symbol"] == "0050")
        assert row["return_1w_pct"] == pytest.approx(10.0)

    def test_expense_ratio_falls_back_past_a_stale_empty_partition(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression guard: the "latest dataset date" scan must skip a date whose partition
        manifest exists but carries zero real rows (a placeholder from a run that found nothing
        to write) and fall back to the most recent date that actually has rows — confirmed live
        this session against a real stale SITCA `etf_expense_ratio` placeholder, which made
        every ETF's `expense_ratio_pct_approx` come back `None` even though a real, older
        expense-ratio partition existed. `write_partition` with an empty row list produces
        exactly this shape (the `_empty`-placeholder column `list_partition_dates` excludes)."""
        store = ParquetStore(root=tmp_path)
        real_day = date_(2025, 4, 1)
        empty_day = date_(2025, 5, 1)
        prior_day = date_(2025, 5, 6)
        trading_day = date_(2025, 5, 7)
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            prior_day,
            [{"symbol": "0050", "trade_date": "2025-05-06", "close": 100.0}],
        )
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            trading_day,
            [{"symbol": "0050", "trade_date": "2025-05-07", "close": 110.0}],
        )
        store.write_partition(
            "etf_expense_ratio",
            "TW",
            real_day,
            [
                {
                    "symbol": "0050",
                    "market": "TW",
                    "as_of": "2025-04-01",
                    "management_fee_pct": 0.3,
                    "custody_fee_pct": 0.035,
                    "total_expense_ratio_pct_approx": 0.335,
                }
            ],
        )
        store.write_partition("etf_expense_ratio", "TW", empty_day, [])  # the stale placeholder
        client = _client_with_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/etf-list",
            params={"trade_date": trading_day.isoformat()},
        )

        assert resp.status_code == 200
        row = next(r for r in resp.json()["rows"] if r["symbol"] == "0050")
        assert row["expense_ratio_pct_approx"] == pytest.approx(0.335)
