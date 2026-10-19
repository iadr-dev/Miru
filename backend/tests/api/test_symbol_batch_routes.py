"""`/market/symbol-info` and `/market/price-history` — both take a single comma-joined `symbols`
query value (`web/src/lib/api/market.ts`'s `symbolsSearchParams` sends `symbols=2330,2454`, never
repeated `symbols=` params). A prior route signature (`symbols: list[str] = Query(...)`) only
populates from repeated keys, so a comma-joined value was parsed as one unmatched literal string
and every multi-symbol batch call silently returned zero rows — these tests pin the fix (splitting
`symbols` server-side) against the real FastAPI route via `TestClient`, same stub-router-swap
pattern `test_etf_list_route.py` uses.
"""

from __future__ import annotations

from datetime import date as date_
from pathlib import Path

import pytest

from config.settings import Settings
from ingest.store import ParquetStore
from tests.api.conftest import client_with_symbol_store


class TestSymbolInfoRoute:
    def test_comma_joined_symbols_resolve_every_requested_symbol(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        roster_day = date_(2025, 5, 7)
        store.write_partition(
            "TaiwanStockInfo",
            "TW",
            roster_day,
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
                    "symbol": "2454",
                    "market": "TW",
                    "name": "聯發科",
                    "industry_category": "半導體業",
                    "security_type": "twse",
                    "as_of": "2025-05-07",
                },
            ],
        )
        client = client_with_symbol_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get("/api/v1/market/symbol-info", params={"symbols": "2330,2454"})

        assert resp.status_code == 200
        body = resp.json()
        symbols = {row["symbol"] for row in body["rows"]}
        assert symbols == {"2330", "2454"}


class TestPriceHistoryRoute:
    def test_comma_joined_symbols_resolve_every_requested_symbol(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ParquetStore(root=tmp_path)
        trading_day = date_(2025, 5, 7)
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            trading_day,
            [
                {
                    "symbol": "2330",
                    "trade_date": "2025-05-07",
                    "open": 595.0,
                    "high": 605.0,
                    "low": 590.0,
                    "close": 600.0,
                    "volume": 1000,
                },
                {
                    "symbol": "2454",
                    "trade_date": "2025-05-07",
                    "open": 895.0,
                    "high": 905.0,
                    "low": 890.0,
                    "close": 900.0,
                    "volume": 2000,
                },
            ],
        )
        client = client_with_symbol_store(settings, agent_answer, tmp_path, monkeypatch)

        resp = client.get(
            "/api/v1/market/price-history", params={"symbols": "2330,2454", "days": 5}
        )

        assert resp.status_code == 200
        body = resp.json()
        symbols = {row["symbol"] for row in body["rows"]}
        assert symbols == {"2330", "2454"}

    def test_days_cap_is_a_trading_year(
        self,
        settings: Settings,
        agent_answer: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = client_with_symbol_store(settings, agent_answer, tmp_path, monkeypatch)

        ok = client.get("/api/v1/market/price-history", params={"symbols": "2330", "days": 260})
        too_long = client.get(
            "/api/v1/market/price-history", params={"symbols": "2330", "days": 261}
        )

        assert ok.status_code == 200
        assert too_long.status_code == 422
