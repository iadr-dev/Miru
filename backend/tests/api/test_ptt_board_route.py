"""`/market/ptt-board` carries the source health, so a PTT outage never reads as "no posts"."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from dataaccess.ptt import PttGateway
from schemas.enums import DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.api.conftest import build_test_client


def _result(status: HealthStatus, payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="PTT",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=status, detail="x"),
    )


def _client(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, agent_answer: str, result: SourceResult
) -> TestClient:
    async def fake_fetch_board(
        self: PttGateway,
        *,
        board: str,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
        lookback_days: int = 1,
    ) -> SourceResult:
        return result

    monkeypatch.setattr(PttGateway, "fetch_board", fake_fetch_board)
    return build_test_client(settings, agent_answer=agent_answer)


def test_ptt_board_route_reports_unreachable(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, agent_answer: str
) -> None:
    client = _client(monkeypatch, settings, agent_answer, _result(HealthStatus.UNREACHABLE, {}))
    resp = client.get("/api/v1/market/ptt-board", params={"board": "Stock"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["posts"] == []
    assert body["health_status"] == "unreachable"
    assert body["source_id"] == "PTT"


def test_ptt_board_route_reports_ok_with_posts(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, agent_answer: str
) -> None:
    post = {
        "board": "Stock",
        "board_tag": "stock_buzz",
        "title": "t",
        "author": "a",
        "push_count": "5",
        "link": "https://www.ptt.cc/bbs/Stock/x.html",
    }
    client = _client(
        monkeypatch, settings, agent_answer, _result(HealthStatus.OK, {"posts": [post]})
    )
    body = client.get("/api/v1/market/ptt-board").json()
    assert body["health_status"] == "ok"
    assert body["posts"][0]["title"] == "t"
