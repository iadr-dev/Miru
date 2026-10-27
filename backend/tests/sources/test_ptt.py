"""PTT adapter tests — recorded HTML fixtures, no live network (default profile)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

import sources.ptt as ptt_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.ptt import PttAdapter, _resolve_posted_date

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ptt"


def _rows_html(entries: list[tuple[str, str]], *, start: int = 0) -> str:
    return "".join(
        f'<div class="r-ent"><div class="title"><a href="/bbs/Stock/{i}.html">{title}</a>'
        f'</div><div class="author">tester</div><div class="nrec"><span>5</span></div>'
        f'<div class="date"> {date}</div></div>'
        for i, (date, title) in enumerate(entries, start=start)
    )


def _index_page_html(
    *,
    entries: list[tuple[str, str]],
    prev_href: str | None,
    pinned: list[tuple[str, str]] | None = None,
) -> str:
    """Build a minimal PTT-index-shaped HTML page — `entries` is `[(date, title), ...]`,
    `prev_href` is the "上頁" nav link's href (`None` omits the nav entirely, matching the
    oldest page in a real board's history). `pinned` adds the newest page's 置底 rows below a
    `div.r-list-sep` line, inside the listing container as on ptt.cc."""
    nav = f'<a class="btn wide" href="{prev_href}">&lsaquo; 上頁</a>' if prev_href else ""
    if pinned is None:
        return f"<html><body>{_rows_html(entries)}{nav}</body></html>"
    listing = (
        f'<div class="r-list-container">{_rows_html(entries)}<div class="r-list-sep"></div>'
        f"{_rows_html(pinned, start=len(entries))}</div>"
    )
    return f"<html><body>{nav}{listing}</body></html>"


def _post_page_html(*, body: str, pushes: tuple[str, ...] = ()) -> str:
    """A minimal PTT-article-page-shaped HTML page — `body` is the article's own text,
    `pushes` are comment strings that must be excluded from any body-text search."""
    push_html = "".join(
        f'<div class="push"><span class="push-content">{p}</span></div>' for p in pushes
    )
    return f'<html><body><div id="main-content">{body}{push_html}</div></body></html>'


def _client_factory(html: str, status_code: int = 200) -> object:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=html)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


def test_supports_declares_tw_news() -> None:
    adapter = PttAdapter()
    assert (Capability.NEWS, Market.TW) in adapter.supports()


def test_validate_rejects_missing_posts() -> None:
    adapter = PttAdapter()
    with pytest.raises(ValueError, match="missing 'posts'"):
        adapter.validate({})


async def test_fetch_board_stock_tags_are_stock_buzz(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (FIXTURES / "stock_index.html").read_text(encoding="utf-8")
    monkeypatch.setattr(ptt_module, "build_async_client", _client_factory(html))
    adapter = PttAdapter()

    result = await adapter.fetch_board(board="Stock", tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "PTT"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    assert result.currentness_caveat
    posts = result.payload["posts"]
    assert isinstance(posts, list)
    # The deleted post (no <a> link) must never surface as a record.
    assert len(posts) == 3
    assert all(post["board_tag"] == "stock_buzz" for post in posts)  # type: ignore[index]
    tags = {post["post_tag"] for post in posts}  # type: ignore[index]
    assert tags == {"[標的]", "[新聞]", "[心得]"}
    push_counts = {post["push_count"] for post in posts}  # type: ignore[index]
    assert "24" in push_counts
    assert "爆" in push_counts


async def test_fetch_board_beauty_tags_are_offtopic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Beauty/Gossiping results must carry `offtopic` — load-bearing for the guardrail that
    keeps them out of any market claim."""
    html = (FIXTURES / "beauty_index.html").read_text(encoding="utf-8")
    monkeypatch.setattr(ptt_module, "build_async_client", _client_factory(html))
    adapter = PttAdapter()

    result = await adapter.fetch_board(board="Beauty", tls_policy=TLSPolicy.STRICT)

    posts = result.payload["posts"]
    assert isinstance(posts, list)
    assert len(posts) == 2
    assert all(post["board_tag"] == "offtopic" for post in posts)  # type: ignore[index]
    assert "mascot" in result.currentness_caveat or "off-topic" in result.currentness_caveat


