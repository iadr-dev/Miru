"""The new `/market/tag-catalog` and `/market/tag-screener` REST routes, driven through the REAL
FastAPI route via `TestClient` — proving each dispatches through the real registry
(`tools/screener_tools.py`) exactly as `tests/api/test_fund_data_routes.py`'s stub-router-swap
pattern does for the funds routes. `run_tag_screener`'s tool is `DataClass.PUBLIC`, so unlike the
Sponsor-tier routes covered in `test_phase11_governance.py`, no masked-teaser assertion is needed
here.
"""

from __future__ import annotations

import asyncio
from calendar.trading_days import TradingDayCalendar
from datetime import date as date_
from pathlib import Path

from fastapi.testclient import TestClient

from analytics.screener.batch import (
    INSTITUTIONAL_FLOW_DATASET,
    PRICE_EOD_DATASET,
    job_screener_flags,
)
from config.settings import Settings
from ingest.context import IngestContext
from ingest.leader import AlwaysLeader
from ingest.reader import ParquetMarketDataReader
from ingest.store import ParquetStore
from schemas.enums import Market, TLSPolicy
from schemas.market import Symbol
from tests.api.conftest import build_test_client
from tests.tools.conftest import make_router
from tools import build_default_tool_registry

_RUN_DATE = date_(2024, 1, 2)  # a Tuesday, a trading day


def _ctx(root: Path) -> IngestContext:
    return IngestContext(
        finmind=None,  # type: ignore[arg-type]  # this fixture makes no FinMind call
        store=ParquetStore(root=root),
        leader=AlwaysLeader(),
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


def _price_rows_for(
    symbol: str, closes: list[float], dates: list[date_]
) -> list[dict[str, object]]:
    return [
        {
            "symbol": symbol,
            "market": "TW",
            "trade_date": day.isoformat(),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
            "turnover": None,
        }
        for close, day in zip(closes, dates, strict=True)
    ]


def _flow_rows_for(
    symbol: str, investor: str, nets: list[float], dates: list[date_]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for net, day in zip(nets, dates, strict=True):
        buy = int(net) if net > 0 else 0
        sell = int(-net) if net < 0 else 0
        rows.append(
            {
                "symbol": symbol,
                "market": "TW",
                "trade_date": day.isoformat(),
                "investor": investor,
                "buy": buy,
                "sell": sell,
            }
        )
    return rows


def _seed_upstream(store: ParquetStore, calendar: TradingDayCalendar) -> None:
    """Same fixture shape as `tests/analytics/test_screener_e2e.py`'s `_seed_upstream`: BREAKOUT
    crosses above its 20-day MA on the run date, FOREIGN is foreign-net-buy 3 days running,
    NEITHER matches nothing."""
    window = calendar.business_days_between(calendar.shift_business_days(_RUN_DATE, -89), _RUN_DATE)
    breakout_closes = [100.0] * (len(window) - 1) + [130.0]
    neither_closes = [float(50 + i) for i in range(len(window))]

    for i, day in enumerate(window):
        price_rows = _price_rows_for("BREAKOUT", [breakout_closes[i]], [day]) + _price_rows_for(
            "NEITHER", [neither_closes[i]], [day]
        )
        store.write_partition(PRICE_EOD_DATASET, "TW", day, price_rows)

    chip_window = window[-5:]
    neither_nets = [200.0, -100.0, 400.0, -500.0, -300.0]
    for i, day in enumerate(chip_window):
        flow_rows = _flow_rows_for("FOREIGN", "Foreign_Investor", [500.0], [day]) + _flow_rows_for(
            "NEITHER", "Foreign_Investor", [neither_nets[i]], [day]
        )
        store.write_partition(INSTITUTIONAL_FLOW_DATASET, "TW", day, flow_rows)


def _client_with_reader(
    settings: Settings, agent_answer: str, tmp_path: Path, *, seed: bool
) -> TestClient:
    """A real `TestClient` whose tool registry is built with a real `ParquetMarketDataReader`
    rooted at `tmp_path` — optionally seeded through the REAL `job_screener_flags` batch job, so
    the route is proven against the real screener stack, not a stub."""
    ctx = _ctx(tmp_path)
    if seed:
        _seed_upstream(ctx.store, ctx.calendar)
        asyncio.run(job_screener_flags(_RUN_DATE, ctx))

    client = build_test_client(settings, agent_answer=agent_answer)
    # An empty-capability router — this test exercises only the screener group, which is
    # parameterized by the Parquet reader, not the router; `make_router` still needs a `Symbol`
    # to build one, matching `test_fund_data_routes.py`'s own stub-router-swap pattern.
    stub_router, _ = make_router(Symbol(id="2330", market=Market.TW), {})
    reader = ParquetMarketDataReader(root=tmp_path)
    registry = build_default_tool_registry(stub_router, market_data_reader=reader)
    object.__setattr__(client.app.state.app_state, "tool_registry", registry)
    return client


class TestTagCatalogRoute:
    def test_catalog_shape(self, settings: Settings, agent_answer: str, tmp_path: Path) -> None:
        client = _client_with_reader(settings, agent_answer, tmp_path, seed=False)
        resp = client.get("/api/v1/market/tag-catalog")
        assert resp.status_code == 200
        body = resp.json()
        categories = {c["category"] for c in body["categories"]}
        assert "技術面" in categories
        assert "籌碼面" in categories
        # Every category carries at least one tag entry with bilingual labels.
        for category in body["categories"]:
            assert len(category["tags"]) > 0
            tag = category["tags"][0]
            assert tag["key"]
            assert tag["label_zh"]
            assert tag["label_en"]


class TestTagScreenerRoute:
    def test_and_query(self, settings: Settings, agent_answer: str, tmp_path: Path) -> None:
        client = _client_with_reader(settings, agent_answer, tmp_path, seed=True)
        resp = client.get(
            "/api/v1/market/tag-screener",
            params={"tags": ["breakout_above_ma20"], "match": "AND"},
        )
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["total_matches"] == 1
        assert result["hits"][0]["symbol"] == "BREAKOUT"
        assert result["hits"][0]["matched_tags"] == ["breakout_above_ma20"]

    def test_or_query(self, settings: Settings, agent_answer: str, tmp_path: Path) -> None:
        client = _client_with_reader(settings, agent_answer, tmp_path, seed=True)
        resp = client.get(
            "/api/v1/market/tag-screener",
            params={
                "tags": ["breakout_above_ma20", "foreign_net_buy_streak_3"],
                "match": "OR",
            },
        )
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["total_matches"] == 2
        by_symbol = {hit["symbol"]: set(hit["matched_tags"]) for hit in result["hits"]}
        assert by_symbol == {
            "BREAKOUT": {"breakout_above_ma20"},
            "FOREIGN": {"foreign_net_buy_streak_3"},
        }

    def test_unknown_tag_degrades_gracefully(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        # An unknown tag key resolves to zero matches, never an error — asserted via `detail`,
        # never a 4xx, per `TagCondition`'s own contract.
        client = _client_with_reader(settings, agent_answer, tmp_path, seed=True)
        resp = client.get(
            "/api/v1/market/tag-screener",
            params={"tags": ["not_a_real_tag_key"], "match": "AND"},
        )
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["total_matches"] == 0
        assert "unknown tag key" in result["detail"]

    def test_requires_at_least_one_tag(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_reader(settings, agent_answer, tmp_path, seed=False)
        resp = client.get("/api/v1/market/tag-screener")
        assert resp.status_code == 422
        assert resp.json()["code"] == "invalid_request"
