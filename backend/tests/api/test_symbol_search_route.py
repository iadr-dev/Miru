"""`GET /search` — dispatches `market=TW` to the ingested-roster tool
(`get_symbol_directory_search`) and `market=US/GB/JP/EU` to the yfinance-backed tool
(`get_symbol_search`); any other market still 501s. Pins the fix that used to hard-reject TW."""

from __future__ import annotations

from datetime import date as date_
from pathlib import Path

import pytest

from config.settings import Settings
from ingest.store import ParquetStore
from tests.api.conftest import client_with_symbol_store


def test_tw_market_dispatches_the_ingested_roster_tool(
    settings: Settings,
    agent_answer: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParquetStore(root=tmp_path)
    store.write_partition(
        "TaiwanStockInfo",
        "TW",
        date_(2025, 5, 7),
        [
            {
                "symbol": "2330",
                "market": "TW",
                "name": "台積電",
                "industry_category": "半導體業",
                "security_type": "twse",
                "as_of": "2025-05-07",
            },
        ],
    )
    client = client_with_symbol_store(settings, agent_answer, tmp_path, monkeypatch)

    resp = client.get("/api/v1/search", params={"q": "2330", "market": "TW"})

    assert resp.status_code == 200
    body = resp.json()
    assert [row["symbol"] for row in body["rows"]] == ["2330"]


def test_unsupported_market_still_returns_501(
    settings: Settings,
    agent_answer: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # HK is a real `Market` enum member with no search tool built for it yet — a valid market,
    # just an unserved one, so this exercises the route's own 501 branch rather than FastAPI's
    # query-param 422 validation.
    client = client_with_symbol_store(settings, agent_answer, tmp_path, monkeypatch)

    resp = client.get("/api/v1/search", params={"q": "x", "market": "HK"})

    assert resp.status_code == 501
