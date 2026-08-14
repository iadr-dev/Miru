"""TDCC adapter tests — recorded fixtures (real CSV bytes captured live from
`opendata.tdcc.com.tw`), no live network in the default profile."""

from __future__ import annotations

import httpx
import pytest

import sources.tdcc as tdcc_module
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.tdcc import TdccAdapter
from sources.tdcc._shared import _roc_yearmonth_to_iso, _yyyymmdd_to_date, parse_csv
from tests.sources.conftest import load_text_fixture, make_bytes_client_factory


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    monkeypatch.setattr(tdcc_module, "build_async_client", make_bytes_client_factory(body))


def _fixture_bytes(name: str, *, encoding: str = "utf-8-sig") -> bytes:
    text = load_text_fixture("tdcc", name, encoding=encoding)
    return text.encode(encoding)


def test_supports_declares_every_dataset_as_tw() -> None:
    adapter = TdccAdapter()
    supported = adapter.supports()
    assert (Capability.TDCC_SECURITIES_INFO, Market.TW) in supported
    assert (Capability.TDCC_SHAREHOLDING_DISTRIBUTION, Market.TW) in supported
    assert (Capability.TDCC_OFFSHORE_FUND_NAV, Market.TW) in supported
    assert (Capability.TDCC_EVOTING_SCHEDULE, Market.TW) in supported
    assert (Capability.TDCC_MONTHLY_CUSTODY_ACCOUNTS, Market.TW) in supported
    assert (Capability.TDCC_PLEDGED_CUSTODY_SHARES, Market.TW) in supported


def test_validate_rejects_non_list_rows() -> None:
    adapter = TdccAdapter()
    with pytest.raises(ValueError, match="not a list"):
        adapter.validate({"rows": "nope"})


def test_parse_csv_handles_blank_lines() -> None:
    header, rows = parse_csv("a,b\n1,2\n\n3,4\n")
    assert header == ["a", "b"]
    assert rows == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_yyyymmdd_to_date() -> None:
    result = _yyyymmdd_to_date("20260812")
    assert result is not None
    assert result.isoformat() == "2026-08-12"


def test_yyyymmdd_to_date_rejects_malformed() -> None:
    assert _yyyymmdd_to_date("not-a-date") is None
    assert _yyyymmdd_to_date("2026") is None


def test_roc_yearmonth_to_iso() -> None:
    assert _roc_yearmonth_to_iso("11503") == "2026-03"


async def test_fetch_securities_info(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _fixture_bytes("1-1_securities_info.csv")
    _patch_client(monkeypatch, body)
    adapter = TdccAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.TDCC_SECURITIES_INFO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "TDCC"
    assert result.data_class is DataClass.PUBLIC
    assert result.timing_class is TimingClass.REFERENCE
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "2330"  # type: ignore[index]
    assert row["name"] == "台積電"  # type: ignore[index]
    assert row["listing_market"] == "上市"  # type: ignore[index]
    assert row["updated_date"] == "2026-08-12"  # type: ignore[index]


async def test_fetch_securities_info_symbol_not_found_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _fixture_bytes("1-1_securities_info.csv")
    _patch_client(monkeypatch, body)
    adapter = TdccAdapter()
    sym = Symbol(id="9999", market=Market.TW)

    result = await adapter.fetch(Capability.TDCC_SECURITIES_INFO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_pledged_custody_shares(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _fixture_bytes("1-4_pledged_custody_shares.csv")
    _patch_client(monkeypatch, body)
    adapter = TdccAdapter()
    sym = Symbol(id="1623", market=Market.TW)

    result = await adapter.fetch(
        Capability.TDCC_PLEDGED_CUSTODY_SHARES, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    row = rows[0]
    assert row["dispersed_custody_shares"] == 0  # type: ignore[index]
    assert row["specific_custody_shares"] == 15004757  # type: ignore[index]


async def test_fetch_shareholding_distribution_returns_all_tiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _fixture_bytes("1-5_shareholding_distribution.csv")
    _patch_client(monkeypatch, body)
    adapter = TdccAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.TDCC_SHAREHOLDING_DISTRIBUTION, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 17  # 17 holding-level tiers per symbol, confirmed live
    last_tier = rows[-1]
    assert last_tier["holding_level"] == "17"  # type: ignore[index]
    assert last_tier["percent_of_custody"] == 100.0  # type: ignore[index]


async def test_fetch_offshore_fund_nav(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _fixture_bytes("3-4_offshore_fund_nav.csv")
    _patch_client(monkeypatch, body)
    adapter = TdccAdapter()
    sym = Symbol(id="000AMEEBEI", market=Market.TW)

    result = await adapter.fetch(
        Capability.TDCC_OFFSHORE_FUND_NAV, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 3  # 3 NAV-date rows for this fund code in the fixture
    assert rows[0]["currency"] == "EUR"  # type: ignore[index]


async def test_fetch_evoting_schedule_decodes_big5(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _fixture_bytes("6-2_evoting_schedule_big5.csv", encoding="big5")
    _patch_client(monkeypatch, body)
    adapter = TdccAdapter()
    sym = Symbol(id="1256", market=Market.TW)

    result = await adapter.fetch(Capability.TDCC_EVOTING_SCHEDULE, sym, tls_policy=TLSPolicy.STRICT)

    assert result.timing_class is TimingClass.EVENT_DRIVEN
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    row = rows[0]
    assert row["name"] == "鮮活果汁-KY"  # type: ignore[index]
    assert row["meeting_date"] == "2026-08-11"  # type: ignore[index]
    assert row["voting_start_date"] == "2026-07-25"  # type: ignore[index]
    assert row["voting_end_date"] == "2026-08-08"  # type: ignore[index]


async def test_fetch_monthly_custody_accounts_is_market_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _fixture_bytes("2-1_monthly_custody_accounts.csv")
    _patch_client(monkeypatch, body)
    adapter = TdccAdapter()
    sym = Symbol(id="", market=Market.TW)  # market-level dataset — symbol is a placeholder

    result = await adapter.fetch(
        Capability.TDCC_MONTHLY_CUSTODY_ACCOUNTS, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.timing_class is TimingClass.REFERENCE
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 5  # every row in the market-level fixture, unfiltered
    assert rows[0]["as_of_month"] == "2026-03"  # type: ignore[index]


async def test_fetch_unsupported_capability_is_unavailable_tier() -> None:
    adapter = TdccAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.FINANCIALS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_fetch_non_tw_market_is_unavailable_tier() -> None:
    adapter = TdccAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.TDCC_SECURITIES_INFO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_fetch_no_data_marker_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, b"No Data!")
    adapter = TdccAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.TDCC_SECURITIES_INFO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE


async def test_fetch_transport_error_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(**_kwargs: object) -> httpx.AsyncClient:
        def responder(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        return httpx.AsyncClient(transport=httpx.MockTransport(responder))

    monkeypatch.setattr(tdcc_module, "build_async_client", factory)
    adapter = TdccAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.TDCC_SECURITIES_INFO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