async def test_stock_and_beauty_tags_never_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    stock_html = (FIXTURES / "stock_index.html").read_text(encoding="utf-8")
    beauty_html = (FIXTURES / "beauty_index.html").read_text(encoding="utf-8")

    monkeypatch.setattr(ptt_module, "build_async_client", _client_factory(stock_html))
    adapter = PttAdapter()
    stock_result = await adapter.fetch_board(board="Stock", tls_policy=TLSPolicy.STRICT)

    monkeypatch.setattr(ptt_module, "build_async_client", _client_factory(beauty_html))
    beauty_result = await adapter.fetch_board(board="Beauty", tls_policy=TLSPolicy.STRICT)

    stock_posts = stock_result.payload["posts"]
    beauty_posts = beauty_result.payload["posts"]
    assert isinstance(stock_posts, list)
    assert isinstance(beauty_posts, list)
    stock_tags = {post["board_tag"] for post in stock_posts}  # type: ignore[index]
    beauty_tags = {post["board_tag"] for post in beauty_posts}  # type: ignore[index]
    assert stock_tags == {"stock_buzz"}
    assert beauty_tags == {"offtopic"}
    assert stock_tags.isdisjoint(beauty_tags)


async def test_fetch_board_rejects_unknown_board() -> None:
    adapter = PttAdapter()

    result = await adapter.fetch_board(board="NotABoard", tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
    assert result.payload == {}


async def test_fetch_board_degrades_honestly_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ptt_module, "build_async_client", factory)
    adapter = PttAdapter()

    result = await adapter.fetch_board(board="Stock", tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload == {}


async def test_fetch_dispatches_via_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (FIXTURES / "stock_index.html").read_text(encoding="utf-8")
    monkeypatch.setattr(ptt_module, "build_async_client", _client_factory(html))
    adapter = PttAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.NEWS, sym, tls_policy=TLSPolicy.STRICT, board="Stock")

    assert result.health.status is HealthStatus.OK


async def test_fetch_unsupported_capability_returns_unavailable_tier() -> None:
    adapter = PttAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.ESG, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
    assert result.payload == {}


def test_resolve_posted_date_same_year() -> None:
    now = datetime(2026, 9, 9, tzinfo=UTC)
    assert _resolve_posted_date(" 9/05", now=now) == "2026-09-05"


def test_resolve_posted_date_rolls_back_a_year_for_future_month() -> None:
    """A month greater than the current month can't be this year — an index page never shows
    a future date, so it must be last year's occurrence of that month/day."""
    now = datetime(2026, 1, 15, tzinfo=UTC)
    assert _resolve_posted_date(" 12/20", now=now) == "2025-12-20"


def test_resolve_posted_date_rolls_back_a_year_for_a_later_day_this_month() -> None:
    now = datetime(2026, 10, 16, tzinfo=UTC)
    assert _resolve_posted_date("10/19", now=now) == "2025-10-19"
    # Tomorrow stays this year: `now` is UTC, a Taipei post may already carry tomorrow's date.
    assert _resolve_posted_date("10/17", now=now) == "2026-10-17"


def test_resolve_posted_date_malformed_returns_none() -> None:
    now = datetime(2026, 9, 9, tzinfo=UTC)
    assert _resolve_posted_date("not-a-date", now=now) is None


