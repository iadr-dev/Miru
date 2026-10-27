"""`/market/news-feed` and `/market/macro-calendar` carry the source health, so an outage never
reads as "no news" / "no events"."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from dataaccess.financecalendar import FinanceCalendarGateway
from dataaccess.news import NewsGateway
from schemas.enums import DataClass, Market, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.api.conftest import build_test_client


def _result(source_id: str, status: HealthStatus, payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id=source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=status, detail="x"),
    )


def _news_client(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, agent_answer: str, result: SourceResult
) -> TestClient:
    async def fake_fetch_news(
        self: NewsGateway,
        *,
        query: str,
        market: Market = Market.TW,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        return result

    monkeypatch.setattr(NewsGateway, "fetch_news", fake_fetch_news)
    return build_test_client(settings, agent_answer=agent_answer)


def test_news_feed_route_reports_unreachable(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, agent_answer: str
) -> None:
    client = _news_client(
        monkeypatch, settings, agent_answer, _result("NEWS", HealthStatus.UNREACHABLE, {})
    )
    body = client.get("/api/v1/market/news-feed").json()
    assert body["items"] == []
    assert body["health_status"] == "unreachable"
    assert body["source_id"] == "NEWS"


def test_news_feed_route_reports_stale_when_nothing_came_back(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, agent_answer: str
) -> None:
    client = _news_client(
        monkeypatch, settings, agent_answer, _result("NEWS", HealthStatus.STALE, {})
    )
    body = client.get("/api/v1/market/news-feed").json()
    assert body["items"] == []
    assert body["health_status"] == "stale"


def test_news_feed_route_reports_ok_with_items(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, agent_answer: str
) -> None:
    item = {
        "title": "t",
        "link": "https://example.com/a",
        "source": "s",
        "published_at": "2026-10-27T01:00:00+00:00",
        "summary": None,
    }
    client = _news_client(
        monkeypatch, settings, agent_answer, _result("NEWS", HealthStatus.OK, {"items": [item]})
    )
    body = client.get("/api/v1/market/news-feed").json()
    assert body["health_status"] == "ok"
    assert body["items"][0]["title"] == "t"


def test_macro_calendar_route_reports_unreachable(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, agent_answer: str
) -> None:
    async def fake_fetch_calendar(  # noqa: PLR0913 - mirrors the gateway signature
        self: FinanceCalendarGateway,
        *,
        impact: str | None = None,
        days_ahead: int = 14,
        from_date: date | None = None,
        to_date: date | None = None,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        return _result("FINANCE_CALENDAR", HealthStatus.UNREACHABLE, {})

    monkeypatch.setattr(FinanceCalendarGateway, "fetch_calendar", fake_fetch_calendar)
    client = build_test_client(settings, agent_answer=agent_answer)
    body = client.get("/api/v1/market/macro-calendar").json()
    assert body["events"] == []
    assert body["health_status"] == "unreachable"
    assert body["source_id"] == "FINANCE_CALENDAR"
