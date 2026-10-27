"""Shares outstanding from the TWSE and TPEx company-basics dumps - frozen real rows, no network.

The fixtures under tests/fixtures/company_shares are the real 2026-10-26/27 open-data dumps
(`t187ap03_L`, `mopsfin_t187ap03_O`) trimmed to a few companies and to the fields that matter:
TSMC, Hon Hai, a depositary receipt (9105) and a recent split (6669) on the listed board.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

import sources.exchange_whole_market._client as client_module
from schemas.enums import TLSPolicy
from sources.company_shares import LISTED, OTC, CompanySharesAdapter
from sources.company_shares import parse as parse_module
from sources.exchange_whole_market._client import RequestPolicy, ThrottledJsonClient
from sources.exchange_whole_market._common import ExchangeFetchError, JsonObject

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "company_shares"


def _load(name: str) -> list[JsonObject]:
    loaded: list[JsonObject] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return loaded


class FakeDumps:
    """Serves the two dumps by host; records every request."""

    def __init__(self, listed: object, otc: object) -> None:
        self.bodies: dict[str, object] = {"openapi.twse.com.tw": listed, "www.tpex.org.tw": otc}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = self.bodies[request.url.host]
        if isinstance(body, int):
            return httpx.Response(body)
        return httpx.Response(200, json=body)

    def factory(self, **_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


async def _no_sleep(_seconds: float) -> None:
    return None


def _client(host: str) -> ThrottledJsonClient:
    return ThrottledJsonClient(
        host=host, min_gap_seconds=0.0, policy=RequestPolicy(max_attempts=1), sleep=_no_sleep
    )


class Day:
    """A settable 'today' for the once-a-day cache."""

    def __init__(self, today: date) -> None:
        self.today = today

    def __call__(self) -> date:
        return self.today


def _adapter(today: Day | None = None) -> CompanySharesAdapter:
    return CompanySharesAdapter(
        twse_client=_client("openapi.twse.com.tw"),
        tpex_client=_client("www.tpex.org.tw"),
        today=today or Day(date(2026, 10, 27)),
        min_listed=1,
        min_otc=1,
    )


@pytest.fixture
def dumps(monkeypatch: pytest.MonkeyPatch) -> FakeDumps:
    fake = FakeDumps(_load("t187ap03_L.json"), _load("mopsfin_t187ap03_O.json"))
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)
    return fake


def test_listed_dump_gives_issued_common_shares_and_leaves_receipts_out() -> None:
    parsed = parse_module.parse_listed(_load("t187ap03_L.json"), minimum=1)

    by_symbol = {entry.symbol: entry for entry in parsed.entries}
    assert set(by_symbol) == {"1101", "2317", "2330", "2412", "6669"}
    assert by_symbol["2330"].shares == 25_932_370_067
    assert by_symbol["2330"].listing_type == LISTED
    assert by_symbol["2330"].name == "台積電"
    assert parsed.depositary_receipts == 1  # 9105: the dump has the foreign ORIGINAL shares
    assert parsed.without_shares == 0
    assert parsed.as_of == date(2026, 10, 26)  # ROC 1151026


def test_otc_dump_uses_the_english_layout() -> None:
    parsed = parse_module.parse_otc(_load("mopsfin_t187ap03_O.json"), minimum=1)

    by_symbol = {entry.symbol: entry for entry in parsed.entries}
    assert set(by_symbol) == {"3105", "5274", "6223", "8299"}
    assert by_symbol["5274"].listing_type == OTC
    assert by_symbol["5274"].shares > 0
    assert parsed.as_of == date(2026, 10, 27)


@pytest.mark.parametrize("blank", ["", "0", "-", "n/a"])
def test_a_missing_share_count_is_counted_and_never_invented(blank: str) -> None:
    rows = _load("t187ap03_L.json")
    rows[0]["已發行普通股數或TDR原股發行股數"] = blank

    parsed = parse_module.parse_listed(rows, minimum=1)

    assert parsed.without_shares == 1
    assert "1101" not in {entry.symbol for entry in parsed.entries}


def test_thousands_separators_are_read() -> None:
    rows = _load("t187ap03_L.json")
    rows[0]["已發行普通股數或TDR原股發行股數"] = "7,523,181,742"

    parsed = parse_module.parse_listed(rows, minimum=1)

    assert next(e for e in parsed.entries if e.symbol == "1101").shares == 7_523_181_742


def test_a_dump_far_below_a_real_board_is_rejected() -> None:
    six_companies = _load("t187ap03_L.json")  # not a board
    with pytest.raises(ExchangeFetchError, match="expected at least"):
        parse_module.parse_listed(six_companies)


def test_a_changed_layout_is_an_error_not_an_empty_board() -> None:
    renamed = [{"code": "1101", "shares": "1"} for _ in range(3)]

    with pytest.raises(ExchangeFetchError, match="layout changed"):
        parse_module.parse_listed(renamed, minimum=1)


def test_an_unreadable_table_date_is_an_error() -> None:
    rows = _load("t187ap03_L.json")
    rows[0]["出表日期"] = "soon"

    with pytest.raises(ExchangeFetchError, match="table date"):
        parse_module.parse_listed(rows, minimum=1)


async def test_snapshot_joins_both_boards_with_the_older_table_date(dumps: FakeDumps) -> None:
    snapshot = await _adapter().fetch_snapshot(tls_policy=TLSPolicy.STRICT)

    assert snapshot.count(LISTED) == 5
    assert snapshot.count(OTC) == 4
    assert snapshot.as_of == date(2026, 10, 26)
    assert snapshot.depositary_receipts == 1
    assert sorted(r.url.host for r in dumps.requests) == ["openapi.twse.com.tw", "www.tpex.org.tw"]


async def test_the_snapshot_is_fetched_once_per_taipei_day(dumps: FakeDumps) -> None:
    today = Day(date(2026, 10, 27))
    adapter = _adapter(today)

    first = await adapter.fetch_snapshot(tls_policy=TLSPolicy.STRICT)
    again = await adapter.fetch_snapshot(tls_policy=TLSPolicy.STRICT)
    assert again is first
    assert len(dumps.requests) == 2  # one per exchange

    today.today = date(2026, 10, 28)
    await adapter.fetch_snapshot(tls_policy=TLSPolicy.STRICT)
    assert len(dumps.requests) == 4


async def test_one_dump_failing_is_an_error_and_is_not_cached(dumps: FakeDumps) -> None:
    dumps.bodies["www.tpex.org.tw"] = 403
    adapter = _adapter()

    with pytest.raises(ExchangeFetchError, match="HTTP 403"):
        await adapter.fetch_snapshot(tls_policy=TLSPolicy.STRICT)

    dumps.bodies["www.tpex.org.tw"] = _load("mopsfin_t187ap03_O.json")
    snapshot = await adapter.fetch_snapshot(tls_policy=TLSPolicy.STRICT)  # asked again, not cached
    assert snapshot.count(OTC) == 4


async def test_a_block_page_instead_of_the_array_is_an_error(dumps: FakeDumps) -> None:
    dumps.bodies["openapi.twse.com.tw"] = {"message": "blocked"}

    adapter = _adapter()
    with pytest.raises(ExchangeFetchError, match="JSON array"):
        await adapter.fetch_snapshot(tls_policy=TLSPolicy.STRICT)
