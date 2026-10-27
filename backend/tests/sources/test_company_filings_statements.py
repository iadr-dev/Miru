"""Official financial statements (MOPS summary reports) - frozen trimmed real pages, no network.

The fixtures under tests/fixtures/mops_statements are the real 2025 reports (the 2025 income
statement for Q1-Q4, balance sheet and cash flow for Q4) trimmed to a handful of companies per
industry template. The expected figures are the ones FinMind stored for the same companies and
quarters (single-quarter income lines; year-to-date cash flow).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

import sources.exchange_whole_market._client as client_module
from ingest.jobs import BALANCE_SHEET_JOB, CASH_FLOW_JOB, FINANCIALS_JOB
from schemas.capabilities import FinancialStatementLine
from schemas.enums import Capability, TLSPolicy
from schemas.source import HealthStatus, SourceResult
from sources.company_filings import CompanyFilingsAdapter
from sources.company_filings import statements as stmt
from sources.exchange_whole_market._client import RequestPolicy, ThrottledJsonClient
from sources.exchange_whole_market._common import ExchangeFetchError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mops_statements"
DEC_31 = date(2025, 12, 31)
SEP_30 = date(2025, 9, 30)
JUN_30 = date(2025, 6, 30)
MAR_31 = date(2025, 3, 31)
LATE = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)

Key = tuple[str, int, int]  # (market dir, ROC year, season)

INCOME: dict[Key, str] = {
    ("sii", 114, 1): "income_sii_114_01.html",
    ("sii", 114, 2): "income_sii_114_02.html",
    ("sii", 114, 3): "income_sii_114_03.html",
    ("sii", 114, 4): "income_sii_114_04.html",
    ("rotc", 114, 1): "income_rotc_114_01.html",
    ("rotc", 114, 2): "income_rotc_114_02.html",
    ("rotc", 114, 3): "income_rotc_114_03.html",
    ("rotc", 114, 4): "income_rotc_114_04.html",
}
BALANCE: dict[Key, str] = {
    ("sii", 114, 4): "balance_sii_114_04.html",
    ("otc", 114, 4): "balance_otc_114_04.html",
    ("rotc", 114, 4): "empty_sii.html",
}
CASH: dict[Key, str] = {
    ("sii", 114, 4): "cash_sii_114_04.html",
    ("otc", 114, 4): "cash_sii_114_04.html",  # stands in for the OTC page (heading rewritten)
    ("rotc", 114, 4): "cash_rotc_114_04.html",
}


def stmt_season_word(season: int) -> str:
    return {1: "第一季", 2: "第二季", 3: "第三季", 4: "第四季"}[season]


def _page(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeMops:
    """Serves the report pages by (market, year, season); records every request."""

    def __init__(self, pages: dict[Key, str | int], otc_stand_in: str = "") -> None:
        self.pages = dict(pages)
        self.otc_stand_in = otc_stand_in
        self.requests: list[tuple[str, Key]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        key = (form["TYPEK"], int(form["year"]), int(form["season"]))
        self.requests.append((request.method, key))
        served = self.pages.get(key)
        if served is None and key[0] == "otc" and "income" in self.otc_stand_in:
            # The OTC side of an income quarter is not what these tests read: the real Q4 page
            # stands in for the other seasons, with the season it is asked for in its heading.
            body = _page(self.otc_stand_in).decode().replace("第四季", stmt_season_word(key[2]))
            return httpx.Response(200, content=body.encode())
        if served is None:
            return httpx.Response(404)
        if isinstance(served, int):
            return httpx.Response(served)
        body = _page(served)
        if key[0] == "otc" and "_sii_" in served:
            body = body.decode().replace("上市公司", "上櫃公司").encode()
        return httpx.Response(200, content=body)

    def factory(self, **_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def asked(self, market: str) -> list[tuple[int, int]]:
        return [(y, s) for _, (m, y, s) in self.requests if m == market]


class Sleeps:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _adapter(now: datetime = LATE, sleeps: Sleeps | None = None) -> CompanyFilingsAdapter:
    s = sleeps or Sleeps()
    client = ThrottledJsonClient(
        host=stmt.HOST,
        min_gap_seconds=0.0,
        policy=RequestPolicy(backoff_seconds=3.0),
        clock=s.clock,
        sleep=s.sleep,
    )
    return CompanyFilingsAdapter(mops_client=client, clock=lambda: now)


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> FakeMops:
    fake = FakeMops({**INCOME}, otc_stand_in="income_otc_114_04.html")
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)
    return fake


def _serve(monkeypatch: pytest.MonkeyPatch, pages: dict[Key, str | int]) -> FakeMops:
    fake = FakeMops(pages)
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)
    return fake


async def _fetch(
    cap: Capability, run_date: date, adapter: CompanyFilingsAdapter | None = None
) -> SourceResult:
    return await (adapter or _adapter()).fetch_whole_market(
        cap, tls_policy=TLSPolicy.STRICT, start_date=run_date.isoformat()
    )


def _rows(result: SourceResult) -> list[dict[str, object]]:
    rows = next(iter(result.payload.values()))
    assert isinstance(rows, list)
    return rows


def _value(rows: list[dict[str, object]], symbol: str, item: str) -> float:
    found = [r["value"] for r in rows if r["symbol"] == symbol and r["item"] == item]
    assert len(found) == 1, (symbol, item, found)
    assert isinstance(found[0], float)
    return found[0]


# ------------------------------------------------------------------------------- the period


@pytest.mark.parametrize(
    ("day", "season", "roc"),
    [(date(2025, 3, 31), 1, 114), (date(2025, 6, 30), 2, 114), (SEP_30, 3, 114), (DEC_31, 4, 114)],
)
def test_only_a_quarter_end_carries_a_statement(day: date, season: int, roc: int) -> None:
    period = stmt.period_for(day)

    assert period is not None
    assert (period.season, period.roc_year, period.period_end) == (season, roc, day)


@pytest.mark.parametrize("day", [date(2025, 3, 30), date(2025, 4, 1), date(2025, 12, 30)])
def test_any_other_day_carries_no_statement(day: date) -> None:
    assert stmt.period_for(day) is None


@pytest.mark.parametrize(
    ("period_end", "last_day_open", "first_day_complete"),
    [
        (date(2025, 3, 31), date(2025, 5, 30), date(2025, 5, 31)),
        (date(2025, 6, 30), date(2025, 8, 31), date(2025, 9, 1)),
        (date(2025, 9, 30), date(2025, 11, 29), date(2025, 11, 30)),
        (date(2025, 12, 31), date(2026, 3, 31), date(2026, 4, 1)),
    ],
)
def test_a_quarter_is_complete_after_the_last_statutory_deadline(
    period_end: date, last_day_open: date, first_day_complete: date
) -> None:
    period = stmt.period_for(period_end)
    assert period is not None
    assert not stmt.is_complete(period, last_day_open)
    assert stmt.is_complete(period, first_day_complete)


# ------------------------------------------------------------------------------ the pages


def test_every_industry_template_on_a_page_is_read() -> None:
    html = _page("income_sii_114_04.html").decode()

    table = stmt.parse_page(html, "sii", 4, stmt.REPORTS[Capability.FINANCIALS])

    assert {"1101", "2330", "2801", "2880", "2855", "2816", "1409"} <= set(table)
    assert table["1101"]["營業收入"] == 149804135.0
    assert table["1101"]["營業毛利（毛損）"] == 27557589.0
    assert table["1101"]["原始認列生物資產及農產品之利益（損失）"] is None  # printed "--"
    assert table["2801"]["利息淨收益"] == 27106352.0  # the bank template
    assert table["2880"]["淨收益"] == 69492354.0  # the financial holding template


def test_a_page_of_another_season_or_market_is_never_taken_for_the_requested_one() -> None:
    html = _page("income_sii_114_04.html").decode()
    report = stmt.REPORTS[Capability.FINANCIALS]

    with pytest.raises(ExchangeFetchError, match="answered"):
        stmt.parse_page(html, "sii", 3, report)
    with pytest.raises(ExchangeFetchError, match="answered"):
        stmt.parse_page(html, "otc", 4, report)


def test_the_no_data_answer_is_an_empty_table_and_a_challenge_page_is_an_error() -> None:
    report = stmt.REPORTS[Capability.BALANCE_SHEET]

    assert stmt.parse_page(_page("empty_sii.html").decode(), "sii", 1, report) == {}
    with pytest.raises(ExchangeFetchError, match="not a report page"):
        stmt.parse_page("<html><body>Just a moment...</body></html>", "sii", 1, report)


def test_a_report_page_without_company_rows_is_an_error() -> None:
    with pytest.raises(ExchangeFetchError, match="no company rows"):
        stmt.parse_page(
            "<h2>上市公司第一季資料</h2><table class='hasBorder'></table>",
            "sii",
            1,
            stmt.REPORTS[Capability.FINANCIALS],
        )


# --------------------------------------------------------------------------- income lines


async def test_q1_is_the_report_itself_in_twd_with_finmind_items(web: FakeMops) -> None:
    result = await _fetch(Capability.FINANCIALS, MAR_31)

    assert result.source_id == "OFFICIAL_FILINGS"
    assert result.health.status is HealthStatus.OK
    assert list(result.payload) == ["lines"]  # the key FinMind's statement mapper uses
    rows = _rows(result)
    for row in rows:
        FinancialStatementLine.model_validate(row)
    assert _value(rows, "1101", "Revenue") == 34_956_255_000.0
    assert _value(rows, "1101", "IncomeAfterTaxes") == 768_392_000.0
    assert _value(rows, "2330", "EPS") == 13.95
    assert {r["statement_type"] for r in rows} == {"income_statement"}
    assert {r["period_end"] for r in rows} == {"2025-03-31"}
    assert web.asked("sii") == [(114, 1)]  # no earlier season to subtract


@pytest.mark.parametrize(
    ("run_date", "revenue_1101", "income_1101", "revenue_2330"),
    [
        (MAR_31, 34_956_255_000.0, 768_392_000.0, 839_253_664_000.0),
        (JUN_30, 35_354_423_000.0, 730_072_000.0, 933_791_869_000.0),
        (SEP_30, 39_066_903_000.0, -11_785_093_000.0, 989_918_318_000.0),
        (DEC_31, 40_426_554_000.0, -1_575_957_000.0, 1_046_090_421_000.0),
    ],
)
async def test_each_quarter_is_the_single_quarter_finmind_stores(
    web: FakeMops,
    run_date: date,
    revenue_1101: float,
    income_1101: float,
    revenue_2330: float,
) -> None:
    rows = _rows(await _fetch(Capability.FINANCIALS, run_date))

    assert _value(rows, "1101", "Revenue") == revenue_1101
    assert _value(rows, "1101", "IncomeAfterTaxes") == income_1101
    assert _value(rows, "2330", "Revenue") == revenue_2330


async def test_the_finance_templates_use_finmind_items_and_single_quarters(web: FakeMops) -> None:
    rows = _rows(await _fetch(Capability.FINANCIALS, DEC_31))

    assert _value(rows, "2801", "NetInterestIncome") == 7_308_378_000.0  # bank
    assert _value(rows, "2880", "Revenue") == 17_421_791_000.0  # financial holding 淨收益
    by_origin = {
        (r["symbol"], r["item"]): r["origin_name"] for r in rows if r["symbol"] in {"2801", "2880"}
    }
    assert by_origin[("2801", "NetInterestIncome")] == "利息淨收益"
    assert by_origin[("2880", "Revenue")] == "淨收益"


async def test_a_half_yearly_filer_gets_the_second_half_as_its_q4(web: FakeMops) -> None:
    q4 = _rows(await _fetch(Capability.FINANCIALS, DEC_31))
    web.requests.clear()
    q2 = _rows(await _fetch(Capability.FINANCIALS, JUN_30))

    # 1260 filed no Q1 and no Q3: Q2 is the half year itself, Q4 is the full year less that half.
    assert _value(q2, "1260", "Revenue") == 2_607_350_000.0
    assert _value(q4, "1260", "Revenue") == 2_563_014_000.0
    assert _value(q4, "1260", "IncomeAfterTaxes") == 195_061_000.0
    assert web.asked("rotc") == [(114, 2), (114, 1)]


async def test_earlier_seasons_are_fetched_only_while_a_company_has_none(web: FakeMops) -> None:
    await _fetch(Capability.FINANCIALS, DEC_31)

    assert web.asked("sii") == [(114, 4), (114, 3)]  # every sii company has its Q3 filing
    assert web.asked("otc") == [(114, 4), (114, 3)]
    assert web.asked("rotc") == [(114, 4), (114, 3), (114, 2)]  # stops once 1260 has Q2


async def test_a_derived_eps_says_so_and_a_reported_one_does_not(web: FakeMops) -> None:
    q1 = _rows(await _fetch(Capability.FINANCIALS, MAR_31))
    q4 = _rows(await _fetch(Capability.FINANCIALS, DEC_31))

    def origin(rows: list[dict[str, object]], symbol: str) -> object:
        return next(r["origin_name"] for r in rows if r["symbol"] == symbol and r["item"] == "EPS")

    assert origin(q1, "2330") == "基本每股盈餘"
    assert origin(q4, "2330") == "基本每股盈餘（累計差額推算）"
    assert abs(_value(q4, "2330", "EPS") - 19.51) < 0.02  # FinMind's 19.51: rounding only


async def test_lines_finmind_has_no_item_for_are_left_out_and_items_are_unique(
    web: FakeMops,
) -> None:
    rows = _rows(await _fetch(Capability.FINANCIALS, DEC_31))

    assert not any(r["item"] in {"", "-"} for r in rows)
    pairs = [(r["symbol"], r["item"]) for r in rows]
    assert len(pairs) == len(set(pairs))
    assert all(isinstance(r["value"], float) for r in rows)


def test_gross_profit_net_of_unrealized_is_used_only_without_a_plain_gross_profit() -> None:
    report = stmt.REPORTS[Capability.FINANCIALS]
    period = stmt.period_for(MAR_31)
    assert period is not None
    both: stmt.Table = {"1": {"營業毛利（毛損）": 5.0, "營業毛利（毛損）淨額": 4.0}}
    only_net: stmt.Table = {"2": {"營業毛利（毛損）": None, "營業毛利（毛損）淨額": 4.0}}

    rows = stmt.statement_rows(report, period, {**both, **only_net}, [])

    assert {(r["symbol"], r["value"]) for r in rows} == {("1", 5000.0), ("2", 4000.0)}


def test_a_company_with_no_earlier_filing_keeps_its_year_to_date_figure() -> None:
    report = stmt.REPORTS[Capability.FINANCIALS]
    period = stmt.period_for(SEP_30)
    assert period is not None
    current: stmt.Table = {
        "7": {"營業收入": 90.0, "基本每股盈餘（元）": 1.5},
        "8": {"營業收入": 50.0},
    }
    earlier: list[stmt.Table] = [{"8": {"營業收入": 20.0}}]

    rows = stmt.statement_rows(report, period, current, earlier)

    by = {(r["symbol"], r["item"]): r for r in rows}
    assert by[("7", "Revenue")]["value"] == 90_000.0  # no base: as printed
    assert by[("8", "Revenue")]["value"] == 30_000.0  # 50 less its nearest earlier filing, 20
    assert by[("7", "EPS")]["origin_name"] == "基本每股盈餘"  # not derived: nothing subtracted


# ----------------------------------------------------------- balance sheet and cash flow


async def test_balance_sheet_lines_are_the_position_at_the_period_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web = _serve(monkeypatch, BALANCE)

    result = await _fetch(Capability.BALANCE_SHEET, DEC_31)

    rows = _rows(result)
    for row in rows:
        FinancialStatementLine.model_validate(row)
    assert {r["statement_type"] for r in rows} == {"balance_sheet"}
    assert _value(rows, "1101", "TotalAssets") == 588_835_474_000.0
    assert _value(rows, "1101", "Liabilities") == 296_129_659_000.0
    assert _value(rows, "1101", "Equity") == 292_705_815_000.0
    assert _value(rows, "1101", "CurrentAssets") == 178_163_742_000.0
    assert _value(rows, "2801", "TotalAssets") == 3_377_011_238_000.0  # bank 資產總額
    assert [m for m, _ in web.requests] == ["POST"] * 3  # one page per market, no earlier season
    assert web.asked("sii") == [(114, 4)]


async def test_cash_flow_is_year_to_date_as_finmind_stores_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, CASH)

    rows = _rows(await _fetch(Capability.CASH_FLOW_STATEMENT, DEC_31))

    for row in rows:
        FinancialStatementLine.model_validate(row)
    assert {r["statement_type"] for r in rows} == {"cash_flow"}
    # FinMind keeps the operating cash flow under two items, with their own labels.
    assert _value(rows, "1101", "CashFlowsFromOperatingActivities") == 33_191_582_000.0
    assert _value(rows, "1101", "NetCashInflowFromOperatingActivities") == 33_191_582_000.0
    assert _value(rows, "1101", "CashBalancesBeginningOfPeriod") == 77_764_504_000.0
    assert _value(rows, "1101", "CashBalancesEndOfPeriod") == 90_165_009_000.0
    labels = {r["origin_name"] for r in rows if r["symbol"] == "1101"}
    assert {"營業活動之淨現金流入（流出）", "營業活動之淨現金流入"} <= labels


def test_the_three_jobs_validate_their_rows_against_the_statement_line() -> None:
    for job in (FINANCIALS_JOB, BALANCE_SHEET_JOB, CASH_FLOW_JOB):
        assert job.row_model is FinancialStatementLine


# ------------------------------------------------------------------------- what is asked


@pytest.mark.parametrize("cap", sorted(stmt.REPORTS, key=lambda c: c.value))
async def test_a_date_that_is_not_a_quarter_end_is_a_definite_empty_answer(
    web: FakeMops, cap: Capability
) -> None:
    result = await _fetch(cap, date(2025, 11, 5))

    assert result.health.status is HealthStatus.OK  # "nothing, by definition"
    assert _rows(result) == []
    assert "not a quarter end" in result.health.detail
    assert web.requests == []


async def test_before_the_last_deadline_the_quarter_is_not_served_and_nothing_is_asked(
    web: FakeMops,
) -> None:
    result = await _fetch(
        Capability.FINANCIALS, SEP_30, _adapter(datetime(2025, 11, 29, 9, 0, tzinfo=UTC))
    )

    assert result.health.status is HealthStatus.STALE
    assert _rows(result) == []
    assert "not complete" in result.health.detail
    assert web.requests == []


# ------------------------------------------------------------------------ empty / failure


async def test_a_quarter_nobody_filed_for_is_empty_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, {("sii", 114, 1): "empty_sii.html"})
    # the empty page is a "no data" answer, whatever the market and season asked for
    html_empty = _page("empty_sii.html")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html_empty)

    monkeypatch.setattr(
        client_module,
        "build_async_client",
        lambda **_k: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    result = await _fetch(Capability.BALANCE_SHEET, MAR_31)

    assert result.health.status is HealthStatus.STALE
    assert _rows(result) == []


async def test_one_failing_market_makes_the_whole_answer_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, {**BALANCE, ("otc", 114, 4): 403})

    result = await _fetch(Capability.BALANCE_SHEET, DEC_31)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert _rows(result) == []  # never the listed market alone
    assert "otc: " in result.health.detail
    assert "HTTP 403" in result.health.detail


async def test_a_required_market_with_no_rows_while_the_other_has_some_is_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, {**BALANCE, ("otc", 114, 4): "empty_sii.html"})

    # the empty page parses as "no data" for any market: the OTC side is empty, listed is not
    result = await _fetch(Capability.BALANCE_SHEET, DEC_31)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "partial: otc" in result.health.detail
    assert _rows(result) == []


async def test_a_failing_earlier_season_fails_the_quarter_instead_of_writing_year_to_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = {k: v for k, v in INCOME.items() if k != ("sii", 114, 3)}
    fake = FakeMops(pages, otc_stand_in="income_otc_114_04.html")
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)

    result = await _fetch(Capability.FINANCIALS, DEC_31)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "sii: " in result.health.detail
    assert _rows(result) == []


async def test_a_block_page_is_retried_then_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {**BALANCE, ("sii", 114, 4): 503})
    sleeps = Sleeps()

    result = await _fetch(Capability.BALANCE_SHEET, DEC_31, _adapter(sleeps=sleeps))

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "HTTP 503 after 3 tries" in result.health.detail
    assert sleeps.slept == [3.0, 6.0]


async def test_provenance_names_the_report_the_period_and_the_company_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, BALANCE)

    result = await _fetch(Capability.BALANCE_SHEET, DEC_31)

    assert result.health.detail == "MOPS t163sb05 2025Q4: listed 8 + OTC 2 + emerging 0 companies"
