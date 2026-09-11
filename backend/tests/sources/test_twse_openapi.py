"""TWSE OpenAPI adapter tests — recorded fixture, no live network."""

from __future__ import annotations

import httpx
import pytest

import sources.twse_openapi as twse_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.twse_openapi import (
    TwseOpenApiAdapter,
    _split_slash_roc_date_range,
    fetch_whole_market_attention_stocks,
    fetch_whole_market_disposition_events,
)
from tests.sources.conftest import load_fixture, make_json_client_factory


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(twse_module, "build_async_client", make_json_client_factory(body))


def _patch_client_by_url(
    monkeypatch: pytest.MonkeyPatch, bodies_by_suffix: dict[str, object]
) -> None:
    """Route each request by URL suffix to its own fixture body — needed for
    `_fetch_calendar_events`, which makes two independent GETs (AGM + ex-rights) per call."""

    def handler(request: httpx.Request) -> httpx.Response:
        for suffix, body in bodies_by_suffix.items():
            if str(request.url).endswith(suffix):
                return httpx.Response(200, json=body)
        raise AssertionError(f"unexpected URL in test: {request.url}")

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_module, "build_async_client", factory)


def test_supports_price_eod_and_calendar_events_tw_only() -> None:
    adapter = TwseOpenApiAdapter()
    assert adapter.supports() == {
        (Capability.PRICE_EOD, Market.TW),
        (Capability.CALENDAR_EVENTS, Market.TW),
    }


def test_validate_rejects_non_list_rows() -> None:
    adapter = TwseOpenApiAdapter()
    with pytest.raises(ValueError, match="not a list"):
        adapter.validate({"rows": "nope"})


async def test_fetch_filters_to_requested_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("twse_openapi", "stock_day_all.json")
    _patch_client(monkeypatch, body)
    adapter = TwseOpenApiAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "TWSE_OPENAPI"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    bar = result.payload["bar"]
    assert bar["symbol"] == "2330"  # type: ignore[index]
    assert bar["close"] == 593.0  # type: ignore[index]


