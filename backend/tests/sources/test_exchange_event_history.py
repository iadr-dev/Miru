"""Exchange attention / disposition history (TWSE + TPEx) - frozen trimmed real answers, no network.

The fixtures under tests/fixtures/exchange_event_history are the real January-2024 answers of the
four range reports trimmed to a few rows (counts adjusted to match), the real answers for a
weekend range, and a copy of the TPEx attention answer cut one row short.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

import sources.exchange_whole_market._client as client_module
from schemas.capabilities import AttentionStockRow, DispositionEventRow
from schemas.enums import TLSPolicy
from sources.exchange_event_history import ExchangeEventHistoryAdapter
from sources.exchange_event_history._dates import parse_roc_date, parse_roc_period
from sources.exchange_whole_market._client import RequestPolicy, ThrottledJsonClient
from sources.exchange_whole_market._common import ExchangeFetchError
from tests.sources.conftest import load_fixture

START = date(2024, 1, 1)
END = date(2024, 1, 31)

FULL: dict[tuple[str, str], str] = {
    ("www.twse.com.tw", "/announcement/punish"): "twse_punish_2401.json",
    ("www.twse.com.tw", "/announcement/notice"): "twse_notice_2401.json",
    ("www.tpex.org.tw", "/bulletin/disposal"): "tpex_disposal_2401.json",
    ("www.tpex.org.tw", "/bulletin/attention"): "tpex_attention_2401.json",
}


class FakeWeb:
    def __init__(self, routes: dict[tuple[str, str], str]) -> None:
        self.routes = dict(routes)
        self.requests: list[httpx.Request] = []
        self.queued: dict[str, list[httpx.Response]] = {}  # host -> responses served first

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queued = self.queued.get(request.url.host)
        if queued:
            return queued.pop(0)
        for (host, suffix), fixture in self.routes.items():
            if request.url.host == host and request.url.path.endswith(suffix):
                return httpx.Response(200, json=load_fixture("exchange_event_history", fixture))
        return httpx.Response(404)

    def factory(self, **_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def _adapter() -> ExchangeEventHistoryAdapter:
    fake_clock = Clock()

    def client(host: str) -> ThrottledJsonClient:
        return ThrottledJsonClient(
            host=host,
            min_gap_seconds=0.0,
            policy=RequestPolicy(max_attempts=2, backoff_seconds=1.0),
            clock=fake_clock.clock,
            sleep=fake_clock.sleep,
        )

    return ExchangeEventHistoryAdapter(
        twse_client=client("www.twse.com.tw"), tpex_client=client("www.tpex.org.tw")
    )


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> FakeWeb:
    fake = FakeWeb(FULL)
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)
    return fake


# ------------------------------------------------------------------------------------- dates


def test_roc_dates_are_read_with_slashes_and_with_dots() -> None:
    assert parse_roc_date("113/01/11") == date(2024, 1, 11)
    assert parse_roc_date("113.01.18") == date(2024, 1, 18)
    assert parse_roc_date(" 99/12/31 ") == date(2010, 12, 31)
    for bad in ("", "-----", "113/13/01", "113/02/30", "20240111", None):
        assert parse_roc_date(bad) is None


def test_a_period_takes_the_fullwidth_and_the_ascii_tilde() -> None:
    expected = (date(2024, 1, 12), date(2024, 1, 29))
    assert parse_roc_period("113/01/12～113/01/29") == expected  # noqa: RUF001 - the real separator
    assert parse_roc_period("113/01/12~113/01/29") == expected
    assert parse_roc_period("113/01/12 ~ 113/01/29") == expected


def test_a_period_that_is_not_two_ordered_dates_is_unreadable() -> None:
    for bad in ("113/01/12", "113/01/29~113/01/12", "113/01/12~", "x~y", ""):
        assert parse_roc_period(bad) is None


# ------------------------------------------------------------------------------ disposition


async def test_disposition_merges_listed_and_otc_in_the_stored_row_shape(web: FakeWeb) -> None:
    history = await _adapter().fetch_disposition(START, END, tls_policy=TLSPolicy.STRICT)

    assert (history.listed, history.otc, history.no_data) == (4, 5, False)
    for row in history.rows:
        DispositionEventRow.model_validate(row)
    listed = {
        (r["symbol"], r["period_start"]): r for r in history.rows if r["security_type"] == "twse"
    }
    first = listed[("8467", "2024-01-12")]
    assert first["announced_date"] == "2024-01-11"
    assert first["period_end"] == "2024-01-29"
    assert first["reason"] == "連續三次及當日沖銷標準"
    assert first["measure"] == "第一次處置"
    assert isinstance(first["detail"], str)
    assert first["detail"].startswith("１處置原因")
    assert first["company_name"] == "波力-KY"


async def test_otc_disposition_keeps_the_condition_text_and_strips_the_name_link(
    web: FakeWeb,
) -> None:
    history = await _adapter().fetch_disposition(START, END, tls_policy=TLSPolicy.STRICT)

    otc = [r for r in history.rows if r["security_type"] == "tpex"]
    assert {r["symbol"] for r in otc} == {"6535", "6187", "61874", "3228"}
    row = next(r for r in otc if r["symbol"] == "6187")
    assert row["company_name"] == "萬潤"
    assert (row["announced_date"], row["period_start"], row["period_end"]) == (
        "2024-01-23",
        "2024-01-24",
        "2024-02-15",
    )
    assert row["reason"] == "連續5個營業日"
    assert isinstance(row["measure"], str)
    assert row["measure"].startswith("萬潤科技股份有限公司股票(代號：6187)")
    assert row["detail"] is None


async def test_a_warrant_row_is_kept_for_the_reader_to_exclude(web: FakeWeb) -> None:
    history = await _adapter().fetch_disposition(START, END, tls_policy=TLSPolicy.STRICT)

    assert "61874" in {r["symbol"] for r in history.rows}


async def test_the_range_is_one_request_per_exchange(web: FakeWeb) -> None:
    await _adapter().fetch_disposition(START, END, tls_policy=TLSPolicy.STRICT)

    by_host = {r.url.host: r.url for r in web.requests}
    assert len(web.requests) == 2
    assert by_host["www.twse.com.tw"].params["startDate"] == "20240101"
    assert by_host["www.twse.com.tw"].params["endDate"] == "20240131"
    assert by_host["www.tpex.org.tw"].params["startDate"] == "2024/01/01"
    assert by_host["www.tpex.org.tw"].params["endDate"] == "2024/01/31"


# --------------------------------------------------------------------------------- attention


async def test_attention_merges_listed_and_otc_in_the_stored_row_shape(web: FakeWeb) -> None:
    history = await _adapter().fetch_attention(START, END, tls_policy=TLSPolicy.STRICT)

    assert (history.listed, history.otc, history.no_data) == (5, 5, False)
    for row in history.rows:
        AttentionStockRow.model_validate(row)
    listed = [r for r in history.rows if r["security_type"] == "twse"]
    first = listed[0]
    assert (first["symbol"], first["company_name"], first["flagged_date"]) == (
        "1312",
        "國喬",
        "2024-01-18",
    )
    assert first["close"] == 14.05
    assert first["pe"] is None  # '-----'
    assert "<" not in str(first["reason"])  # the markup around the clause number is gone
    otc = [r for r in history.rows if r["security_type"] == "tpex"]
    assert otc[0]["symbol"] == "3218"
    assert otc[0]["flagged_date"] == "2024-01-31"
    assert otc[0]["company_name"] == "大學光"
    assert otc[0]["close"] == 275.0
    assert otc[0]["pe"] == 22.5
    assert next(r for r in otc if r["symbol"] == "3294")["pe"] is None  # 'N/A'
    assert all("<br>" not in str(r["reason"]) for r in otc)


# ------------------------------------------------------------------------------- no data / errors


async def test_an_explicit_empty_answer_from_both_is_no_data(web: FakeWeb) -> None:
    web.routes[("www.twse.com.tw", "/announcement/notice")] = "twse_notice_empty.json"
    web.routes[("www.tpex.org.tw", "/bulletin/attention")] = "tpex_attention_empty.json"

    history = await _adapter().fetch_attention(
        date(2024, 1, 6), date(2024, 1, 7), tls_policy=TLSPolicy.STRICT
    )

    assert (history.rows, history.no_data) == ([], True)


async def test_the_placeholder_only_otc_disposition_report_is_no_data(web: FakeWeb) -> None:
    web.routes[("www.tpex.org.tw", "/bulletin/disposal")] = "tpex_disposal_none.json"

    history = await _adapter().fetch_disposition(START, END, tls_policy=TLSPolicy.STRICT)

    assert (history.listed, history.otc, history.no_data) == (4, 0, False)


async def test_one_exchange_without_rows_is_an_empty_side_not_an_error(web: FakeWeb) -> None:
    web.routes[("www.twse.com.tw", "/announcement/notice")] = "twse_notice_empty.json"

    history = await _adapter().fetch_attention(START, END, tls_policy=TLSPolicy.STRICT)

    assert (history.listed, history.otc, history.no_data) == (0, 5, False)


async def test_a_challenge_page_is_a_transient_error_then_a_failed_range(web: FakeWeb) -> None:
    page = httpx.Response(200, text="<html>Just a moment...</html>")
    web.queued["www.tpex.org.tw"] = [page, page]

    adapter = _adapter()
    with pytest.raises(ExchangeFetchError, match=r"TPEx: .*not JSON"):
        await adapter.fetch_attention(START, END, tls_policy=TLSPolicy.STRICT)


async def test_a_challenge_page_followed_by_the_report_is_retried(web: FakeWeb) -> None:
    web.queued["www.tpex.org.tw"] = [httpx.Response(200, text="<html>Just a moment...</html>")]

    history = await _adapter().fetch_attention(START, END, tls_policy=TLSPolicy.STRICT)

    assert history.otc == 5
    assert len(web.requests) == 3


async def test_a_cut_off_report_fails_the_range_instead_of_being_a_quiet_month(
    web: FakeWeb,
) -> None:
    web.routes[("www.tpex.org.tw", "/bulletin/attention")] = "tpex_attention_truncated.json"

    adapter = _adapter()
    with pytest.raises(ExchangeFetchError, match="report says 5 rows, holds 4"):
        await adapter.fetch_attention(START, END, tls_policy=TLSPolicy.STRICT)


async def test_one_failing_exchange_fails_the_range_even_when_the_other_has_rows(
    web: FakeWeb,
) -> None:
    web.queued["www.twse.com.tw"] = [httpx.Response(403), httpx.Response(403)]

    adapter = _adapter()
    with pytest.raises(ExchangeFetchError, match=r"TWSE: .*HTTP 403"):
        await adapter.fetch_disposition(START, END, tls_policy=TLSPolicy.STRICT)


async def test_an_answer_for_another_range_is_refused(web: FakeWeb) -> None:
    adapter = _adapter()
    february = (date(2024, 2, 1), date(2024, 2, 29))
    with pytest.raises(ExchangeFetchError, match="answered for 20240101~20240131"):
        await adapter.fetch_attention(*february, tls_policy=TLSPolicy.STRICT)


async def test_a_reversed_range_is_a_programming_error(web: FakeWeb) -> None:
    adapter = _adapter()
    with pytest.raises(ValueError, match="empty range"):
        await adapter.fetch_attention(END, START, tls_policy=TLSPolicy.STRICT)