async def test_fetch_board_lookback_1_returns_page_1_verbatim_no_date_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`lookback_days=1` (the default) must behave exactly like the original single-page
    fetch — no date filtering at all, even for a post whose date doesn't match "today" (the
    fixture files are static, recorded on a past date, and must keep passing forever)."""
    html = (FIXTURES / "stock_index.html").read_text(encoding="utf-8")
    monkeypatch.setattr(ptt_module, "build_async_client", _client_factory(html))
    adapter = PttAdapter()

    result = await adapter.fetch_board(board="Stock", tls_policy=TLSPolicy.STRICT, lookback_days=1)

    posts = result.payload["posts"]
    assert isinstance(posts, list)
    assert len(posts) == 3  # same as the single-page test — no page walk, no date drop


async def test_fetch_board_walks_back_pages_within_lookback_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`lookback_days>1` walks the "上頁" chain until a page's posts fall outside the window,
    merging every page's posts up to (and including) the one that crosses the cutoff."""
    now = datetime.now(UTC)
    today = f"{now.month}/{now.day}"
    page1 = _index_page_html(
        entries=[(today, "[標的] page1 post")], prev_href="/bbs/Stock/index2.html"
    )
    # 10 days ago — outside a 3-day lookback window regardless of when this test runs.
    old_date = now - timedelta(days=10)
    old = f"{old_date.month}/{old_date.day}"
    page2 = _index_page_html(entries=[(old, "[標的] page2 old post")], prev_href=None)

    pages = {"/bbs/Stock/index.html": page1, "/bbs/Stock/index2.html": page2}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        return httpx.Response(200, text=pages.get(path, page2))

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ptt_module, "build_async_client", factory)
    adapter = PttAdapter()

    result = await adapter.fetch_board(board="Stock", tls_policy=TLSPolicy.STRICT, lookback_days=3)

    posts = result.payload["posts"]
    assert isinstance(posts, list)
    titles = {post["title"] for post in posts}  # type: ignore[index]
    assert "page1 post" in "".join(titles)
    # The old page2 post (well outside the 3-day window) must be filtered out of the final
    # result even though its page was fetched (needed to discover the walk should stop there).
    assert not any("page2 old post" in t for t in titles)


async def test_search_boards_matches_on_body_text_not_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of `search_boards`: a post whose TITLE never names the stock still
    matches because its BODY does — title-only matching would miss this entirely."""
    today = f"{datetime.now(UTC).month}/{datetime.now(UTC).day}"
    index_html = _index_page_html(entries=[(today, "[心得] 隨便聊聊")], prev_href=None)
    post_html = _post_page_html(body="我覺得台積電這季表現不錯")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("index.html"):
            return httpx.Response(200, text=index_html)
        return httpx.Response(200, text=post_html)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ptt_module, "build_async_client", factory)
    adapter = PttAdapter()

    result = await adapter.search_boards(
        boards=["Stock"], needles=["2330", "台積電"], tls_policy=TLSPolicy.STRICT, lookback_days=7
    )

    posts = result.payload["posts"]
    assert isinstance(posts, list)
    assert len(posts) == 1
    assert posts[0]["title"] == "[心得] 隨便聊聊"  # type: ignore[index]


async def test_search_boards_excludes_push_comments_from_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stock mention that only appears in a PUSH comment (a reader's reaction, not the
    article's own content) must not count as a match."""
    today = f"{datetime.now(UTC).month}/{datetime.now(UTC).day}"
    index_html = _index_page_html(entries=[(today, "[心得] 完全無關的話題")], prev_href=None)
    post_html = _post_page_html(body="今天天氣真好", pushes=("推 網友:台積電要噴了",))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("index.html"):
            return httpx.Response(200, text=index_html)
        return httpx.Response(200, text=post_html)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ptt_module, "build_async_client", factory)
    adapter = PttAdapter()

    result = await adapter.search_boards(
        boards=["Stock"], needles=["2330", "台積電"], tls_policy=TLSPolicy.STRICT, lookback_days=7
    )

    assert result.payload["posts"] == []


async def test_search_boards_no_match_returns_empty_but_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today = f"{datetime.now(UTC).month}/{datetime.now(UTC).day}"
    index_html = _index_page_html(entries=[(today, "[心得] 完全無關的話題")], prev_href=None)
    post_html = _post_page_html(body="今天天氣真好")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("index.html"):
            return httpx.Response(200, text=index_html)
        return httpx.Response(200, text=post_html)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ptt_module, "build_async_client", factory)
    adapter = PttAdapter()

    result = await adapter.search_boards(
        boards=["Stock"], needles=["2330", "台積電"], tls_policy=TLSPolicy.STRICT, lookback_days=7
    )

    assert result.health.status is HealthStatus.OK
    assert result.payload["posts"] == []


