"""MOPS adapter tests — recorded fixture, no live network."""

from __future__ import annotations

import pytest

import sources.mops as mops_module
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.mops import (
    MopsAdapter,
    _fmt_time,
    _gregorian_yyyymmdd_to_iso,
    _roc_to_iso_date,
    _roc_yearmonth_to_iso,
)
from tests.sources.conftest import load_fixture, make_json_client_factory


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(mops_module, "build_async_client", make_json_client_factory(body))


def test_supports_corporate_actions_shareholding_and_profile_tw_only() -> None:
    adapter = MopsAdapter()
    assert adapter.supports() == {
        (Capability.CORPORATE_ACTIONS, Market.TW),
        (Capability.SHAREHOLDING, Market.TW),
        (Capability.PROFILE, Market.TW),
    }


def test_validate_rejects_non_list_rows() -> None:
    adapter = MopsAdapter()
    with pytest.raises(ValueError, match="not a list"):
        adapter.validate({"rows": "nope"})


@pytest.mark.parametrize(
    ("roc", "expected"),
    [
        ("1150625", "2026-06-25"),
        ("1150802", "2026-08-02"),
        ("1000101", "2011-01-01"),
    ],
)
def test_roc_to_iso_date(roc: str, expected: str) -> None:
    result = _roc_to_iso_date(roc)
    assert result is not None
    assert result.isoformat() == expected


def test_roc_to_iso_date_rejects_malformed_input() -> None:
    assert _roc_to_iso_date("not-a-date") is None
    assert _roc_to_iso_date("12") is None


def test_roc_yearmonth_to_iso() -> None:
    assert _roc_yearmonth_to_iso("11506") == "2026-06"


def test_fmt_time() -> None:
    assert _fmt_time("55412") == "05:54:12"
    assert _fmt_time("91107") == "09:11:07"


async def test_fetch_material_announcements_filters_to_symbol_and_converts_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("mops", "t187ap04_L.json")
    _patch_client(monkeypatch, body)
    adapter = MopsAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.CORPORATE_ACTIONS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "MOPS"
    assert result.data_class is DataClass.PUBLIC
    assert result.timing_class is TimingClass.EVENT_DRIVEN
    assert result.health.status is HealthStatus.OK
    announcements = result.payload["announcements"]
    assert isinstance(announcements, list)
    assert len(announcements) == 1
    row = announcements[0]
    assert row["symbol"] == "2330"  # type: ignore[index]
    assert row["announced_date"] == "2026-08-02"  # type: ignore[index]
    assert row["announced_time"] == "09:11:07"  # type: ignore[index]
    assert "董事會預計召開日期" in str(row["subject"])  # type: ignore[index]


async def test_fetch_material_announcements_handles_trailing_space_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("mops", "t187ap04_L.json")
    _patch_client(monkeypatch, body)
    adapter = MopsAdapter()
    sym = Symbol(id="1721", market=Market.TW)

    result = await adapter.fetch(Capability.CORPORATE_ACTIONS, sym, tls_policy=TLSPolicy.STRICT)

    announcements = result.payload["announcements"]
    assert isinstance(announcements, list)
    row = announcements[0]
    assert "更名" in str(row["subject"])  # type: ignore[index] — pulled from "主旨 " (trailing space)


async def test_fetch_material_announcements_symbol_not_in_dump_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("mops", "t187ap04_L.json")
    _patch_client(monkeypatch, body)
    adapter = MopsAdapter()
    sym = Symbol(id="9999", market=Market.TW)

    result = await adapter.fetch(Capability.CORPORATE_ACTIONS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_director_shareholding_filters_and_converts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("mops", "t187ap11_L.json")
    _patch_client(monkeypatch, body)
    adapter = MopsAdapter()
    sym = Symbol(id="1101", market=Market.TW)

    result = await adapter.fetch(Capability.SHAREHOLDING, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "MOPS"
    assert result.timing_class is TimingClass.REFERENCE
    assert result.health.status is HealthStatus.OK
    holdings = result.payload["holdings"]
    assert isinstance(holdings, list)
    assert len(holdings) == 2
    first = holdings[0]
    assert first["as_of"] == "2026-06"  # type: ignore[index]
    assert first["current_shares"] == 3835997  # type: ignore[index]
    assert first["shares_at_election"] == 3335997  # type: ignore[index] — trailing-space key


async def test_fetch_director_shareholding_symbol_not_in_dump_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("mops", "t187ap11_L.json")
    _patch_client(monkeypatch, body)
    adapter = MopsAdapter()
    sym = Symbol(id="9999", market=Market.TW)

    result = await adapter.fetch(Capability.SHAREHOLDING, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("19870221", "1987-02-21"),
        ("19501229", "1950-12-29"),
    ],
)
def test_gregorian_yyyymmdd_to_iso(raw: str, expected: str) -> None:
    assert _gregorian_yyyymmdd_to_iso(raw) == expected


def test_gregorian_yyyymmdd_to_iso_rejects_malformed_input() -> None:
    assert _gregorian_yyyymmdd_to_iso("not-a-date") is None
    assert _gregorian_yyyymmdd_to_iso("1150625") is None  # 7 digits — that's the ROC format


async def test_fetch_company_profile_filters_to_symbol_and_converts_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("mops", "t187ap03_L.json")
    _patch_client(monkeypatch, body)
    adapter = MopsAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PROFILE, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "MOPS"
    assert result.timing_class is TimingClass.REFERENCE
    assert result.health.status is HealthStatus.OK
    profile = result.payload["profile"]
    assert isinstance(profile, dict)
    assert profile["symbol"] == "2330"
    assert profile["chairman"] == "魏哲家"
    assert profile["auditor"] == "勤業眾信聯合會計師事務所"
    assert profile["website"] == "https://www.tsmc.com"
    assert profile["capital"] == 259303805050
    assert profile["founding_date"] == "1987-02-21"
    assert profile["listed_date"] == "1994-09-05"
    assert profile["industry_classification_code"] == "24"


async def test_fetch_company_profile_symbol_not_in_dump_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("mops", "t187ap03_L.json")
    _patch_client(monkeypatch, body)
    adapter = MopsAdapter()
    sym = Symbol(id="9999", market=Market.TW)

    result = await adapter.fetch(Capability.PROFILE, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_unsupported_capability() -> None:
    adapter = MopsAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.FINANCIALS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_fetch_non_tw_market_unavailable() -> None:
    adapter = MopsAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.CORPORATE_ACTIONS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
