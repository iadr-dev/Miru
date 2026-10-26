"""Exchange whole-market adapter (TWSE + TPEx) - frozen trimmed fixtures, no network.

The fixtures under tests/fixtures/exchange_whole_market are the real 2026-10-22 answers trimmed to
a few rows (plus a few synthetic odd rows: a stock that did not trade, an index label); the
`*_empty` / `twse_no_data` ones are the real answers for a Saturday.
"""

from __future__ import annotations

import asyncio
from datetime import date
from itertools import pairwise

import httpx
import pytest
from pydantic import BaseModel

import sources.exchange_whole_market._client as client_module
from ingest.jobs import (
    INSTITUTIONAL_FLOW_JOB,
    MARGIN_SHORT_JOB,
    PRICE_EOD_JOB,
    VALUATION_RATIOS_JOB,
)
from schemas.capabilities import EODBar, InstitutionalFlowRow, MarginShortRow, ValuationRatiosRow
from schemas.enums import Capability, TLSPolicy
from schemas.source import HealthStatus, SourceResult
from sources.exchange_whole_market import ExchangeWholeMarketAdapter
from sources.exchange_whole_market._client import (
    USER_AGENT,
    MinGapThrottle,
    RequestPolicy,
    ThrottledJsonClient,
)
from tests.sources.conftest import load_fixture

DAY = date(2026, 10, 22)
SATURDAY = date(2026, 10, 24)

# (host, path suffix) -> fixture for a trading day / for a day nothing was published
TRADING: dict[tuple[str, str], str] = {
    ("www.twse.com.tw", "/afterTrading/MI_INDEX"): "twse_mi_index.json",
    ("www.twse.com.tw", "/fund/T86"): "twse_t86.json",
    ("www.twse.com.tw", "/marginTrading/MI_MARGN"): "twse_mi_margn.json",
    ("www.twse.com.tw", "/afterTrading/BWIBBU_d"): "twse_bwibbu.json",
    ("www.tpex.org.tw", "/afterTrading/otc"): "tpex_otc.json",
    ("www.tpex.org.tw", "/insti/dailyTrade"): "tpex_daily_trade.json",
    ("www.tpex.org.tw", "/margin/balance"): "tpex_margin_balance.json",
    ("www.tpex.org.tw", "/afterTrading/peQryDate"): "tpex_pe.json",
}
EMPTY: dict[tuple[str, str], str] = {
    ("www.twse.com.tw", "/afterTrading/MI_INDEX"): "twse_no_data.json",
    ("www.twse.com.tw", "/fund/T86"): "twse_no_data.json",
    ("www.twse.com.tw", "/marginTrading/MI_MARGN"): "twse_no_data.json",
    ("www.twse.com.tw", "/afterTrading/BWIBBU_d"): "twse_no_data.json",
    ("www.tpex.org.tw", "/afterTrading/otc"): "tpex_otc_empty.json",
    ("www.tpex.org.tw", "/insti/dailyTrade"): "tpex_daily_trade_empty.json",
    ("www.tpex.org.tw", "/margin/balance"): "tpex_margin_balance_empty.json",
    ("www.tpex.org.tw", "/afterTrading/peQryDate"): "tpex_pe_empty.json",
}


