"""Official stock roster (TWSE ISIN lists) - frozen trimmed fixtures, no network.

The fixtures under tests/fixtures/isin_roster are the real 2026-10-27 lists (Big5 / MS950, as
served) trimmed to one security per category and board, plus a few warrants, depositary receipts
and REITs that must be left out. `finmind_roster_2026_10.json` is what the FinMind roster stored
for the same symbols (their categories and board) the month before.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest

import sources.exchange_whole_market._client as client_module
from schemas.capabilities import StockInfoRow
from schemas.enums import Capability, TLSPolicy
from schemas.source import HealthStatus, SourceResult
from sources.exchange_roster import ExchangeRosterAdapter
from sources.exchange_roster import isin as isin_module
from sources.exchange_whole_market._client import RequestPolicy, ThrottledJsonClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "isin_roster"
PAGES = {"2": "listed.html", "4": "otc.html", "5": "emerging.html"}
# The fixtures hold a handful of securities, not a market.
RELAXED = tuple(replace(board, min_entries=1) for board in isin_module.BOARDS)


def _page(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeIsin:
    """Serves the ISIN lists by `strMode`; records every request."""

    def __init__(self, pages: dict[str, bytes | int]) -> None:
        self.pages = dict(pages)  # strMode -> body, or a status code
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        served = self.pages.get(request.url.params.get("strMode", ""))
        if isinstance(served, int):
            return httpx.Response(served)
        if served is None:
            return httpx.Response(404)
        return httpx.Response(200, content=served)

    def factory(self, **_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


async def _no_sleep(_seconds: float) -> None:
    return None


def _adapter(*, relaxed: bool = True) -> ExchangeRosterAdapter:
    client = ThrottledJsonClient(
        host=isin_module.HOST,
        min_gap_seconds=0.0,
        policy=RequestPolicy(max_attempts=1),
        sleep=_no_sleep,
    )
    return ExchangeRosterAdapter(client=client, boards=RELAXED if relaxed else isin_module.BOARDS)


@pytest.fixture
def isin(monkeypatch: pytest.MonkeyPatch) -> FakeIsin:
    fake = FakeIsin({mode: _page(name) for mode, name in PAGES.items()})
    monkeypatch.setattr(client_module, "build_async_client", fake.factory)
    return fake


async def _fetch(adapter: ExchangeRosterAdapter | None = None) -> SourceResult:
    return await (adapter or _adapter()).fetch_whole_market(
        Capability.STOCK_INFO, tls_policy=TLSPolicy.STRICT, start_date="2026-10-27"
    )


def _rows(result: SourceResult) -> list[dict[str, object]]:
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    return rows


def _by_symbol(result: SourceResult) -> dict[str, dict[str, object]]:
    return {str(row["symbol"]): row for row in _rows(result)}


async def test_the_three_lists_become_one_validated_roster(isin: FakeIsin) -> None:
    result = await _fetch()

    assert result.health.status is HealthStatus.OK
    rows = _rows(result)
    for row in rows:
        StockInfoRow.model_validate(row)  # the stored row model, field for field
    symbols = [str(row["symbol"]) for row in rows]
    assert symbols == sorted(set(symbols))  # one row per security, in symbol order
    assert {row["security_type"] for row in rows} == {"twse", "tpex", "emerging"}
    assert {row["as_of"] for row in rows} == {"2026-10-27"}
    assert {row["market"] for row in rows} == {"TW"}
    assert result.health.detail.startswith("TWSE ISIN lists updated 2026-10-27: listed ")
    assert [r.url.params["strMode"] for r in isin.requests] == ["2", "4", "5"]


async def test_the_request_keeps_the_page_it_asks_for(isin: FakeIsin) -> None:
    """The shared client once sent `params={}`, which replaced the `?strMode=` of the URL and
    made TWSE answer HTTP 500."""
    await _fetch()

    assert {r.url.path for r in isin.requests} == {"/isin/C_public.jsp"}
    assert all("strMode" in r.url.params for r in isin.requests)


async def test_a_common_symbol_has_the_stored_category_and_board(isin: FakeIsin) -> None:
    stored: dict[str, dict[str, object]] = json.loads(
        (FIXTURES / "finmind_roster_2026_10.json").read_text(encoding="utf-8")
    )
    rebuilt = _by_symbol(await _fetch())

    common = sorted(set(stored) & set(rebuilt))

    assert len(common) >= 150
    wrong_category = [
        (symbol, rebuilt[symbol]["industry_category"], stored[symbol]["categories"])
        for symbol in common
        if rebuilt[symbol]["industry_category"] not in stored[symbol]["categories"]
    ]
    wrong_board = [
        (symbol, rebuilt[symbol]["security_type"], stored[symbol]["type"])
        for symbol in common
        if rebuilt[symbol]["security_type"] != stored[symbol]["type"]
    ]
    assert wrong_category == []
    # a symbol that moved boards since the stored roster is the only legitimate difference
    assert wrong_board == []


@pytest.mark.parametrize(
    ("symbol", "category", "board"),
    [
        ("2330", "半導體業", "twse"),
        ("1342", "其他", "twse"),  # 其他業
        ("1316", "建材營造", "twse"),  # 建材營造業
        ("2901", "貿易百貨", "twse"),  # 貿易百貨業
        ("2882", "金融保險", "twse"),  # listed: 金融保險
        ("5864", "金融業", "tpex"),  # OTC: 金融業
        ("5859", "金融業", "emerging"),
        ("1785", "其他電子類", "tpex"),  # OTC: 類
        ("2312", "其他電子業", "twse"),
        ("2230", "居家生活類", "tpex"),
        ("2942", "居家生活", "emerging"),
        ("2640", "數位雲端類", "tpex"),
        ("3130", "數位雲端", "twse"),
        ("3073", "綠能環保類", "tpex"),
        ("1593", "運動休閒類", "tpex"),
        ("1432", "運動休閒", "twse"),
        ("1240", "農業科技業", "tpex"),
        ("7840", "農業科技", "emerging"),
        ("1589", "電機機械", "twse"),  # a -KY company: its ISIN does not start with TW
        ("2237", "汽車工業", "twse"),  # the innovation board keeps its industry
    ],
)
async def test_the_exchange_category_is_spelled_as_the_roster_spelled_it(
    isin: FakeIsin, symbol: str, category: str, board: str
) -> None:
    rebuilt = _by_symbol(await _fetch())

    assert rebuilt[symbol]["industry_category"] == category
    assert rebuilt[symbol]["security_type"] == board


async def test_funds_take_the_roster_labels(isin: FakeIsin) -> None:
    rebuilt = _by_symbol(await _fetch())

    assert rebuilt["00400A"]["industry_category"] == "ETF"
    assert rebuilt["00411A"]["industry_category"] == "上櫃ETF"
    assert rebuilt["020000"]["industry_category"] == "ETN"
    assert rebuilt["020001"]["industry_category"] == "指數投資證券(ETN)"


async def test_a_preferred_share_without_a_category_takes_its_common_shares(
    isin: FakeIsin,
) -> None:
    rebuilt = _by_symbol(await _fetch())

    assert (
        rebuilt["1101B"]["industry_category"] == rebuilt["1101"]["industry_category"] == "水泥工業"
    )
    assert rebuilt["8349A"]["industry_category"] == rebuilt["8349"]["industry_category"]
    assert rebuilt["2836A"]["industry_category"] == "金融保險"  # one the exchange categorises


async def test_warrants_receipts_and_trusts_are_not_in_the_roster(isin: FakeIsin) -> None:
    html = _page("listed.html").decode("cp950") + _page("otc.html").decode("cp950")
    left_out = {
        code
        for section in ("認購(售)權證", "臺灣存託憑證(TDR)", "受益證券")
        for code in _codes_after(html, section)
    }
    assert left_out  # the fixtures do carry them

    rebuilt = _by_symbol(await _fetch())

    assert left_out.isdisjoint(rebuilt)


def _codes_after(html: str, section: str) -> set[str]:
    """The codes of the rows following each section row whose title contains `section`."""
    codes: set[str] = set()
    inside = False
    for row in re.findall(r"<tr>.*?</tr>", html, re.DOTALL):
        if "colspan=7" in row:
            inside = section in row
        elif inside:
            cell = re.sub(r"<[^>]*>", "", re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)[0])
            codes.add(cell.partition("　")[0])
    return codes


@pytest.mark.parametrize("failing", ["2", "4", "5"])
async def test_one_board_failing_is_never_half_a_roster(isin: FakeIsin, failing: str) -> None:
    isin.pages[failing] = 500

    result = await _fetch()

    assert result.health.status is HealthStatus.UNREACHABLE
    assert _rows(result) == []
    assert "HTTP 500" in result.health.detail


@pytest.mark.parametrize("bad", [b"<html>blocked</html>", b""])
async def test_a_page_that_is_not_the_list_is_an_error_not_an_empty_board(
    isin: FakeIsin, bad: bytes
) -> None:
    isin.pages["4"] = bad

    result = await _fetch()

    assert result.health.status is HealthStatus.UNREACHABLE
    assert _rows(result) == []
    assert "not the ISIN list page" in result.health.detail


async def test_a_page_far_smaller_than_the_board_is_a_changed_layout(isin: FakeIsin) -> None:
    result = await _fetch(_adapter(relaxed=False))  # the fixtures hold a handful of securities

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "fewer than the 800 expected" in result.health.detail


async def test_another_capability_is_not_served() -> None:
    result = await _adapter().fetch_whole_market(
        Capability.PRICE_EOD, tls_policy=TLSPolicy.STRICT, start_date=date(2026, 10, 27).isoformat()
    )

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


def test_a_preferred_share_with_no_common_share_keeps_an_empty_category() -> None:
    entry = isin_module.IsinEntry(
        "9999A", "孤特", "", isin_module.PREFERRED_SECTION, isin_module.TWSE, date(2026, 10, 27)
    )

    (row,) = isin_module.to_rows([entry])

    assert row["industry_category"] == ""
    StockInfoRow.model_validate(row)


def test_a_code_on_two_lists_keeps_the_first_board() -> None:
    twse = isin_module.IsinEntry("1234", "甲", "水泥工業", "股票", "twse", date(2026, 10, 27))
    tpex = isin_module.IsinEntry("1234", "甲", "水泥工業", "股票", "tpex", date(2026, 10, 27))

    (row,) = isin_module.to_rows([twse, tpex])

    assert row["security_type"] == "twse"


def test_every_category_of_the_fixtures_is_one_the_roster_used() -> None:
    entries: list[isin_module.IsinEntry] = []
    for board, name in zip(RELAXED, PAGES.values(), strict=True):
        html = _page(name).decode(isin_module.ENCODING)
        entries += isin_module.parse_page(html, board)

    categories = {str(row["industry_category"]) for row in isin_module.to_rows(entries)}

    assert categories - isin_module.KNOWN_CATEGORIES == set()
