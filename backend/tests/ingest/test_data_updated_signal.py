"""The `data.updated` signal is emitted from the REAL ingest batch-completion path — not a
standalone `publish_data_updated()` unit test. A whole-market job that lands a partition as the
leader publishes exactly one signal carrying the landed batch's `{capability, market, as_of,
scope}`; a non-leader (or a non-trading day) lands nothing and so emits nothing."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date

import pytest

import sources.finmind as finmind_module
from config.settings import Settings
from ingest.context import IngestContext
from ingest.data_updates import DataUpdatePublisher
from ingest.jobs import PRICE_EOD_JOB, job_price_eod
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import Capability, Market, TLSPolicy
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture, make_json_client_factory


class _RecordingPublisher:
    """A `DataUpdatePublisher` double that records every signal the job path emits."""

    def __init__(self) -> None:
        self.signals: list[dict[str, object]] = []

    async def publish_data_updated(
        self, *, capability: Capability, market: Market, as_of: str, scope: str
    ) -> None:
        self.signals.append(
            {
                "capability": capability.value,
                "market": market.value,
                "as_of": as_of,
                "scope": scope,
            }
        )


def _settings() -> Settings:
    return Settings(finmind_api_key="test-token")


def _ctx(tmp_path: object, *, leader: object, publisher: DataUpdatePublisher) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=_settings()),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader,  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=publisher,
    )


async def test_leader_write_publishes_data_updated(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "price_eod_whole_market_2024-01-02.json")
    monkeypatch.setattr(finmind_module, "build_async_client", make_json_client_factory(body))
    publisher = _RecordingPublisher()
    ctx = _ctx(tmp_path, leader=AlwaysLeader(), publisher=publisher)
    trading_day = date(2024, 1, 2)

    await job_price_eod(trading_day, ctx)

    assert publisher.signals == [
        {
            "capability": PRICE_EOD_JOB.capability.value,
            "market": "TW",
            "as_of": "2024-01-02",
            "scope": "whole-market",
        }
    ]


async def test_non_leader_emits_no_signal(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A follower never writes, so it never emits the invalidation signal — the same leadership
    gate guards both."""

    def _boom(**_kwargs: object) -> object:
        raise AssertionError("a non-leader must not fetch")

    monkeypatch.setattr(finmind_module, "build_async_client", _boom)
    publisher = _RecordingPublisher()
    ctx = _ctx(tmp_path, leader=NeverLeader(), publisher=publisher)

    await job_price_eod(date(2024, 1, 2), ctx)

    assert publisher.signals == []
