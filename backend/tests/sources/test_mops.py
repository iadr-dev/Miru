"""MOPS adapter tests — recorded fixture, no live network."""

from __future__ import annotations

import httpx
import pytest

import sources.mops as mops_module
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.mops import (
    MopsAdapter,
    _announcement_category,
    _fmt_time,
    _gregorian_yyyymmdd_to_iso,
    _roc_to_iso_date,
    _roc_yearmonth_to_iso,
    _split_roc_date_range,
    _text,
    fetch_whole_market_insider_transfer_declarations,
    fetch_whole_market_material_announcements,
)
from tests.sources.conftest import (
    load_fixture,
    load_text_fixture,
    make_bytes_client_factory,
    make_json_client_factory,
)


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(mops_module, "build_async_client", make_json_client_factory(body))


def _patch_html_client(monkeypatch: pytest.MonkeyPatch, html_text: str) -> None:
    monkeypatch.setattr(
        mops_module, "build_async_client", make_bytes_client_factory(html_text.encode("utf-8"))
    )


def test_supports_corporate_actions_shareholding_and_profile_tw_only() -> None:
    adapter = MopsAdapter()
    assert adapter.supports() == {
        (Capability.CORPORATE_ACTIONS, Market.TW),
        (Capability.SHAREHOLDING, Market.TW),
        (Capability.PROFILE, Market.TW),
        (Capability.SELF_ASSESSED_EARNINGS, Market.TW),
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


def test_text_decodes_a_literal_html_numeric_character_reference() -> None:
    """Confirmed live: `t187ap11_L` served `"姓名":"&#28201;順德"` — a literal, undecoded HTML
    entity (decimal codepoint 28201 = U+6E29 温) rather than a plain string. `_text` must recover
    the real character, even though it's the simplified-form glyph the upstream dump itself
    encodes — not something this adapter corrects to the traditional variant."""
    assert _text("&#28201;順德") == "温順德"


def test_text_is_a_no_op_on_already_plain_text() -> None:
    assert _text("張安平") == "張安平"


def test_text_is_none_for_none() -> None:
    assert _text(None) is None


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


async def test_fetch_self_assessed_earnings_filters_to_symbol_prefers_consolidated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html_text = load_text_fixture("mops", "t138sb01.html")
    _patch_html_client(monkeypatch, html_text)
    adapter = MopsAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.SELF_ASSESSED_EARNINGS,
        sym,
        tls_policy=TLSPolicy.STRICT,
        period="2025-07",
        security_type="twse",
    )

    assert result.source_id == "MOPS"
    assert result.timing_class is TimingClass.REFERENCE
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 2  # 2330 filed both 個別 and 合併 for this period
    consolidated = next(row for row in rows if row["consolidated"])  # type: ignore[index]
    assert consolidated["revenue_period"] == "2025-07"
    assert consolidated["monthly_pretax_profit"] == 4155208
    assert consolidated["ytd_pretax_profit"] == 29984477
    assert consolidated["variance_note"] == "測試差異說明"
    individual = next(row for row in rows if not row["consolidated"])  # type: ignore[index]
    assert individual["monthly_pretax_profit"] == 572947