class FakeWeb:
    """Routes the adapter's requests to fixtures; records every request."""

    def __init__(self, routes: dict[tuple[str, str], str]) -> None:
        self.routes = dict(routes)
        self.requests: list[httpx.Request] = []
        self.overrides: dict[str, list[httpx.Response]] = {}  # host -> responses served first

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queued = self.overrides.get(request.url.host)
        if queued:
            return queued.pop(0)
        for (host, suffix), fixture in self.routes.items():
            if request.url.host == host and request.url.path.endswith(suffix):
                return httpx.Response(200, json=load_fixture("exchange_whole_market", fixture))
        return httpx.Response(404)

    def factory(self, **_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def hosts(self) -> list[str]:
        return [r.url.host for r in self.requests]


class Sleeps:
    """A fake sleep + clock: sleeping advances the clock, nothing really waits."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _adapter(sleeps: Sleeps | None = None, *, gap: float = 0.0) -> ExchangeWholeMarketAdapter:
    s = sleeps or Sleeps()

    def client(host: str) -> ThrottledJsonClient:
        return ThrottledJsonClient(
            host=host,
            min_gap_seconds=gap,
            policy=RequestPolicy(backoff_seconds=3.0),
            clock=s.clock,
            sleep=s.sleep,
        )

    return ExchangeWholeMarketAdapter(
        twse_client=client("www.twse.com.tw"), tpex_client=client("www.tpex.org.tw")
    )


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> FakeWeb:
    fake = FakeWeb(TRADING)
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)
    return fake


async def _fetch(
    cap: Capability, run_date: date = DAY, adapter: ExchangeWholeMarketAdapter | None = None
) -> SourceResult:
    return await (adapter or _adapter()).fetch_whole_market(
        cap, tls_policy=TLSPolicy.STRICT, start_date=run_date.isoformat()
    )


def _rows(result: SourceResult) -> list[dict[str, object]]:
    payload_rows = next(iter(result.payload.values()))
    assert isinstance(payload_rows, list)
    return payload_rows


def _by(rows: list[dict[str, object]], *keys: str) -> dict[tuple[object, ...], dict[str, object]]:
    return {tuple(r[k] for k in keys): r for r in rows}


def _all_valid(model: type[BaseModel], rows: list[dict[str, object]]) -> None:
    for row in rows:
        model.model_validate(row)


# ---------------------------------------------------------------------------------- price


async def test_price_merges_listed_and_otc_in_the_finmind_row_shape(web: FakeWeb) -> None:
    result = await _fetch(Capability.PRICE_EOD)

    assert result.source_id == "EXCHANGE_OFFICIAL"
    assert result.health.status is HealthStatus.OK
    assert list(result.payload) == ["bars"]  # the key FinMind's price mapper uses
    rows = _rows(result)
    _all_valid(EODBar, rows)
    by = _by(rows, "symbol")
    assert set(by) == {("00400A",), ("1101",), ("2330",), ("00411A",), ("00679B",)}
    assert by[("1101",)] == {  # TWSE, comma-formatted numbers
        "symbol": "1101",
        "market": "TW",
        "trade_date": "2026-10-22",
        "open": 27.15,
        "high": 28.2,
        "low": 27.05,
        "close": 27.95,
        "volume": 103437782,
        "turnover": 2880485345,
    }
    assert by[("2330",)]["close"] == 2385.0  # '2,385.00'
    assert by[("00411A",)]["close"] == 9.93  # TPEx, an ETF
    assert by[("00411A",)]["volume"] == 10545000
    assert sorted(web.hosts()) == ["www.tpex.org.tw", "www.twse.com.tw"]


async def test_price_drops_untraded_stocks_and_index_labels(web: FakeWeb) -> None:
    rows = _rows(await _fetch(Capability.PRICE_EOD))

    symbols = {r["symbol"] for r in rows}
    assert "9958" not in symbols  # TWSE: prices '--', volume 0
    assert "6488" not in symbols  # TPEx: prices '---'
    assert "TAIEX" not in symbols  # not a security code


async def test_request_carries_date_params_user_agent_and_tls_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeWeb(TRADING)
    seen: list[dict[str, object]] = []

    def factory(**kwargs: object) -> httpx.AsyncClient:
        seen.append(kwargs)
        return fake.factory()

    monkeypatch.setattr(client_module, "build_async_client", factory)

    await _fetch(Capability.PRICE_EOD)

    twse = next(r for r in fake.requests if r.url.host == "www.twse.com.tw")
    tpex = next(r for r in fake.requests if r.url.host == "www.tpex.org.tw")
    assert dict(twse.url.params) == {"date": "20261022", "type": "ALLBUT0999", "response": "json"}
    assert dict(tpex.url.params) == {
        "type": "EW",
        "date": "2026/10/22",
        "id": "",
        "response": "json",
    }
    assert all(k["tls_policy"] is TLSPolicy.STRICT for k in seen)
    headers = seen[0]["headers"]
    assert isinstance(headers, dict)
    assert headers["User-Agent"] == USER_AGENT
    assert seen[0]["timeout"] == 20.0


# ---------------------------------------------------------------------- institutional flow


async def test_institutional_flow_is_five_long_rows_per_stock(web: FakeWeb) -> None:
    result = await _fetch(Capability.INSTITUTIONAL_FLOW)

    rows = _rows(result)
    _all_valid(InstitutionalFlowRow, rows)
    assert list(result.payload) == ["rows"]
    investors = {"Foreign_Investor", "Foreign_Dealer_Self", "Investment_Trust"}
    investors |= {"Dealer_self", "Dealer_Hedging"}
    assert {r["investor"] for r in rows} == investors
    assert len(rows) == 5 * 5  # three TWSE + two TPEx stocks
    by = _by(rows, "symbol", "investor")
    # TWSE 1101: 外陸資 buy/sell, 投信, 自營商 自行買賣 / 避險 (shares, unsigned)
    assert (by[("1101", "Foreign_Investor")]["buy"], by[("1101", "Foreign_Investor")]["sell"]) == (
        44655834,
        19106509,
    )
    assert by[("1101", "Investment_Trust")]["buy"] == 4728000
    assert by[("1101", "Dealer_self")]["buy"] == 438000
    assert by[("1101", "Dealer_self")]["sell"] == 536000
    assert by[("1101", "Dealer_Hedging")]["buy"] == 3759611
    assert by[("1101", "Dealer_Hedging")]["sell"] == 577923
    # TPEx 006201: unnamed columns, read by position - foreign, trust, dealer self, hedging
    assert by[("006201", "Foreign_Investor")]["sell"] == 13000
    assert by[("006201", "Investment_Trust")]["buy"] == 0
    assert by[("006201", "Dealer_self")]["sell"] == 0
    assert by[("006201", "Dealer_Hedging")]["buy"] == 119000
    assert by[("006201", "Dealer_Hedging")]["sell"] == 234634


# ------------------------------------------------------------------------------- margin


async def test_margin_short_maps_both_blocks_and_skips_the_total_row(web: FakeWeb) -> None:
    result = await _fetch(Capability.MARGIN_SHORT)

    rows = _rows(result)
    _all_valid(MarginShortRow, rows)
    by = _by(rows, "symbol")
    assert set(by) == {("1101",), ("2330",), ("00411A",), ("00679B",)}  # no 合計 row
    assert by[("1101",)] == {
        "symbol": "1101",
        "market": "TW",
        "trade_date": "2026-10-22",
        "margin_purchase_buy": 3526,
        "margin_purchase_sell": 4675,
        "margin_purchase_cash_repayment": 9,
        "margin_purchase_yesterday_balance": 36856,
        "margin_purchase_today_balance": 35698,
        "margin_purchase_limit": 1880795,
        "short_sale_buy": 2,
        "short_sale_sell": 112,
        "short_sale_cash_repayment": 0,
        "short_sale_yesterday_balance": 164,
        "short_sale_today_balance": 274,
        "short_sale_limit": 1880795,
        "offset_loan_and_short": 157,
        "note": None,
    }
    # TPEx lists 券賣 before 券買: 00411A sold 90 short, covered 0 -> balance 1 + 90 = 91
    etf = by[("00411A",)]
    assert (etf["short_sale_sell"], etf["short_sale_buy"], etf["short_sale_today_balance"]) == (
        90,
        0,
        91,
    )
    assert etf["margin_purchase_buy"] == 184
    assert etf["margin_purchase_sell"] == 1417
    assert etf["margin_purchase_today_balance"] == 13476


# ----------------------------------------------------------------------------- valuation


async def test_valuation_missing_per_is_zero_like_finmind(web: FakeWeb) -> None:
    rows = _rows(await _fetch(Capability.VALUATION_RATIOS))

    _all_valid(ValuationRatiosRow, rows)
    by = _by(rows, "symbol")
    assert by[("1101",)]["per"] == 0.0  # TWSE prints '-' for a loss-making company
    assert by[("1101",)]["dividend_yield"] == 2.86
    assert by[("1101",)]["pbr"] == 0.91
    assert by[("2330",)]["per"] == 27.65
    assert by[("1240",)] == {  # TPEx
        "symbol": "1240",
        "market": "TW",
        "trade_date": "2026-10-22",
        "dividend_yield": 0.93,
        "per": 9.98,
        "pbr": 1.58,
    }


# ---------------------------------------------------------------- empty / error / partial


@pytest.mark.parametrize(
    "cap",
    [
        Capability.PRICE_EOD,
        Capability.INSTITUTIONAL_FLOW,
        Capability.MARGIN_SHORT,
        Capability.VALUATION_RATIOS,
    ],
)
async def test_non_trading_day_is_an_empty_result_not_an_error(
    cap: Capability, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_module, "build_async_client", FakeWeb(EMPTY).factory)

    result = await _fetch(cap, SATURDAY)

    assert result.health.status is HealthStatus.STALE
    assert _rows(result) == []


async def test_one_exchange_empty_while_the_other_has_rows_is_not_a_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = dict(TRADING)
    routes[("www.tpex.org.tw", "/afterTrading/otc")] = "tpex_otc_empty.json"
    monkeypatch.setattr(client_module, "build_async_client", FakeWeb(routes).factory)

    result = await _fetch(Capability.PRICE_EOD)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "partial" in result.health.detail
    assert "TPEx" in result.health.detail
    assert _rows(result) == []  # never half a market


async def test_http_403_is_not_retried_and_reports_which_exchange(web: FakeWeb) -> None:
    web.overrides["www.tpex.org.tw"] = [httpx.Response(403)]

    result = await _fetch(Capability.PRICE_EOD)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "TPEx" in result.health.detail
    assert "403" in result.health.detail
    assert "TWSE" not in result.health.detail
    assert web.hosts().count("www.tpex.org.tw") == 1


async def test_unexpected_stat_is_an_error_not_an_empty_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeWeb(TRADING)
    fake.overrides["www.twse.com.tw"] = [httpx.Response(200, json={"stat": "Too many requests"})]
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)

    result = await _fetch(Capability.INSTITUTIONAL_FLOW)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "unexpected stat" in result.health.detail


async def test_an_answer_for_another_date_is_never_filed_under_the_requested_one(
    web: FakeWeb,
) -> None:
    result = await _fetch(Capability.PRICE_EOD, date(2026, 10, 21))  # fixtures answer 20261022

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "20261022" in result.health.detail


async def test_changed_layout_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = load_fixture("exchange_whole_market", "twse_t86.json")
    broken["fields"] = broken["fields"][:5]  # an older T86 layout, without the dealer columns
    fake = FakeWeb(TRADING)
    fake.overrides["www.twse.com.tw"] = [httpx.Response(200, json=broken)]
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)

    result = await _fetch(Capability.INSTITUTIONAL_FLOW)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "TWSE" in result.health.detail


async def test_unsupported_capability_and_missing_date(web: FakeWeb) -> None:
    adapter = _adapter()

    other = await adapter.fetch_whole_market(Capability.FINANCIALS, tls_policy=TLSPolicy.STRICT)
    no_date = await adapter.fetch_whole_market(
        Capability.PRICE_EOD, tls_policy=TLSPolicy.STRICT, start_date="soon"
    )

    assert other.health.status is HealthStatus.UNAVAILABLE_TIER
    assert no_date.health.status is HealthStatus.UNREACHABLE
    assert web.requests == []  # nothing was requested for either


def test_the_adapter_covers_exactly_the_four_dated_datasets() -> None:
    specs = (PRICE_EOD_JOB, INSTITUTIONAL_FLOW_JOB, MARGIN_SHORT_JOB, VALUATION_RATIOS_JOB)
    assert ExchangeWholeMarketAdapter.supported_capabilities() == {s.capability for s in specs}


# ---------------------------------------------------------------- retry and throttle


async def test_429_and_a_block_page_are_retried_with_a_doubling_backoff(web: FakeWeb) -> None:
    sleeps = Sleeps()
    web.overrides["www.twse.com.tw"] = [
        httpx.Response(429),
        httpx.Response(200, text="<html>blocked</html>"),  # not JSON
    ]

    result = await _fetch(Capability.VALUATION_RATIOS, adapter=_adapter(sleeps))

    assert result.health.status is HealthStatus.OK
    assert sleeps.slept == [3.0, 6.0]
    assert web.hosts().count("www.twse.com.tw") == 3


async def test_gives_up_after_the_bounded_number_of_tries(web: FakeWeb) -> None:
    web.overrides["www.twse.com.tw"] = [httpx.Response(503)] * 5

    result = await _fetch(Capability.VALUATION_RATIOS)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "HTTP 503" in result.health.detail
    assert "3 tries" in result.health.detail
    assert web.hosts().count("www.twse.com.tw") == 3


async def test_timeout_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return FakeWeb(TRADING).handler(request)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(client_module, "build_async_client", factory)

    result = await _fetch(Capability.VALUATION_RATIOS)

    assert result.health.status is HealthStatus.OK


async def test_throttle_spaces_request_starts_on_a_fake_clock() -> None:
    sleeps = Sleeps()
    throttle = MinGapThrottle(2.5, clock=sleeps.clock, sleep=sleeps.sleep)
    starts: list[float] = []

    async def one() -> None:
        await throttle.wait_turn()
        starts.append(sleeps.now)

    await asyncio.gather(*(one() for _ in range(4)))

    assert starts == [1000.0, 1002.5, 1005.0, 1007.5]
    assert sleeps.slept == [2.5, 2.5, 2.5]


async def test_throttle_does_not_sleep_when_the_gap_already_passed() -> None:
    sleeps = Sleeps()
    throttle = MinGapThrottle(2.0, clock=sleeps.clock, sleep=sleeps.sleep)

    await throttle.wait_turn()
    sleeps.now += 5.0
    await throttle.wait_turn()

    assert sleeps.slept == []


async def test_requests_to_one_host_are_spaced_by_the_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps = Sleeps()
    fake = FakeWeb(TRADING)
    started: list[tuple[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        started.append((request.url.host, sleeps.now))
        return fake.handler(request)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(client_module, "build_async_client", factory)
    adapter = _adapter(sleeps, gap=2.5)

    for cap in (Capability.PRICE_EOD, Capability.INSTITUTIONAL_FLOW, Capability.MARGIN_SHORT):
        await _fetch(cap, adapter=adapter)

    for host in ("www.twse.com.tw", "www.tpex.org.tw"):
        times = [t for h, t in started if h == host]
        assert len(times) == 3
        assert all(b - a >= 2.5 for a, b in pairwise(times))


async def test_duplicate_symbols_are_kept_once(monkeypatch: pytest.MonkeyPatch) -> None:
    dup = load_fixture("exchange_whole_market", "tpex_pe.json")
    dup["tables"][0]["data"].append(dup["tables"][0]["data"][0])  # 1240 twice
    fake = FakeWeb(TRADING)
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)
    fake.routes[("www.tpex.org.tw", "/afterTrading/peQryDate")] = "tpex_pe.json"
    fake.overrides["www.tpex.org.tw"] = [httpx.Response(200, json=dup)]

    rows = _rows(await _fetch(Capability.VALUATION_RATIOS))

    assert [r["symbol"] for r in rows].count("1240") == 1
