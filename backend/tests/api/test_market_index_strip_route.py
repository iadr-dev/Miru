"""The new `/market/index-strip`, `/market/tick-snapshot`, and `/market/futures-snapshot` REST
routes, driven through the REAL FastAPI route via `TestClient` — mirrors
`tests/api/test_dividend_schedule_route.py`'s stub-router-swap pattern. `get_market_index_strip`
is `DataClass.PUBLIC` (no masked-teaser branch); `get_tick_snapshot`/`get_futures_snapshot` are
`DataClass.SPONSOR` (masked-teaser coverage is here, not deferred to `test_phase11_governance.py`,
since these two routes are new).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from config.settings import Settings
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.api.conftest import build_test_client
from tests.tools.conftest import make_router
from tools import build_default_tool_registry


def _result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="stub caveat — recorded fixture, not live",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _client_with_stub(
    settings: Settings, agent_answer: str, results: dict[Capability, SourceResult]
) -> TestClient:
    client = build_test_client(settings, agent_answer=agent_answer)
    symbol = Symbol(id="001", market=Market.TW)
    stub_router, _ = make_router(symbol, results)
    registry = build_default_tool_registry(stub_router)
    object.__setattr__(client.app.state.app_state, "tool_registry", registry)
    return client


class TestIndexStripRoute:
    def test_returns_public_rows_with_no_masking(
        self, settings: Settings, agent_answer: str
    ) -> None:
        client = _client_with_stub(
            settings,
            agent_answer,
            {
                Capability.TAIEX_5S_INDEX: _result(
                    {"rows": [{"market": "TW", "trade_date": "2026-09-04", "taiex": 24240.0}]}
                ),
                Capability.TOTAL_RETURN_INDEX: _result({"rows": []}),
                Capability.EXCHANGE_RATE: _result({"rows": []}),
                Capability.GOLD_PRICE: _result({"rows": []}),
                Capability.CRUDE_OIL_PRICES: _result({"rows": []}),
            },
        )

        resp = client.get("/api/v1/market/index-strip")

        assert resp.status_code == 200
        body = resp.json()
        assert "locked" not in body
        assert body["rows"][0]["key"] == "taiex"
        assert body["rows"][0]["value"] == 24240.0


class TestTickSnapshotRoute:
    def test_unentitled_caller_gets_masked_teaser(
        self, settings: Settings, agent_answer: str
    ) -> None:
        client = _client_with_stub(
            settings, agent_answer, {Capability.TICK_SNAPSHOT: _result({"rows": []})}
        )

        resp = client.get("/api/v1/market/tick-snapshot", params={"symbol": "036"})

        assert resp.status_code == 200
        assert resp.json()["locked"] is True

    def test_requires_symbol(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_stub(
            settings, agent_answer, {Capability.TICK_SNAPSHOT: _result({"rows": []})}
        )

        resp = client.get("/api/v1/market/tick-snapshot")

        assert resp.status_code == 422


class TestFuturesSnapshotRoute:
    def test_unentitled_caller_gets_masked_teaser(
        self, settings: Settings, agent_answer: str
    ) -> None:
        client = _client_with_stub(
            settings, agent_answer, {Capability.FUTURES_SNAPSHOT: _result({"rows": []})}
        )

        resp = client.get("/api/v1/market/futures-snapshot", params={"symbol": "TXF"})

        assert resp.status_code == 200
        assert resp.json()["locked"] is True

    def test_requires_symbol(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_stub(
            settings, agent_answer, {Capability.FUTURES_SNAPSHOT: _result({"rows": []})}
        )

        resp = client.get("/api/v1/market/futures-snapshot")

        assert resp.status_code == 422
