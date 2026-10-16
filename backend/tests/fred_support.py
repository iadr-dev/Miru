"""A fake US macro statistics service for tests: `httpx.MockTransport` handler that answers the v1
endpoints the adapter uses with recorded-shape JSON (field names as documented), deterministic
synthetic data, and knobs for the scenarios the job tests need (a changed series, a failing
series, a revision history, an empty answer). Also builds an `IngestContext` wired to it, and a
helper that runs the real job once to fill a store for the tool and route tests.

No test using this touches the network or a real key."""

from __future__ import annotations

import asyncio
from calendar.trading_days import TradingDayCalendar
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

import sources.fred as fred_module
from config.settings import Settings
from dataaccess.fred import FredGateway
from ingest.context import IngestContext
from ingest.jobs.fred import job_fred_macro
from ingest.journal import JobRun
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from schemas.fred_catalog import SERIES_CATALOG, MacroSeriesSpec, SeriesFrequency
from sources.finmind import FinMindAdapter
from sources.fred import FredAdapter, TokenBucket

ANCHOR = date(2026, 10, 14)  # the newest observation date of every synthetic series
RUN_DATE = date(2026, 10, 16)
DEFAULT_UPDATED = "2026-10-14 08:00:00-05"
KEY = "abcdefghijklmnopqrstuvwxyz123456"

_POINTS = {
    SeriesFrequency.DAILY: 80,
    SeriesFrequency.WEEKLY: 30,
    SeriesFrequency.MONTHLY: 40,
    SeriesFrequency.QUARTERLY: 14,
}
_UNIT_OFFSET = {"lin": 0.0, "chg": 100.0, "ch1": 50.0, "pch": 0.2, "pc1": 2.0, "pca": 3.0}
_SHARED_RELEASES: dict[str, tuple[int, str]] = {
    "PAYEMS": (50, "Employment Situation"),
    "UNRATE": (50, "Employment Situation"),
    "CPIAUCSL": (10, "Consumer Price Index"),
    "CPILFESL": (10, "Consumer Price Index"),
    "GDPC1": (53, "Gross Domestic Product"),
    "A191RL1Q225SBEA": (53, "Gross Domestic Product"),
}


def release_for(series_id: str) -> tuple[int, str]:
    if series_id in _SHARED_RELEASES:
        return _SHARED_RELEASES[series_id]
    index = next(i for i, s in enumerate(SERIES_CATALOG) if s.series_id == series_id)
    return 200 + index, f"Release of {series_id}"


def series_dates(spec: MacroSeriesSpec) -> list[date]:
    """Oldest first, ending on `ANCHOR`."""
    count = _POINTS[spec.frequency]
    if spec.frequency is SeriesFrequency.DAILY:
        return [ANCHOR - timedelta(days=count - 1 - i) for i in range(count)]
    if spec.frequency is SeriesFrequency.WEEKLY:
        return [ANCHOR - timedelta(days=7 * (count - 1 - i)) for i in range(count)]
    step = 1 if spec.frequency is SeriesFrequency.MONTHLY else 3
    days: list[date] = []
    year, month = ANCHOR.year, ANCHOR.month
    for _ in range(count):
        days.append(date(year, month, 1))
        month -= step
        while month < 1:
            month += 12
            year -= 1
    return sorted(days)


def synthetic_value(series_id: str, units: str, index: int, count: int) -> float | None:
    """The value of observation `index` of `count` (oldest first); `None` is a missing ('.')."""
    if series_id == "USREC":
        return 1.0 if 20 <= index < 26 else 0.0
    if series_id == "T10Y2Y":
        inverted_from = count - 20
        return 0.5 if index < inverted_from else -0.3 - 0.01 * (index - inverted_from)
    if series_id == "T10Y3M":
        return 0.8
    spec = next(s for s in SERIES_CATALOG if s.series_id == series_id)
    if spec.frequency is SeriesFrequency.DAILY and index % 17 == 16 and index < count - 3:
        return None
    return round(1.0 + 0.01 * index + _UNIT_OFFSET[units], 4)


