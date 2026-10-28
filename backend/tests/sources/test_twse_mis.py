"""TWSE MIS adapter tests — recorded fixture, no live network.

Includes a dedicated test proving `fetch()` makes exactly one upstream request per call
(single-shot, never a poll/stream) — the hard governance rule for this adapter.
"""

from __future__ import annotations

import httpx
import pytest

import sources.twse_mis as twse_mis_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.twse_mis import TwseMisAdapter
from tests.sources.conftest import load_fixture, make_json_client_factory


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(twse_mis_module, "build_async_client", make_json_client_factory(body))


def test_supports_price_intraday_tw_only() -> None:
    adapter = TwseMisAdapter()
    assert adapter.supports() == {(Capability.PRICE_INTRADAY, Market.TW)}


def test_validate_rejects_non_list_msg_array() -> None:
    adapter = TwseMisAdapter()
    with pytest.raises(ValueError, match="not a list"):
        adapter.validate({"msgArray": "nope"})


async def test_fetch_returns_one_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("twse_mis", "getStockInfo_2330.json")
    _patch_client(monkeypatch, body)
    adapter = TwseMisAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_INTRADAY, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "TWSE_MIS"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    snapshot = result.payload["snapshot"]
    assert snapshot["symbol"] == "2330"  # type: ignore[index]
    assert snapshot["last_price"] == 596.0  # type: ignore[index]
    assert snapshot["best_bid_volume"] == "363_747_0_0_0_"  # type: ignore[index]
    assert snapshot["best_ask_volume"] == "217_312_0_0_0_"  # type: ignore[index]


async def test_fetch_empty_msg_array_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("twse_mis", "getStockInfo_empty.json")
    _patch_client(monkeypatch, body)
    adapter = TwseMisAdapter()
    sym = Symbol(id="9999", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_INTRADAY, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_unsupported_capability() -> None:
    adapter = TwseMisAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_fetch_is_single_shot_never_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    """One `fetch()` call must issue exactly one upstream HTTP request — no internal loop."""
    call_count = 0
    body = load_fixture("twse_mis", "getStockInfo_2330.json")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=body)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_mis_module, "build_async_client", factory)
    adapter = TwseMisAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_INTRADAY, sym, tls_policy=TLSPolicy.STRICT)

    assert call_count == 1
    assert result.health.status is HealthStatus.OK


# -- watchlist batch (策略盯盤 intraday) -------------------------------------------------------


def _row(
    code: str, *, z: str = "-", trade_z: str | None = None, **extra: object
) -> dict[str, object]:
    row: dict[str, object] = {
        "c": code,
        "ch": f"{code}.tw",
        "y": "100.0000",
        "o": "101.0000",
        "h": "103.0000",
        "l": "100.5000",
        "v": "250",
        "z": z,
        "d": "20261028",
        "t": "10:00:05",
    }
    if trade_z is not None:
        row["trade"] = {"t": "10:00:03", "v": 1, "z": trade_z}
    row.update(extra)
    return row


_STUB = {"c": "2330", "s": "-", "tv": "-", "z": "-"}  # what a wrong prefix / unknown code returns


def test_parse_quote_reads_the_nested_trade_price_between_ticks() -> None:
    quote = twse_mis_module.parse_quote(_row("2330", z="-", trade_z="102.5000"))

    assert quote is not None
    assert quote.last == 102.5
    assert quote.prev_close == 100.0
    assert quote.volume_shares == 250_000  # MIS reports lots of 1,000
    assert quote.trade_date == "2026-10-28"


def test_parse_quote_prefers_the_top_level_price_and_has_none_before_the_first_trade() -> None:
    top = twse_mis_module.parse_quote(_row("2330", z="104.0000", trade_z="102.5000"))
    untraded = twse_mis_module.parse_quote(_row("2330", z="-"))

    assert top is not None
    assert top.last == 104.0
    assert untraded is not None
    assert untraded.last is None


def test_parse_quote_drops_the_stub_row_for_a_wrong_prefix_or_unknown_code() -> None:
    assert twse_mis_module.parse_quote(dict(_STUB)) is None


async def test_the_snapshot_fetch_uses_the_nested_price_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"msgArray": [_row("2330", z="-", trade_z="102.5000")]}
    _patch_client(monkeypatch, body)

    result = await TwseMisAdapter().fetch(
        Capability.PRICE_INTRADAY, Symbol(id="2330", market=Market.TW), tls_policy=TLSPolicy.STRICT
    )

    assert result.payload["snapshot"]["last_price"] == 102.5  # type: ignore[index]


async def test_watchlist_fetch_asks_both_prefixes_and_the_index_in_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["ex_ch"])
        rows = [
            _row("2330", trade_z="102.5000"),
            dict(_STUB),  # the otc_2330 attempt
            _row("t00", z="20100.0", y="20000.0"),
        ]
        return httpx.Response(200, json={"msgArray": rows})

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_mis_module, "build_async_client", factory)

    result = await twse_mis_module.fetch_watchlist_quotes(["2330"], tls_policy=TLSPolicy.STRICT)

    assert seen == ["tse_2330.tw|otc_2330.tw|tse_t00.tw"]
    assert result.health.status is HealthStatus.OK
    assert [q.symbol for q in result.quotes] == ["2330"]
    assert result.index is not None
    assert result.index.last == 20100.0


async def test_watchlist_fetch_chunks_a_long_list_without_looping_on_a_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"msgArray": [_row("1101", trade_z="50.0000")]})

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_mis_module, "build_async_client", factory)
    codes = [f"{1000 + i}" for i in range(95)]

    await twse_mis_module.fetch_watchlist_quotes(codes, tls_policy=TLSPolicy.STRICT)

    assert requests == 3  # 95 codes at 40 per request


async def test_watchlist_fetch_transport_error_is_unreachable_not_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_mis_module, "build_async_client", factory)

    result = await twse_mis_module.fetch_watchlist_quotes(["2330"], tls_policy=TLSPolicy.STRICT)

    assert result.quotes == ()
    assert result.health.status is HealthStatus.UNREACHABLE


async def test_watchlist_fetch_repeats_a_timed_out_request_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"msgArray": [_row("2330", trade_z="102.5000")]})

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_mis_module, "build_async_client", factory)

    result = await twse_mis_module.fetch_watchlist_quotes(["2330"], tls_policy=TLSPolicy.STRICT)

    assert attempts == 2
    assert result.health.status is HealthStatus.OK


async def test_watchlist_fetch_gives_up_after_the_second_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("slow", request=request)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_mis_module, "build_async_client", factory)

    result = await twse_mis_module.fetch_watchlist_quotes(["2330"], tls_policy=TLSPolicy.STRICT)

    assert attempts == 2
    assert result.health.status is HealthStatus.UNREACHABLE
