"""Official company-filings adapter (MOPS monthly revenue) - frozen trimmed fixtures, no network.

The fixtures under tests/fixtures/mops_month_revenue are the real 2026-06 revenue pages (Big5, as
served) trimmed to a few companies; `sii_empty.html` is the real answer for a month nobody has
filed for. The expected figures are the ones FinMind stored for the same companies and month.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

import sources.exchange_whole_market._client as client_module
from ingest.jobs import MONTH_REVENUE_JOB
from schemas.capabilities import MonthRevenueRow
from schemas.enums import Capability, TLSPolicy
from schemas.source import HealthStatus, SourceResult
from sources.company_filings import MOPS_MIN_GAP_SECONDS, CompanyFilingsAdapter
from sources.company_filings import month_revenue as revenue
from sources.exchange_whole_market._client import RequestPolicy, ThrottledJsonClient
from sources.exchange_whole_market._common import ExchangeFetchError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mops_month_revenue"
JULY_1ST = date(2026, 7, 1)  # carries the revenue of 2026-06
AFTER_DEADLINE = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
BEFORE_DEADLINE = datetime(2026, 7, 10, 9, 0, tzinfo=UTC)

# page directory -> the fixture served for 2026-06
JUNE: dict[str, str] = {
    "sii": "sii_115_6.html",
    "otc": "otc_115_6.html",
    "rotc": "rotc_115_6.html",
}

# What FinMind stored for these companies and 2026-06 (TWD).
FINMIND_JUNE = {
    "1101": 13_382_706_000,
    "2317": 821_762_936_000,
    "2330": 442_679_969_000,
    "1264": 544_506_000,
    "3105": 1_767_694_000,
    "1260": 460_654_000,
    "6886": 42_510_000,
}


def _page(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeMops:
    """Serves the MOPS pages by market directory; records every request."""

    def __init__(self, pages: dict[str, bytes | int]) -> None:
        self.pages = dict(pages)  # market dir -> body, or a status code
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        market_dir = request.url.path.split("/")[-2]
        served = self.pages.get(market_dir)
        if isinstance(served, int):
            return httpx.Response(served)
        if served is None:
            return httpx.Response(404)
        return httpx.Response(200, content=served)

    def factory(self, **_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


class Sleeps:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _adapter(now: datetime = AFTER_DEADLINE, sleeps: Sleeps | None = None) -> CompanyFilingsAdapter:
    s = sleeps or Sleeps()
    client = ThrottledJsonClient(
        host=revenue.HOST,
        min_gap_seconds=0.0,
        policy=RequestPolicy(backoff_seconds=3.0),
        clock=s.clock,
        sleep=s.sleep,
    )
    return CompanyFilingsAdapter(mops_client=client, clock=lambda: now)


@pytest.fixture
def mops(monkeypatch: pytest.MonkeyPatch) -> FakeMops:
    fake = FakeMops({d: _page(n) for d, n in JUNE.items()})
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)
    return fake


async def _fetch(
    adapter: CompanyFilingsAdapter,
    run_date: date = JULY_1ST,
    cap: Capability = Capability.MONTH_REVENUE,
) -> SourceResult:
    return await adapter.fetch_whole_market(
        cap, tls_policy=TLSPolicy.STRICT, start_date=run_date.isoformat()
    )


def _rows(result: SourceResult) -> list[dict[str, object]]:
    rows = next(iter(result.payload.values()))
    assert isinstance(rows, list)
    return rows


def _all_valid(model: type[BaseModel], rows: list[dict[str, object]]) -> None:
    for row in rows:
        model.model_validate(row)


# ------------------------------------------------------------------------------ the period


def test_the_first_of_a_month_carries_the_previous_months_revenue() -> None:
    assert revenue.period_for(date(2026, 7, 1)) == revenue.RevenuePeriod(2026, 6, date(2026, 7, 1))
    assert revenue.period_for(date(2026, 1, 1)) == revenue.RevenuePeriod(2025, 12, date(2026, 1, 1))


@pytest.mark.parametrize("day", [date(2026, 7, 2), date(2026, 7, 10), date(2026, 7, 31)])
def test_any_other_day_carries_no_revenue(day: date) -> None:
    assert revenue.period_for(day) is None


def test_a_month_is_complete_from_the_11th() -> None:
    period = revenue.RevenuePeriod(2026, 6, JULY_1ST)
    assert not revenue.is_complete(period, date(2026, 7, 1))
    assert not revenue.is_complete(period, date(2026, 7, 10))
    assert revenue.is_complete(period, date(2026, 7, 11))


# ------------------------------------------------------------------------------- parsing


async def test_rows_have_the_finmind_shape_and_figures(mops: FakeMops) -> None:
    result = await _fetch(_adapter())

    assert result.source_id == "OFFICIAL_FILINGS"
    assert result.health.status is HealthStatus.OK
    assert list(result.payload) == ["rows"]
    rows = _rows(result)
    _all_valid(MonthRevenueRow, rows)
    by_symbol = {str(r["symbol"]): r for r in rows}
    assert set(by_symbol) == set(FINMIND_JUNE)
    assert by_symbol["2330"] == {
        "symbol": "2330",
        "market": "TW",
        "trade_date": "2026-07-01",  # the 1st of the month AFTER the revenue month
        "country": "Taiwan",
        "revenue": 442_679_969_000,  # the page is in thousands of TWD
        "revenue_month": 6,
        "revenue_year": 2026,
        "create_time": None,  # no official counterpart
    }
    assert {s: r["revenue"] for s, r in by_symbol.items()} == FINMIND_JUNE
    assert list(by_symbol["2330"]) == [  # key order of the FinMind mapper
        "symbol",
        "market",
        "trade_date",
        "country",
        "revenue",
        "revenue_month",
        "revenue_year",
        "create_time",
    ]


async def test_listed_otc_and_emerging_pages_are_all_asked_once_each(mops: FakeMops) -> None:
    result = await _fetch(_adapter())

    assert mops.paths() == [
        "/nas/t21/sii/t21sc03_115_6.html",
        "/nas/t21/otc/t21sc03_115_6.html",
        "/nas/t21/rotc/t21sc03_115_6.html",
    ]
    assert {r.url.host for r in mops.requests} == {"mopsov.twse.com.tw"}
    assert result.health.detail == "MOPS t21sc03 2026-06: listed 3 + OTC 2 + emerging 2 rows"


async def test_blank_and_non_numeric_revenue_rows_are_dropped_not_zeroed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sii = _page("sii_115_6.html").decode("big5hkscs")
    damaged = sii.replace("442,679,969", "-", 1)  # 2330's current-month cell
    fake = FakeMops({**{d: _page(n) for d, n in JUNE.items()}, "sii": damaged.encode("big5hkscs")})
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)

    rows = _rows(await _fetch(_adapter()))

    assert "2330" not in {r["symbol"] for r in rows}
    assert {"1101", "2317"} <= {r["symbol"] for r in rows}


async def test_a_company_listed_on_two_pages_is_kept_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMops({**{d: _page(n) for d, n in JUNE.items()}, "rotc": _page("otc_115_6.html")})
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)

    rows = _rows(await _fetch(_adapter()))

    symbols = [str(r["symbol"]) for r in rows]
    assert sorted(symbols) == sorted(set(symbols))
    assert symbols.count("1264") == 1


def test_the_older_page_layout_without_a_remark_column_parses_the_same() -> None:
    period = revenue.RevenuePeriod(2011, 12, date(2012, 1, 1))
    html = _page("sii_100_12_old_layout.html").decode("big5hkscs")

    rows = revenue.parse_page(html, "sii", period)

    assert [(r["symbol"], r["revenue"], r["revenue_month"], r["revenue_year"]) for r in rows] == [
        ("1101", 2_072_570_000, 12, 2011),
        ("1102", 1_006_050_000, 12, 2011),
    ]
    assert {r["trade_date"] for r in rows} == {"2012-01-01"}


def test_a_page_of_another_month_is_never_taken_for_the_requested_one() -> None:
    period = revenue.RevenuePeriod(2026, 5, date(2026, 6, 1))  # the fixtures are June
    html = _page("sii_115_6.html").decode("big5hkscs")

    with pytest.raises(ExchangeFetchError, match="not the report page"):
        revenue.parse_page(html, "sii", period)


def test_a_challenge_page_is_an_error_not_an_empty_month() -> None:
    period = revenue.RevenuePeriod(2026, 6, JULY_1ST)

    with pytest.raises(ExchangeFetchError, match="not the report page"):
        revenue.parse_page("<html><body>Just a moment...</body></html>", "sii", period)


def test_a_report_page_without_rows_or_the_empty_marker_is_an_error() -> None:
    period = revenue.RevenuePeriod(2026, 6, JULY_1ST)
    html = (
        "<font size='5'><b>上市公司115年6月份(累計與當月)營業收入統計表</b></font><table></table>"
    )

    with pytest.raises(ExchangeFetchError, match="no revenue rows"):
        revenue.parse_page(html, "sii", period)


# ------------------------------------------------------------------------- what is asked


@pytest.mark.parametrize("day", [date(2026, 7, 2), date(2026, 7, 15), date(2026, 7, 31)])
async def test_a_date_without_a_period_is_a_definite_empty_answer_and_asks_nothing(
    mops: FakeMops, day: date
) -> None:
    result = await _fetch(_adapter(), day)

    assert result.health.status is HealthStatus.OK  # "nothing, by definition" (not STALE)
    assert _rows(result) == []
    assert "not the 1st" in result.health.detail
    assert mops.requests == []


async def test_before_the_filing_deadline_the_month_is_not_served_and_nothing_is_asked(
    mops: FakeMops,
) -> None:
    result = await _fetch(_adapter(BEFORE_DEADLINE))

    assert result.health.status is HealthStatus.STALE  # "not yet": the next source is asked
    assert _rows(result) == []
    assert "not complete yet" in result.health.detail
    assert mops.requests == []


async def test_a_future_period_is_not_requested(mops: FakeMops) -> None:
    result = await _fetch(_adapter(), date(2026, 12, 1))

    assert result.health.status is HealthStatus.STALE
    assert mops.requests == []


async def test_the_deadline_is_judged_on_the_taipei_calendar(mops: FakeMops) -> None:
    # 2026-07-10 17:00 UTC is already the 11th in Taipei.
    result = await _fetch(_adapter(datetime(2026, 7, 10, 17, 0, tzinfo=UTC)))

    assert result.health.status is HealthStatus.OK
    assert len(mops.requests) == 3


# ------------------------------------------------------------------------- empty / failure


async def test_a_month_nobody_filed_for_is_empty_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = _page("sii_empty.html")
    fake = FakeMops({"sii": empty, "otc": empty, "rotc": empty})
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)

    result = await _fetch(_adapter(datetime(2026, 11, 12, tzinfo=UTC)), date(2026, 11, 1))

    assert result.health.status is HealthStatus.STALE
    assert _rows(result) == []
    assert "nothing published" in result.health.detail


async def test_one_failing_market_makes_the_whole_answer_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMops({**{d: _page(n) for d, n in JUNE.items()}, "otc": 403})
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)

    result = await _fetch(_adapter())

    assert result.health.status is HealthStatus.UNREACHABLE
    assert _rows(result) == []  # never the listed market alone
    assert "otc: " in result.health.detail
    assert "HTTP 403" in result.health.detail


async def test_a_required_market_with_no_rows_while_the_other_has_some_is_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMops({**{d: _page(n) for d, n in JUNE.items()}, "otc": _page("sii_empty.html")})
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)

    # the empty page is the October one, so it is also the wrong month for June: an error
    result = await _fetch(_adapter())

    assert result.health.status is HealthStatus.UNREACHABLE
    assert _rows(result) == []


async def test_an_empty_emerging_market_alone_does_not_block_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = _page("sii_empty.html").decode("big5hkscs").replace("115年10月", "115年6月")
    fake = FakeMops({**{d: _page(n) for d, n in JUNE.items()}, "rotc": html.encode("big5hkscs")})
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)

    result = await _fetch(_adapter())

    assert result.health.status is HealthStatus.OK
    assert len(_rows(result)) == 5
    assert "emerging 0" in result.health.detail


async def test_a_block_page_is_retried_then_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMops({**{d: _page(n) for d, n in JUNE.items()}, "sii": 503})
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)
    sleeps = Sleeps()

    result = await _fetch(_adapter(sleeps=sleeps))

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "HTTP 503 after 3 tries" in result.health.detail
    assert sleeps.slept == [3.0, 6.0]  # bounded: a doubling pause between the 3 tries


async def test_unsupported_capability_and_missing_date(mops: FakeMops) -> None:
    adapter = _adapter()
    other = await _fetch(adapter, cap=Capability.DIVIDENDS)
    no_date = await adapter.fetch_whole_market(
        Capability.MONTH_REVENUE, tls_policy=TLSPolicy.STRICT
    )

    assert other.health.status is HealthStatus.UNAVAILABLE_TIER
    assert no_date.health.status is HealthStatus.UNREACHABLE
    assert mops.requests == []


def test_the_adapter_covers_month_revenue_and_the_statements() -> None:
    assert CompanyFilingsAdapter.supported_capabilities() == {
        Capability.MONTH_REVENUE,
        Capability.FINANCIALS,
        Capability.BALANCE_SHEET,
        Capability.CASH_FLOW_STATEMENT,
    }
    assert MONTH_REVENUE_JOB.row_model is MonthRevenueRow


def test_mops_pages_are_requested_with_the_throttle_the_site_needs() -> None:
    assert MOPS_MIN_GAP_SECONDS >= 3.0