class FakeFredServer:
    def __init__(self) -> None:
        self.last_updated: dict[str, str] = {}
        self.overrides: dict[tuple[str, str, date], float] = {}
        self.failing_observations: set[str] = set()
        self.failing_meta: set[str] = set()
        self.failing_release: set[str] = set()
        self.revisions: dict[str, dict[date, list[tuple[date, float]]]] = {}
        self.empty_observations: set[str] = set()
        self.all_meta_fail = False
        self.requests: list[httpx.Request] = []

    # -- helpers for assertions -------------------------------------------------------------

    def observation_requests(self, series_id: str | None = None) -> list[httpx.Request]:
        return [
            r
            for r in self.requests
            if r.url.path.endswith("/series/observations")
            and "realtime_end" not in r.url.params
            and (series_id is None or r.url.params["series_id"] == series_id)
        ]

    def count(self, suffix: str) -> int:
        return sum(1 for r in self.requests if r.url.path.endswith(suffix))

    # -- the handler ------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/fred")
        params = request.url.params
        if self.all_meta_fail and path == "/series":
            return httpx.Response(400, json={"error_code": 400, "error_message": "Bad Request."})
        handlers = {
            "/series": self._series,
            "/series/release": self._series_release,
            "/series/updates": lambda _r: httpx.Response(200, json={"count": 0, "seriess": []}),
            "/series/observations": self._observations,
            "/series/vintagedates": self._vintage_dates,
            "/releases/dates": self._release_dates,
        }
        handler = handlers.get(path)
        if handler is None:
            return httpx.Response(404, json={"error_message": f"no route {path}"})
        del params
        return handler(request)

    def _series(self, request: httpx.Request) -> httpx.Response:
        series_id = request.url.params["series_id"]
        if series_id in self.failing_meta:
            return httpx.Response(400, json={"error_code": 400, "error_message": "Bad Request."})
        spec = next(s for s in SERIES_CATALOG if s.series_id == series_id)
        return httpx.Response(
            200,
            json={
                "seriess": [
                    {
                        "id": series_id,
                        "title": f"Title of {series_id}",
                        "observation_start": "1960-01-01",
                        "observation_end": ANCHOR.isoformat(),
                        "frequency": spec.frequency.value.capitalize(),
                        "frequency_short": spec.frequency.value[0].upper(),
                        "units": "Percent" if series_id.startswith("DGS") else "Index",
                        "units_short": "%",
                        "seasonal_adjustment": "Seasonally Adjusted",
                        "seasonal_adjustment_short": "SA",
                        "last_updated": self.last_updated.get(series_id, DEFAULT_UPDATED),
                        "popularity": 50,
                        "notes": f"Notes for {series_id}",
                    }
                ]
            },
        )

    def _series_release(self, request: httpx.Request) -> httpx.Response:
        if request.url.params["series_id"] in self.failing_release:
            return httpx.Response(400, json={"error_code": 400, "error_message": "Bad Request."})
        release_id, name = release_for(request.url.params["series_id"])
        return httpx.Response(
            200,
            json={
                "releases": [
                    {"id": release_id, "name": name, "press_release": True, "link": "http://x"}
                ]
            },
        )

    def _observations(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        series_id = params["series_id"]
        if series_id in self.failing_observations:
            return httpx.Response(500, text="boom")
        spec = next(s for s in SERIES_CATALOG if s.series_id == series_id)
        if params.get("realtime_end") == "9999-12-31":
            return self._vintage_observations(spec, request)
        if series_id in self.empty_observations:
            return httpx.Response(200, json={"count": 0, "offset": 0, "observations": []})
        units = params.get("units", "lin")
        start = (
            date.fromisoformat(params["observation_start"])
            if "observation_start" in params
            else None
        )
        days = series_dates(spec)
        rows: list[dict[str, str]] = []
        for index, day in enumerate(days):
            if start is not None and day < start:
                continue
            value = self.overrides.get((series_id, units, day))
            if value is None:
                value = synthetic_value(series_id, units, index, len(days))
            rows.append(
                {
                    "realtime_start": RUN_DATE.isoformat(),
                    "realtime_end": RUN_DATE.isoformat(),
                    "date": day.isoformat(),
                    "value": "." if value is None else repr(value),
                }
            )
        offset = int(params.get("offset", "0"))
        limit = int(params.get("limit", "100000"))
        return httpx.Response(
            200,
            json={
                "units": units,
                "count": len(rows),
                "offset": offset,
                "limit": limit,
                "observations": rows[offset : offset + limit],
            },
        )

    def _vintage_observations(
        self, spec: MacroSeriesSpec, request: httpx.Request
    ) -> httpx.Response:
        params = request.url.params
        start = date.fromisoformat(params["observation_start"])
        days = series_dates(spec)
        rows: list[dict[str, str]] = []
        for index, day in enumerate(days):
            if day < start:
                continue
            history = self.revisions.get(spec.series_id, {}).get(day)
            if history is None:
                value = synthetic_value(spec.series_id, "lin", index, len(days)) or 0.0
                history = [(day + timedelta(days=20), value)]
            for position, (real_start, value) in enumerate(history):
                real_end = (
                    (history[position + 1][0] - timedelta(days=1)).isoformat()
                    if position + 1 < len(history)
                    else "9999-12-31"
                )
                rows.append(
                    {
                        "realtime_start": real_start.isoformat(),
                        "realtime_end": real_end,
                        "date": day.isoformat(),
                        "value": repr(value),
                    }
                )
        return httpx.Response(200, json={"count": len(rows), "offset": 0, "observations": rows})

    def _vintage_dates(self, _request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 2, "vintage_dates": ["2026-10-02", "2026-09-05"]})

    def _release_dates(self, request: httpx.Request) -> httpx.Response:
        assert request.url.params["include_release_dates_with_no_data"] == "true"
        rows: list[dict[str, object]] = []
        seen: set[int] = set()
        for spec in SERIES_CATALOG:
            release_id, name = release_for(spec.series_id)
            if release_id in seen:
                continue
            seen.add(release_id)
            rows.append(
                {
                    "release_id": release_id,
                    "release_name": name,
                    "date": (ANCHOR - timedelta(days=release_id % 20)).isoformat(),
                }
            )
            rows.append(
                {
                    "release_id": release_id,
                    "release_name": name,
                    "date": (ANCHOR + timedelta(days=7 + release_id % 20)).isoformat(),
                }
            )
        rows.append(
            {"release_id": 99999, "release_name": "Not in the catalog", "date": ANCHOR.isoformat()}
        )
        offset = int(request.url.params.get("offset", "0"))
        limit = int(request.url.params.get("limit", "1000"))
        return httpx.Response(
            200,
            json={
                "count": len(rows),
                "offset": offset,
                "limit": limit,
                "release_dates": rows[offset : offset + limit],
            },
        )