async def test_fetch_self_assessed_earnings_financial_industry_row_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """高雄銀's 個別 row has "不適用" in its 營業損益 cells (a real upstream quirk for financial-
    industry filers) — those two fields must be `None`, not a parse failure, while its
    稅前損益 cells (which ARE populated for this filer) still parse normally."""
    html_text = load_text_fixture("mops", "t138sb01.html")
    _patch_html_client(monkeypatch, html_text)
    adapter = MopsAdapter()
    sym = Symbol(id="2836", market=Market.TW)

    result = await adapter.fetch(
        Capability.SELF_ASSESSED_EARNINGS,
        sym,
        tls_policy=TLSPolicy.STRICT,
        period="2025-07",
        security_type="twse",
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    row = rows[0]
    assert row["monthly_operating_profit"] is None  # type: ignore[index]
    assert row["ytd_operating_profit"] is None  # type: ignore[index]
    assert row["monthly_pretax_profit"] == 150247  # type: ignore[index]


async def test_fetch_self_assessed_earnings_symbol_not_filed_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html_text = load_text_fixture("mops", "t138sb01.html")
    _patch_html_client(monkeypatch, html_text)
    adapter = MopsAdapter()
    sym = Symbol(id="9999", market=Market.TW)

    result = await adapter.fetch(
        Capability.SELF_ASSESSED_EARNINGS,
        sym,
        tls_policy=TLSPolicy.STRICT,
        period="2025-07",
        security_type="twse",
    )

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_self_assessed_earnings_missing_params_is_unavailable() -> None:
    adapter = MopsAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.SELF_ASSESSED_EARNINGS, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


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


@pytest.mark.parametrize(
    ("qualifying_clause", "expected_category"),
    [
        ("第12款", "法說會"),
        ("第20款", "資產處分/併購"),
        ("第36款", "庫藏股/減資"),
        ("第35款", "庫藏股/減資"),
        ("第11款", "財務/營運重大變動"),
        ("第51款", "其他"),  # a real, high-frequency clause with no dedicated bucket
        ("第2款", "其他"),
        (None, "其他"),
        (123, "其他"),  # non-string input — never raises
    ],
)
def test_announcement_category(qualifying_clause: object, expected_category: str) -> None:
    assert _announcement_category(qualifying_clause) == expected_category


def test_split_roc_date_range() -> None:
    start, end = _split_roc_date_range("1150906~1151005")
    assert start is not None
    assert end is not None
    assert start.isoformat() == "2026-09-06"
    assert end.isoformat() == "2026-10-05"


def test_split_roc_date_range_rejects_malformed_input() -> None:
    assert _split_roc_date_range("not-a-range") == (None, None)
    assert _split_roc_date_range(None) == (None, None)
    assert _split_roc_date_range("1150906~1151005~extra") == (None, None)


async def test_fetch_whole_market_material_announcements_maps_every_row_and_buckets_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whole-market — unlike the per-symbol path, every row in the dump is mapped, never
    filtered to one company, and each row carries a derived `category`."""
    body = load_fixture("mops", "t187ap04_L.json")
    _patch_client(monkeypatch, body)

    result = await fetch_whole_market_material_announcements(tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "MOPS"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 2  # both fixture rows, not filtered to one symbol
    by_symbol = {row["symbol"]: row for row in rows}  # type: ignore[index]
    assert by_symbol["1721"]["category"] == "其他"  # 第51款 has no dedicated bucket
    assert by_symbol["2330"]["category"] == "其他"  # 第4款 likewise


async def test_fetch_whole_market_material_announcements_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(mops_module, "build_async_client", factory)

    result = await fetch_whole_market_material_announcements(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload == {"rows": []}


async def test_fetch_whole_market_insider_transfer_declarations_maps_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("mops", "t187ap12_L.json")
    _patch_client(monkeypatch, body)

    result = await fetch_whole_market_insider_transfer_declarations(tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "MOPS"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 2
    by_symbol = {row["symbol"]: row for row in rows}  # type: ignore[index]
    yushan = by_symbol["2884"]
    assert yushan["transfer_shares"] == 220000
    assert yushan["transferee"] is None  # blank upstream "受讓人" -> None, not ""
    assert yushan["transfer_window_start"] == "2026-09-06"
    assert yushan["transfer_window_end"] == "2026-10-05"
    gift = by_symbol["6603"]
    assert gift["transferee"] == "張又仁"
    assert gift["transfer_method"] == "贈與"
    # `預定轉讓方式及股數-轉讓股數` is genuinely blank upstream for this 贈與 row (confirmed
    # live) — `transfer_shares` must fall back to `預定轉讓總股數-自有持股` +
    # `...-保留運用決定權信託股數` (2000 + 0) rather than being dropped as a missing required
    # field.
    assert gift["transfer_shares"] == 2000


async def test_fetch_whole_market_insider_transfer_declarations_drops_row_with_bad_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row missing a parseable `有效轉讓期間` is dropped rather than reaching
    `_validate_rows_against` with a guaranteed-failing required field."""
    body = load_fixture("mops", "t187ap12_L.json")
    assert isinstance(body, list)
    broken_row = {**body[0], "有效轉讓期間": "not-a-range"}
    _patch_client(monkeypatch, [broken_row, body[1]])

    result = await fetch_whole_market_insider_transfer_declarations(tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "6603"  # type: ignore[index]
