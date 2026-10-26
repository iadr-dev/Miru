"""Characterization of `fetch_whole_market_etf_nav`'s response-shape handling: every malformed
level of the `a1 -> msgArray -> row` nesting is skipped or reported, never raised. Stubbed HTTP
client, synthetic bodies, no live network."""

from __future__ import annotations

import httpx
import pytest

import sources.twse_etf_nav as twse_etf_nav_module
from schemas.enums import Market, TLSPolicy
from schemas.source import HealthStatus
from sources.twse_etf_nav import fetch_whole_market_etf_nav
from tests.sources.conftest import make_json_client_factory


def _patch(monkeypatch: pytest.MonkeyPatch, body: object, *, status_code: int = 200) -> None:
    monkeypatch.setattr(
        twse_etf_nav_module,
        "build_async_client",
        make_json_client_factory(body, status_code=status_code),
    )


def _raw(symbol: object = "0050", as_of: object = "20260828", **extra: object) -> dict[str, object]:
    return {
        "a": symbol,
        "b": "元大台灣50",
        "c": "1,000",
        "d": -5,
        "e": "106.95",
        "i": as_of,
    } | extra


async def _fetch() -> tuple[list[dict[str, object]], HealthStatus, str]:
    result = await fetch_whole_market_etf_nav(tls_policy=TLSPolicy.STRICT)
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    return rows, result.health.status, result.health.detail


async def test_a_non_object_body_is_unreachable_with_the_missing_a1_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, [1, 2, 3])

    rows, status, detail = await _fetch()

    assert (rows, status) == ([], HealthStatus.UNREACHABLE)
    assert detail == "TWSE all_etf.txt response missing 'a1'"


async def test_a1_that_is_not_a_list_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, {"a1": {"msgArray": []}})

    rows, status, detail = await _fetch()

    assert (rows, status) == ([], HealthStatus.UNREACHABLE)
    assert detail == "TWSE all_etf.txt 'a1' is not a list"


async def test_malformed_groups_and_rows_are_skipped_and_good_rows_survive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "a1": [
            "not-a-dict-group",
            {"msgArray": "not-a-list"},
            {"no_msg_array": True},
            {
                "msgArray": [
                    "not-a-dict-row",
                    _raw(symbol=""),  # empty symbol
                    _raw(symbol=50),  # non-string symbol
                    _raw(as_of="2026-08-28"),  # unparseable as-of date
                    _raw(as_of=None),
                    _raw(symbol="0056"),
                ]
            },
            {"msgArray": [_raw(symbol="00878")]},
        ]
    }
    _patch(monkeypatch, body)

    rows, status, detail = await _fetch()

    assert status is HealthStatus.OK
    assert detail == "ok"
    assert [r["symbol"] for r in rows] == ["0056", "00878"]
    first = rows[0]
    assert first["market"] == Market.TW.value
    assert first["as_of"] == "2026-08-28"
    assert first["fund_name"] == "元大台灣50"
    assert first["units_outstanding"] == 1000
    assert first["units_outstanding_change"] == -5
    assert first["close_price"] == 106.95
    # fields the row does not carry parse to None, never 0
    assert first["estimated_nav"] is None
    assert first["premium_discount_pct"] is None
    assert first["prior_day_nav"] is None


async def test_groups_with_only_unusable_rows_are_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, {"a1": [{"msgArray": [_raw(symbol="")]}, {"msgArray": []}]})

    rows, status, detail = await _fetch()

    assert (rows, status) == ([], HealthStatus.STALE)
    assert detail == "no ETF rows parsed from all_etf.txt"


async def test_an_http_error_status_is_unreachable_with_the_error_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, {"a1": []}, status_code=503)

    rows, status, detail = await _fetch()

    assert (rows, status) == ([], HealthStatus.UNREACHABLE)
    assert detail == "transport error: HTTPStatusError"


async def test_a_body_that_is_not_json_propagates_the_decode_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_etf_nav_module, "build_async_client", factory)

    with pytest.raises(ValueError):
        await fetch_whole_market_etf_nav(tls_policy=TLSPolicy.STRICT)