def install(monkeypatch: pytest.MonkeyPatch, server: FakeFredServer) -> None:
    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(server.handle))

    monkeypatch.setattr(fred_module, "build_async_client", factory)


def settings(key: str | None = KEY) -> Settings:
    return Settings(_env_file=None, fred_api_key=key, fred_base_url="https://fred.test/fred")


async def _no_sleep(_seconds: float) -> None:
    await asyncio.sleep(0)


def gateway(key: str | None = KEY) -> FredGateway:
    bucket = TokenBucket(rate_per_second=10_000.0, capacity=10_000.0)
    return FredGateway(
        FredAdapter(settings(key), bucket=bucket, sleep=_no_sleep, jitter=lambda: 0.0)
    )


def make_ctx(
    store_root: Path, *, key: str | None = KEY, leader: object | None = None
) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=Settings(_env_file=None, finmind_api_key="test-token")),
        store=ParquetStore(root=store_root),
        leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        fred=gateway(key),
    )


async def run_job(
    store_root: Path, monkeypatch: pytest.MonkeyPatch, server: FakeFredServer | None = None
) -> tuple[JobRun, FakeFredServer]:
    """Run the real job once against the fake service, filling `store_root`."""
    fake = server or FakeFredServer()
    install(monkeypatch, fake)
    run = await job_fred_macro(RUN_DATE, make_ctx(store_root))
    return run, fake


def fill_store(root: Path, server: FakeFredServer | None = None) -> None:
    """Run the real job once, synchronously, against the fake service (for module fixtures)."""
    with pytest.MonkeyPatch.context() as patch:
        run, _ = asyncio.run(run_job(root, patch, server))
    assert run.status.value == "success", run.detail