async def test_fetch_symbol_not_in_dump_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("twse_openapi", "stock_day_all.json")
    _patch_client(monkeypatch, body)
    adapter = TwseOpenApiAdapter()
    sym = Symbol(id="9999", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_unsupported_capability() -> None:
    adapter = TwseOpenApiAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.FINANCIALS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


def test_split_slash_roc_date_range() -> None:
    start, end = _split_slash_roc_date_range("115/09/03～115/09/09")  # noqa: RUF001 — real upstream separator
    assert start is not None
    assert end is not None
    assert start.isoformat() == "2026-09-03"
    assert end.isoformat() == "2026-09-09"


def test_split_slash_roc_date_range_rejects_malformed_input() -> None:
    assert _split_slash_roc_date_range("not-a-range") == (None, None)
    assert _split_slash_roc_date_range(None) == (None, None)
    assert _split_slash_roc_date_range("115/09/03~115/09/09") == (None, None)  # ASCII tilde


async def test_fetch_whole_market_attention_stocks_drops_sentinel_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A day with zero attention-stock flags returns one all-blank/all-zero sentinel row
    (confirmed live) — the fixture's real rows must survive, and a sentinel-style blank-`Code`
    row appended to them must be dropped, never mapped as a stock."""
    body = load_fixture("twse_openapi", "announcement_notice.json")
    sentinel = {
        "Number": "0",
        "Code": "",
        "Name": "",
        "NumberOfAnnouncement": "0",
        "TradingInfoForAttention": "",
        "Date": "",
        "ClosingPrice": "0",
        "PE": "0",
    }
    _patch_client(monkeypatch, [*body, sentinel])

    result = await fetch_whole_market_attention_stocks(tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 2
    by_symbol = {row["symbol"]: row for row in rows}  # type: ignore[index]
    assert by_symbol["3081"]["flagged_date"] == "2026-09-04"  # type: ignore[index]
    assert by_symbol["3081"]["security_type"] == "twse"  # type: ignore[index]
    assert by_symbol["30371"]["pe"] is None  # "N/A" -> None, not a fabricated value


async def test_fetch_whole_market_attention_stocks_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_module, "build_async_client", factory)

    result = await fetch_whole_market_attention_stocks(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload == {"rows": []}


async def test_fetch_whole_market_disposition_events_maps_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("twse_openapi", "announcement_punish.json")
    _patch_client(monkeypatch, body)

    result = await fetch_whole_market_disposition_events(tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 2
    by_symbol = {row["symbol"]: row for row in rows}  # type: ignore[index]
    row = by_symbol["3008"]
    assert row["period_start"] == "2026-09-03"  # type: ignore[index]
    assert row["period_end"] == "2026-09-09"  # type: ignore[index]
    assert row["measure"] == "第一次處置"  # type: ignore[index]
    assert row["security_type"] == "twse"  # type: ignore[index]


async def test_fetch_whole_market_disposition_events_drops_row_with_bad_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("twse_openapi", "announcement_punish.json")
    assert isinstance(body, list)
    broken_row = {**body[0], "DispositionPeriod": "not-a-range"}
    _patch_client(monkeypatch, [broken_row, body[1]])

    result = await fetch_whole_market_disposition_events(tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "3406"  # type: ignore[index]


_AGM_ROW = {
    "出表日期": "1150908",
    "公司代號": "2330",
    "公司名稱": "台積電",
    "公司地址": "新竹科學園區力行六路8號",
    "股東常(臨時)會": "常會",
    "開會日期": "1150528",
    "開會地點": "新竹",
    "是否改選董監": "否",
    "聯絡電話": "",
    "股務單位": "",
    "股務單位電話": "",
    "是否採電子投票": "強制",
}
_AGM_ROW_OTHER_SYMBOL = {**_AGM_ROW, "公司代號": "1101", "公司名稱": "台泥"}
_EX_RIGHTS_ROW_DIVIDEND_ONLY = {
    "Date": "1150907",
    "Code": "2330",
    "Name": "台積電",
    "Exdividend": "息",
    "StockDividendRatio": "",
    "SubscriptionRatio": "",
    "SubscriptionPricePerShare": "",
    "CashDividend": "7.000001",
    "SharesOffered": "",
    "SharesEmpOwner": "",
    "SharesholderOwner": "",
    "StockHoldingRatio": "",
}
_EX_RIGHTS_ROW_WITH_CASH_INCREASE = {
    **_EX_RIGHTS_ROW_DIVIDEND_ONLY,
    "SubscriptionRatio": "0.05",
}
_EX_RIGHTS_ROW_OTHER_SYMBOL = {**_EX_RIGHTS_ROW_DIVIDEND_ONLY, "Code": "1101", "Name": "台泥"}


async def test_fetch_calendar_events_filters_to_requested_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client_by_url(
        monkeypatch,
        {
            "t187ap41_L": [_AGM_ROW, _AGM_ROW_OTHER_SYMBOL],
            "TWT48U_ALL": [_EX_RIGHTS_ROW_DIVIDEND_ONLY, _EX_RIGHTS_ROW_OTHER_SYMBOL],
        },
    )
    adapter = TwseOpenApiAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.CALENDAR_EVENTS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    events = result.payload["events"]
    assert isinstance(events, list)
    assert len(events) == 2
    types = {e["type"] for e in events}  # type: ignore[union-attr]
    assert types == {"agm", "dividend_ex_rights"}


async def test_fetch_calendar_events_splits_dividend_and_cash_increase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row with both `Exdividend` and a non-empty `SubscriptionRatio` yields two distinct
    events — the ex-rights price adjustment and the separate cash-capital-increase
    subscription entitlement, not one merged row."""
    _patch_client_by_url(
        monkeypatch,
        {"t187ap41_L": [], "TWT48U_ALL": [_EX_RIGHTS_ROW_WITH_CASH_INCREASE]},
    )
    adapter = TwseOpenApiAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.CALENDAR_EVENTS, sym, tls_policy=TLSPolicy.STRICT)

    events = result.payload["events"]
    assert isinstance(events, list)
    types = {e["type"] for e in events}  # type: ignore[union-attr]
    assert types == {"dividend_ex_rights", "cash_capital_increase_rights"}


async def test_fetch_calendar_events_both_feeds_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_module, "build_async_client", factory)
    adapter = TwseOpenApiAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.CALENDAR_EVENTS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload == {}