async def test_search_boards_all_boards_unreachable_is_unreachable_not_empty_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(500, text="down"))
        )

    monkeypatch.setattr(ptt_module, "build_async_client", factory)

    result = await PttAdapter().search_boards(
        boards=["Stock", "Warrant"], needles=["2330"], tls_policy=TLSPolicy.STRICT, lookback_days=7
    )

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "posts" not in result.payload


async def test_search_boards_body_fetch_transport_error_skips_post_not_whole_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One candidate post's body being unreachable must not fail the whole search — it's just
    not checked, the other candidates still are."""
    today = f"{datetime.now(UTC).month}/{datetime.now(UTC).day}"
    index_html = _index_page_html(
        entries=[(today, "[心得] 第一篇"), (today, "[心得] 第二篇")], prev_href=None
    )
    good_post_html = _post_page_html(body="這是台積電相關內容")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("index.html"):
            return httpx.Response(200, text=index_html)
        if "0.html" in request.url.path:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, text=good_post_html)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ptt_module, "build_async_client", factory)
    adapter = PttAdapter()

    result = await adapter.search_boards(
        boards=["Stock"], needles=["台積電"], tls_policy=TLSPolicy.STRICT, lookback_days=7
    )

    posts = result.payload["posts"]
    assert isinstance(posts, list)
    assert len(posts) == 1
    assert posts[0]["title"] == "[心得] 第二篇"  # type: ignore[index]


async def test_fetch_board_skips_pinned_posts_and_keeps_walking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The newest page's pinned 置底 posts (months old) sit below `div.r-list-sep`: they are
    left out, and their old dates no longer stop a multi-day walk on page 1."""
    now = datetime.now(UTC)
    today = f"{now.month}/{now.day}"
    yesterday_at = now - timedelta(days=1)
    yesterday = f"{yesterday_at.month}/{yesterday_at.day}"
    months_ago_at = now - timedelta(days=120)
    months_ago = f"{months_ago_at.month}/{months_ago_at.day}"
    old_at = now - timedelta(days=10)
    old = f"{old_at.month}/{old_at.day}"
    page1 = _index_page_html(
        entries=[(today, "[新聞] today post")],
        prev_href="/bbs/Stock/index2.html",
        pinned=[(months_ago, "[公告] 板規")],
    )
    page2 = _index_page_html(
        entries=[(yesterday, "[標的] yesterday post")], prev_href="/bbs/Stock/index3.html"
    )
    page3 = _index_page_html(entries=[(old, "[閒聊] old post")], prev_href=None)
    pages = {
        "/bbs/Stock/index.html": page1,
        "/bbs/Stock/index2.html": page2,
        "/bbs/Stock/index3.html": page3,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(request.url.path, page3))

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ptt_module, "build_async_client", factory)

    walked = await PttAdapter().fetch_board(
        board="Stock", tls_policy=TLSPolicy.STRICT, lookback_days=3
    )
    single = await PttAdapter().fetch_board(
        board="Stock", tls_policy=TLSPolicy.STRICT, lookback_days=1
    )

    walked_posts = walked.payload["posts"]
    assert isinstance(walked_posts, list)
    titles = [post["title"] for post in walked_posts]  # type: ignore[index]
    assert titles == ["[新聞] today post", "[標的] yesterday post"]
    # A one-day fetch whose newest page is short reads older pages too, undated, so a quiet
    # board still shows its latest activity (here: all three pages, still under the minimum).
    single_posts = single.payload["posts"]
    assert isinstance(single_posts, list)
    assert [post["title"] for post in single_posts] == [  # type: ignore[index]
        "[新聞] today post",
        "[標的] yesterday post",
        "[閒聊] old post",
    ]
