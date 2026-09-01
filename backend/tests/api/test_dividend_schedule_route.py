"""The new `/market/dividend-schedule` REST route, driven through the REAL FastAPI route via
`TestClient` — proving it dispatches through the real registry (`tools/market_tools.py`) exactly
as `tests/api/test_fund_data_routes.py`'s stub-router-swap pattern does for the funds routes.
`get_dividend_schedule`'s tool is `DataClass.PUBLIC`, so no masked-teaser assertion is needed here
(see `test_phase11_governance.py` for the Sponsor-tier masking coverage).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

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


_DIVIDEND_PAYLOAD = _result(
    {
        "events": [
            {
                "symbol": "0056",
                "market": "TW",
                "announced_date": date(2024, 6, 1).isoformat(),
                "fiscal_year": "2023",
                "cash_dividend": 1.5,
                "stock_dividend": None,
                "cash_ex_dividend_date": date(2024, 7, 18).isoformat(),
                "stock_ex_dividend_date": None,
            }
        ]
    }
)


def _client_with_stub(settings: Settings, agent_answer: str) -> TestClient:
    """A real `TestClient` whose tool registry answers `Capability.DIVIDENDS` with a canned,
    healthy result — enough to prove the route wires through the real tool, not the tool's own
    business logic."""
    client = build_test_client(settings, agent_answer=agent_answer)
    symbol = Symbol(id="0056", market=Market.TW)
    stub_router, _ = make_router(symbol, {Capability.DIVIDENDS: _DIVIDEND_PAYLOAD})
    registry = build_default_tool_registry(stub_router)
    object.__setattr__(client.app.state.app_state, "tool_registry", registry)
    return client


class TestDividendScheduleRoute:
    def test_dividend_schedule_route(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_stub(settings, agent_answer)
        resp = client.get(
            "/api/v1/market/dividend-schedule",
            params={"symbol": "0056", "market": Market.TW.value},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "locked" not in body
        assert body["events"][0]["symbol"] == "0056"
        assert body["events"][0]["cash_dividend"] == 1.5

    def test_dividend_schedule_route_with_date_range(
        self, settings: Settings, agent_answer: str
    ) -> None:
        client = _client_with_stub(settings, agent_answer)
        resp = client.get(
            "/api/v1/market/dividend-schedule",
            params={
                "symbol": "0056",
                "market": Market.TW.value,
                "start_date": date(2024, 1, 1).isoformat(),
                "end_date": date(2024, 12, 31).isoformat(),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["events"][0]["fiscal_year"] == "2023"

    def test_dividend_schedule_requires_symbol(self, settings: Settings, agent_answer: str) -> None:
        client = _client_with_stub(settings, agent_answer)
        resp = client.get("/api/v1/market/dividend-schedule")
        assert resp.status_code == 422
