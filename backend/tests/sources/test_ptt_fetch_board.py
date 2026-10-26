"""Characterization of `PttAdapter.fetch_board` page-walk branches — stubbed transport only."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest

import sources.ptt as ptt_module
from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.ptt import (
    MAX_INDEX_PAGES_PER_FETCH,
    SINGLE_PAGE_MIN_POSTS,
    SINGLE_PAGE_TOP_UP_MAX_PAGES,
    PttAdapter,
)
from tests.sources.test_ptt import _index_page_html

_NOW = datetime.now(UTC)
_TODAY = f"{_NOW.month}/{_NOW.day}"
_OLD_DATE = _NOW - timedelta(days=20)
_OLD = f"{_OLD_DATE.month}/{_OLD_DATE.day}"


def _page(n: int, *, count: int = 1, date: str = _TODAY, has_prev: bool = True) -> str:
    entries = [(date, f"[標的] p{n}-{i}") for i in range(count)]
    prev = f"/bbs/Stock/index{n + 1}.html" if has_prev else None
    return _index_page_html(entries=entries, prev_href=prev)


def _install(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ptt_module, "build_async_client", factory)


def _chain(
    monkeypatch: pytest.MonkeyPatch, pages: dict[str, str], seen: list[str], default: str = ""
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=pages.get(request.url.path, default))

    _install(monkeypatch, handler)


def _titles(result: object) -> list[str]:
    payload = result.payload  # type: ignore[attr-defined]
    return [p["title"] for p in payload["posts"]]


async def test_single_page_with_enough_posts_stops_after_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    _chain(monkeypatch, {"/bbs/Stock/index.html": _page(1, count=SINGLE_PAGE_MIN_POSTS)}, seen)

    result = await PttAdapter().fetch_board(board="Stock")

    assert len(seen) == 1
    assert len(result.payload["posts"]) == SINGLE_PAGE_MIN_POSTS  # type: ignore[arg-type]
    assert result.health.status is HealthStatus.OK
    assert result.health.detail == "ok"


async def test_single_page_tops_up_from_older_page_until_minimum_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    pages = {
        "/bbs/Stock/index.html": _page(0, count=SINGLE_PAGE_MIN_POSTS - 1),
        "/bbs/Stock/index1.html": _page(1, count=2),
        "/bbs/Stock/index2.html": _page(2, count=2),
    }
    _chain(monkeypatch, pages, seen)

    result = await PttAdapter().fetch_board(board="Stock", lookback_days=1)

    assert len(seen) == 2  # 39 + 2 >= 40 -> stop, never reaches page 3
    assert len(result.payload["posts"]) == SINGLE_PAGE_MIN_POSTS + 1  # type: ignore[arg-type]


async def test_single_page_top_up_is_capped_by_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    pages = {"/bbs/Stock/index.html": _page(0)} | {
        f"/bbs/Stock/index{n}.html": _page(n) for n in range(1, 20)
    }
    _chain(monkeypatch, pages, seen)

    result = await PttAdapter().fetch_board(board="Stock", lookback_days=1)

    assert len(seen) == SINGLE_PAGE_TOP_UP_MAX_PAGES
    assert len(result.payload["posts"]) == SINGLE_PAGE_TOP_UP_MAX_PAGES  # type: ignore[arg-type]


async def test_lookback_zero_and_negative_behave_like_single_page_without_date_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _index_page_html(
        entries=[(_OLD, "[標的] ancient"), (_TODAY, "[標的] today")], prev_href=None
    )
    for lookback in (0, -5):
        seen: list[str] = []
        _chain(monkeypatch, {"/bbs/Stock/index.html": page}, seen)
        result = await PttAdapter().fetch_board(board="Stock", lookback_days=lookback)
        assert len(seen) == 1
        assert len(result.payload["posts"]) == 2  # type: ignore[arg-type]


async def test_no_prev_link_ends_the_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    _chain(monkeypatch, {"/bbs/Stock/index.html": _page(0, has_prev=False)}, seen)

    result = await PttAdapter().fetch_board(board="Stock", lookback_days=5)

    assert len(seen) == 1
    assert result.health.status is HealthStatus.OK


async def test_wide_lookback_walk_is_bounded_by_max_index_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    pages = {"/bbs/Stock/index.html": _page(0)} | {
        f"/bbs/Stock/index{n}.html": _page(n) for n in range(1, 40)
    }
    _chain(monkeypatch, pages, seen)

    result = await PttAdapter().fetch_board(board="Stock", lookback_days=30)

    assert len(seen) == MAX_INDEX_PAGES_PER_FETCH
    assert len(result.payload["posts"]) == MAX_INDEX_PAGES_PER_FETCH  # type: ignore[arg-type]


async def test_wide_lookback_stops_at_cutoff_page_and_filters_old_keeps_undated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    page = _index_page_html(
        entries=[(_TODAY, "[標的] fresh"), ("", "[標的] undated"), (_OLD, "[標的] stale")],
        prev_href="/bbs/Stock/index1.html",
    )
    _chain(monkeypatch, {"/bbs/Stock/index.html": page}, seen)

    result = await PttAdapter().fetch_board(board="Stock", lookback_days=3)

    assert len(seen) == 1  # the page crossed the cutoff -> no older page requested
    assert _titles(result) == ["[標的] fresh", "[標的] undated"]


async def test_absolute_prev_href_is_followed_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    first = _index_page_html(
        entries=[(_TODAY, "[標的] a")], prev_href="https://other.example/bbs/Stock/index9.html"
    )
    second = _page(9, has_prev=False)
    _chain(monkeypatch, {"/bbs/Stock/index.html": first, "/bbs/Stock/index9.html": second}, seen)

    result = await PttAdapter().fetch_board(board="Stock", lookback_days=5)

    assert seen == [
        "https://www.ptt.cc/bbs/Stock/index.html",
        "https://other.example/bbs/Stock/index9.html",
    ]
    assert len(result.payload["posts"]) == 2  # type: ignore[arg-type]


async def test_later_page_transport_error_keeps_earlier_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bbs/Stock/index.html":
            return httpx.Response(200, text=_page(0))
        raise httpx.ConnectError("boom")

    _install(monkeypatch, handler)

    result = await PttAdapter().fetch_board(board="Stock", lookback_days=5)

    assert result.health.status is HealthStatus.OK
    assert _titles(result) == ["[標的] p0-0"]


async def test_later_page_http_status_error_keeps_earlier_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bbs/Stock/index.html":
            return httpx.Response(200, text=_page(0))
        return httpx.Response(503, text="down")

    _install(monkeypatch, handler)

    result = await PttAdapter().fetch_board(board="Stock", lookback_days=1)

    assert result.health.status is HealthStatus.OK
    assert _titles(result) == ["[標的] p0-0"]


async def test_first_page_http_status_error_is_unreachable_with_exception_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, lambda _r: httpx.Response(500, text="no"))

    result = await PttAdapter().fetch_board(board="Stock")

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.health.detail == "transport error: HTTPStatusError"
    assert result.payload == {}


async def test_no_posts_is_stale_and_reports_deleted_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted = '<div class="r-ent"><div class="title">(本文已被刪除)</div></div>'
    html = f"<html><body>{deleted}{deleted}</body></html>"
    _chain(monkeypatch, {"/bbs/Stock/index.html": html}, [])

    result = await PttAdapter().fetch_board(board="Stock")

    assert result.health.status is HealthStatus.STALE
    assert result.health.detail == "no posts parsed from Stock index (deleted=2)"
    assert result.payload == {}


async def test_deleted_rows_are_skipped_but_other_posts_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted = '<div class="r-ent"><div class="title">(本文已被刪除)</div></div>'
    html = _page(0, has_prev=False).replace("</body>", f"{deleted}</body>")
    _chain(monkeypatch, {"/bbs/Stock/index.html": html}, [])

    result = await PttAdapter().fetch_board(board="Stock")

    assert _titles(result) == ["[標的] p0-0"]


async def test_fetch_param_handling_for_board_and_lookback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sym = Symbol(id="2330", market=Market.TW)
    seen: list[str] = []
    pages = {"/bbs/Stock/index.html": _page(0)} | {
        f"/bbs/Stock/index{n}.html": _page(n) for n in range(1, 20)
    }
    _chain(monkeypatch, pages, seen)
    adapter = PttAdapter()

    # empty board falls back to Stock; non-int lookback falls back to 1 (top-up cap of 3 pages)
    await adapter.fetch(
        Capability.NEWS, sym, tls_policy=TLSPolicy.STRICT, board="", lookback_days="9"
    )
    assert len(seen) == SINGLE_PAGE_TOP_UP_MAX_PAGES

    seen.clear()
    await adapter.fetch(Capability.NEWS, sym, tls_policy=TLSPolicy.STRICT, lookback_days=30)
    assert len(seen) == MAX_INDEX_PAGES_PER_FETCH


async def test_unknown_board_reports_declared_boards_message() -> None:
    result = await PttAdapter().fetch_board(board="Nope")

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
    assert result.health.detail == "board 'Nope' is not one of the declared PTT boards"
