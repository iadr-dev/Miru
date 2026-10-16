"""US macro statistics adapter tests: mocked HTTP only (recorded-shape payloads), no network."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import httpx
import pytest

import sources.fred as fred_module
from config.settings import Settings
from schemas.enums import DataClass, TimingClass
from schemas.source import HealthStatus
from sources.fred import (
    NOT_CONFIGURED_DETAIL,
    FredAdapter,
    TokenBucket,
    parse_value,
)

_KEY = "abcdefghijklmnopqrstuvwxyz123456"


def _settings(key: str | None = _KEY) -> Settings:
    return Settings(_env_file=None, fred_api_key=key, fred_base_url="https://fred.test/fred")


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _adapter(
    key: str | None = _KEY, *, clock: _Clock | None = None, attempts: int = 4
) -> tuple[FredAdapter, _Clock]:
    clock = clock or _Clock()
    bucket = TokenBucket(
        rate_per_second=1000.0, capacity=1000.0, clock=clock.time, sleep=clock.sleep
    )
    adapter = FredAdapter(
        _settings(key),
        bucket=bucket,
        sleep=clock.sleep,
        jitter=lambda: 0.0,
        max_attempts=attempts,
    )
    return adapter, clock


def _serve(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(recording))

    monkeypatch.setattr(fred_module, "build_async_client", factory)
    return seen


def _observations_body(count: int = 3, offset: int = 0, rows: int = 3) -> dict[str, object]:
    values = ["4.25", ".", "4.31", "4.40", "4.41"]
    return {
        "realtime_start": "2026-10-16",
        "realtime_end": "2026-10-16",
        "observation_start": "1776-07-04",
        "observation_end": "9999-12-31",
        "units": "lin",
        "output_type": 1,
        "file_type": "json",
        "order_by": "observation_date",
        "sort_order": "asc",
        "count": count,
        "offset": offset,
        "limit": 100000,
        "observations": [
            {
                "realtime_start": "2026-10-16",
                "realtime_end": "2026-10-16",
                "date": f"2026-10-{10 + offset + i:02d}",
                "value": values[(offset + i) % len(values)],
            }
            for i in range(rows)
        ],
    }


async def test_missing_key_degrades_to_unavailable_without_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _serve(monkeypatch, lambda _r: httpx.Response(200, json={}))
    adapter, _ = _adapter(key=None)

    result = await adapter.series_observations("DGS10")

    assert not seen
    assert result.health.status is HealthStatus.UNAVAILABLE
    assert result.health.detail == NOT_CONFIGURED_DETAIL
    assert result.currentness_caveat
    assert adapter.is_configured is False


async def test_blank_key_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(monkeypatch, lambda _r: httpx.Response(200, json={}))
    adapter, _ = _adapter(key="   ")

    result = await adapter.series_meta("DGS10")

    assert not seen
    assert result.health.status is HealthStatus.UNAVAILABLE


def test_parse_value_treats_dot_blank_and_garbage_as_missing() -> None:
    assert parse_value(".") is None
    assert parse_value("") is None
    assert parse_value(None) is None
    assert parse_value("abc") is None
    assert parse_value("nan") is None
    assert parse_value("4.25") == 4.25
    assert parse_value("-0.5") == -0.5


async def test_observations_parse_missing_values_and_carry_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _serve(monkeypatch, lambda _r: httpx.Response(200, json=_observations_body()))
    adapter, _ = _adapter()

    result = await adapter.series_observations(
        "DGS10",
        units="pc1",
        observation_start=date(2025, 1, 1),
        realtime_start=date(1776, 7, 4),
        realtime_end=date(9999, 12, 31),
        limit=500,
    )

    assert result.source_id == "FRED"
    assert result.data_class is DataClass.PUBLIC
    assert result.timing_class is TimingClass.EVENT_DRIVEN
    assert result.health.status is HealthStatus.OK
    assert "revised" in result.currentness_caveat
    observations = result.payload["observations"]
    assert isinstance(observations, list)
    assert [o["value"] for o in observations] == [4.25, None, 4.31]
    assert observations[0]["date"] == "2026-10-10"
    params = dict(seen[0].url.params)
    assert params["series_id"] == "DGS10"
    assert params["units"] == "pc1"
    assert params["observation_start"] == "2025-01-01"
    assert params["realtime_end"] == "9999-12-31"
    assert params["file_type"] == "json"
    assert params["api_key"] == _KEY
    assert seen[0].url.path == "/fred/series/observations"


async def test_the_result_never_contains_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, lambda _r: httpx.Response(200, json=_observations_body()))
    adapter, _ = _adapter()

    result = await adapter.series_observations("DGS10")

    assert _KEY not in result.model_dump_json()


async def test_paging_concatenates_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        rows = min(2, 5 - offset)
        return httpx.Response(200, json=_observations_body(count=5, offset=offset, rows=rows))

    seen = _serve(monkeypatch, handler)
    adapter, _ = _adapter()

    result = await adapter.fetch_all_observations("DGS10", page_size=2)

    observations = result.payload["observations"]
    assert isinstance(observations, list)
    assert len(observations) == 5
    assert [int(r.url.params["offset"]) for r in seen] == [0, 2, 4]
    assert all(r.url.params["limit"] == "2" for r in seen)


async def test_a_failing_page_returns_the_degraded_result_not_half_a_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["offset"] == "0":
            return httpx.Response(200, json=_observations_body(count=4, offset=0, rows=2))
        return httpx.Response(400, json={"error_code": 400, "error_message": "Bad Request."})

    _serve(monkeypatch, handler)
    adapter, _ = _adapter()

    result = await adapter.fetch_all_observations("DGS10", page_size=2)

    assert result.health.status is HealthStatus.UNAVAILABLE
    assert result.payload == {}


async def test_429_is_retried_honoring_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(
                429, headers={"Retry-After": "7"}, json={"error_message": "Too Many Requests"}
            )
        return httpx.Response(200, json=_observations_body())

    _serve(monkeypatch, handler)
    adapter, clock = _adapter()

    result = await adapter.series_observations("DGS10")

    assert result.health.status is HealthStatus.OK
    assert calls["n"] == 3
    assert clock.sleeps[:2] == [7.0, 7.0]


async def test_server_errors_back_off_exponentially_then_degrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="unavailable")

    _serve(monkeypatch, handler)
    adapter, clock = _adapter(attempts=3)

    result = await adapter.series_observations("DGS10")

    assert calls["n"] == 3
    assert clock.sleeps == [1.0, 2.0]
    assert result.health.status is HealthStatus.UNREACHABLE
    assert "503" in result.health.detail


async def test_a_client_error_is_not_retried_and_scrubs_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            400, json={"error_code": 400, "error_message": f"api_key {_KEY} is not registered"}
        )

    _serve(monkeypatch, handler)
    adapter, clock = _adapter()

    result = await adapter.series_meta("NOPE")

    assert calls["n"] == 1
    assert not clock.sleeps
    assert result.health.status is HealthStatus.UNAVAILABLE
    assert _KEY not in result.health.detail
    assert "400" in result.health.detail


async def test_a_transport_failure_is_retried_then_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError(f"cannot reach {request.url}")

    _serve(monkeypatch, handler)
    adapter, _ = _adapter(attempts=2)

    result = await adapter.series_observations("DGS10")

    assert calls["n"] == 2
    assert result.health.status is HealthStatus.UNREACHABLE
    assert _KEY not in result.health.detail
    assert "ConnectError" in result.health.detail


async def test_the_token_bucket_spaces_requests_past_its_burst() -> None:
    clock = _Clock()
    bucket = TokenBucket(rate_per_second=1.0, capacity=2.0, clock=clock.time, sleep=clock.sleep)

    for _ in range(4):
        await bucket.acquire()

    assert clock.sleeps == [1.0, 1.0]  # a burst of 2 is free, then one request per second


async def test_the_token_bucket_refills_with_time() -> None:
    clock = _Clock()
    bucket = TokenBucket(rate_per_second=1.0, capacity=1.0, clock=clock.time, sleep=clock.sleep)
    await bucket.acquire()
    clock.now += 5.0

    await bucket.acquire()

    assert not clock.sleeps


def test_the_token_bucket_rejects_nonsense_rates() -> None:
    with pytest.raises(ValueError, match="positive"):
        TokenBucket(rate_per_second=0.0, capacity=1.0)


async def test_every_request_waits_for_a_bucket_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, lambda _r: httpx.Response(200, json=_observations_body()))
    clock = _Clock()
    bucket = TokenBucket(rate_per_second=1.0, capacity=1.0, clock=clock.time, sleep=clock.sleep)
    adapter = FredAdapter(_settings(), bucket=bucket, sleep=clock.sleep, jitter=lambda: 0.0)

    await adapter.series_observations("A")
    await adapter.series_observations("B")

    assert clock.sleeps == [1.0]


async def test_series_meta_maps_the_documented_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "realtime_start": "2026-10-16",
        "realtime_end": "2026-10-16",
        "seriess": [
            {
                "id": "GNPCA",
                "realtime_start": "2026-10-16",
                "realtime_end": "2026-10-16",
                "title": "Real Gross National Product",
                "observation_start": "1929-01-01",
                "observation_end": "2012-01-01",
                "frequency": "Annual",
                "frequency_short": "A",
                "units": "Billions of Chained 2009 Dollars",
                "units_short": "Bil. of Chn. 2009 $",
                "seasonal_adjustment": "Not Seasonally Adjusted",
                "seasonal_adjustment_short": "NSA",
                "last_updated": "2013-07-31 09:26:16-05",
                "popularity": 39,
                "notes": "BEA Account Code: A001RX1",
            }
        ],
    }
    seen = _serve(monkeypatch, lambda _r: httpx.Response(200, json=body))
    adapter, _ = _adapter()

    result = await adapter.series_meta("GNPCA")

    series = result.payload["series"]
    assert isinstance(series, dict)
    assert series["title"] == "Real Gross National Product"
    assert series["observation_end"] == "2012-01-01"
    assert series["popularity"] == 39
    assert series["last_updated"] == "2013-07-31 09:26:16-05"
    assert seen[0].url.path == "/fred/series"


async def test_an_unknown_series_is_an_empty_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, lambda _r: httpx.Response(200, json={"seriess": []}))
    adapter, _ = _adapter()

    result = await adapter.series_meta("NOPE")

    assert result.health.status is HealthStatus.STALE
    assert result.payload["series"] is None


async def test_series_metas_fetches_concurrently_and_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        series_id = request.url.params["series_id"]
        if series_id == "BAD":
            return httpx.Response(400, json={"error_message": "Bad Request. series does not exist"})
        if request.url.path.endswith("/series/release"):
            return httpx.Response(
                200,
                json={
                    "releases": [
                        {
                            "id": 53,
                            "name": "Gross Domestic Product",
                            "press_release": True,
                            "link": "http://www.bea.gov/national/index.htm",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"seriess": [{"id": series_id, "title": f"T {series_id}"}]})

    _serve(monkeypatch, handler)
    adapter, _ = _adapter()

    results = await adapter.series_metas(["GDPC1", "BAD", "UNRATE"], concurrency=2)

    assert set(results) == {"GDPC1", "BAD", "UNRATE"}
    assert results["BAD"].health.status is HealthStatus.UNAVAILABLE
    good = results["GDPC1"]
    assert good.health.status is HealthStatus.OK
    release = good.payload["release"]
    assert isinstance(release, dict)
    assert release["id"] == 53
    assert release["name"] == "Gross Domestic Product"


async def test_release_dates_include_future_pages_and_map_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        rows = [
            {"release_id": 10 + offset, "release_name": f"R{offset}", "date": "2026-10-20"},
            {"release_id": 11 + offset, "release_name": f"R{offset + 1}", "date": "2026-11-02"},
        ]
        return httpx.Response(
            200, json={"count": 4, "offset": offset, "limit": 2, "release_dates": rows}
        )

    seen = _serve(monkeypatch, handler)
    adapter, _ = _adapter()

    result = await adapter.release_dates(
        from_date=date(2026, 9, 1), to_date=date(2027, 3, 1), page_size=2
    )

    rows = result.payload["release_dates"]
    assert isinstance(rows, list)
    assert len(rows) == 4
    assert rows[0] == {"release_id": 10, "release_name": "R0", "date": "2026-10-20"}
    params = dict(seen[0].url.params)
    assert params["include_release_dates_with_no_data"] == "true"
    assert params["realtime_start"] == "2026-09-01"
    assert params["realtime_end"] == "2027-03-01"
    assert seen[0].url.path == "/fred/releases/dates"


async def test_release_info_and_release_dates_for_one_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/release/dates"):
            return httpx.Response(
                200, json={"release_dates": [{"release_id": 53, "date": "2026-10-30"}]}
            )
        return httpx.Response(
            200,
            json={
                "releases": [{"id": 53, "name": "Gross Domestic Product", "press_release": True}]
            },
        )

    _serve(monkeypatch, handler)
    adapter, _ = _adapter()

    info = await adapter.release_info(53)
    dates = await adapter.release_dates_for(53, from_date=date(2026, 10, 1))

    release = info.payload["release"]
    assert isinstance(release, dict)
    assert release["name"] == "Gross Domestic Product"
    assert dates.payload["release_dates"] == [{"release_id": 53, "date": "2026-10-30"}]


async def test_series_updates_pages_merge_into_one_map(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        if offset == 0:
            rows = [{"id": f"S{i}", "last_updated": f"u{i}"} for i in range(1000)]
        else:
            rows = [{"id": "LAST", "last_updated": "ulast"}]
        return httpx.Response(200, json={"count": 1001, "seriess": rows})

    seen = _serve(monkeypatch, handler)
    adapter, _ = _adapter()

    result = await adapter.recent_series_updates(pages=3)

    updated = result.payload["series"]
    assert isinstance(updated, dict)
    assert len(updated) == 1001
    assert updated["LAST"] == "ulast"
    assert len(seen) == 2
    assert seen[0].url.params["filter_value"] == "macro"


async def test_series_updates_time_window_needs_both_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(monkeypatch, lambda _r: httpx.Response(200, json={"seriess": []}))
    adapter, _ = _adapter()

    with pytest.raises(ValueError, match="together"):
        await adapter.series_updates(start_time=datetime(2026, 10, 15, 8, 30))
    await adapter.series_updates(
        start_time=datetime(2026, 10, 15, 8, 30), end_time=datetime(2026, 10, 16, 8, 30)
    )

    assert seen[0].url.params["start_time"] == "202610150830"
    assert seen[0].url.params["end_time"] == "202610160830"


async def test_vintage_dates_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"count": 3, "vintage_dates": ["2026-09-05", "2026-10-03", "bogus"]}
    seen = _serve(monkeypatch, lambda _r: httpx.Response(200, json=body))
    adapter, _ = _adapter()

    result = await adapter.vintage_dates("PAYEMS", sort_order="desc", limit=1)

    assert result.payload["vintage_dates"] == ["2026-09-05", "2026-10-03"]
    assert result.payload["count"] == 3
    assert seen[0].url.params["sort_order"] == "desc"
    assert seen[0].url.path == "/fred/series/vintagedates"


async def test_bulk_release_observations_is_cursor_paged_and_unverified_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "has_more": True,
        "next_cursor": "BOGZ1FA106000125A,2010-01-01",
        "release": {"release_id": 52, "name": "Z.1"},
        "series": [
            {
                "series_id": "X",
                "title": "T",
                "frequency": "Quarterly",
                "units": "Millions",
                "observations": [
                    {"date": "2010-01-01", "value": "1.5"},
                    {"date": "2010-04-01", "value": "."},
                ],
            }
        ],
    }
    seen = _serve(monkeypatch, lambda _r: httpx.Response(200, json=body))
    adapter, _ = _adapter()

    result = await adapter.bulk_release_observations(52, cursor="PREV,2000-01-01")

    assert result.payload["has_more"] is True
    assert result.payload["next_cursor"] == "BOGZ1FA106000125A,2010-01-01"
    series = result.payload["series"]
    assert isinstance(series, list)
    assert series[0]["observations"] == [
        {"date": "2010-01-01", "value": 1.5},
        {"date": "2010-04-01", "value": None},
    ]
    assert seen[0].url.path == "/fred/v2/release/observations"
    assert seen[0].url.params["next_cursor"] == "PREV,2000-01-01"
    assert seen[0].url.params["api_key"] == _KEY  # query param only until the header is confirmed


async def test_bulk_release_without_a_key_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(monkeypatch, lambda _r: httpx.Response(200, json={}))
    adapter, _ = _adapter(key=None)

    result = await adapter.bulk_release_observations(52)

    assert not seen
    assert result.health.status is HealthStatus.UNAVAILABLE


async def test_an_html_error_page_is_a_non_retryable_decode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="<html>maintenance</html>")

    _serve(monkeypatch, handler)
    adapter, _ = _adapter()

    result = await adapter.series_meta("DGS10")

    assert calls["n"] == 1
    assert result.health.status is HealthStatus.UNAVAILABLE
