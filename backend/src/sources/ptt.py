"""PTT adapter — social-buzz layer, board-index-listing scrape (MVP scope).

Boards are tagged by purpose, never mixed:

- `Stock` (股票版), `Tech_Job` (科技業板), `Warrant` (權證版) → `stock_buzz` — retail/industry
  chatter usable in analysis, always caveated **unverified retail opinion**.
- `Beauty` (表特版), `Gossiping` (八卦版) → `offtopic` — general-buzz for a whimsy/mascot
  surface, explicitly excluded from any market claim by the `offtopic` tag downstream
  guardrails key on.

Scope is the board-index listing (title, author, push-count, post tag) for `fetch_board`, plus
a per-post BODY fetch for `search_boards` (the per-symbol best-effort search — title-only
matching misses most real posts, since PTT posts rarely name a stock in the title). Comment
threads (`div.push`) are excluded from the body text `search_boards` checks — pushes are reader
reactions, not the article's own content. The upstream HTML is parsed immediately in both
paths; no raw HTML crosses this module's boundary.

Not wired into `config/source_priority.yaml`'s `(market, capability)` priority table — a board
name is a required per-call parameter, so this isn't a drop-in fallback for the `news`
capability the way `twse_openapi` is a fallback for `finmind`. Callers (a dedicated
`ptt_buzz`-style tool) invoke `fetch_board` directly, the same pattern `web_search.py` uses for
`WEB`; `fetch`/`supports` still exist so all network I/O stays inside `sources/` and the
`SourceAdapter`-shaped surface is available if a future router extension wants it.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast

import httpx
import structlog
from selectolax.parser import HTMLParser, Node

from schemas.capabilities import PttPost
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_BASE_URL = "https://www.ptt.cc"

# Politeness floor — this adapter is user-driven only; a coarse scheduler refresh (if any) must
# not poll a single board more often than this. Enforcement is the caller's responsibility;
# this constant documents the contract.
MIN_SECONDS_BETWEEN_REQUESTS = 60.0

# A caller asking for N days of history walks back at most this many index pages before giving
# up — a hard ceiling so a quiet board (few posts/day) can't turn one request into dozens of
# sequential upstream fetches. ~20 posts/page on Stock is typical, so 12 pages covers a week
# even on a slow news day; a board that's still short of the requested window at this ceiling
# just returns whatever it found (never blocks indefinitely chasing a full week).
MAX_INDEX_PAGES_PER_FETCH = 12

# The newest index page is often only partly filled (a page holds ~20 rows), and a quiet board
# (Warrant) may have only a handful there. A single-page (`lookback_days<=1`) fetch that comes
# back short keeps reading older pages, undated, until it has this many posts or has read
# `SINGLE_PAGE_TOP_UP_MAX_PAGES` pages, so the board shows its latest activity, not 4 rows.
SINGLE_PAGE_MIN_POSTS = 40  # the PTT tab shows 40 posts before 展開
SINGLE_PAGE_TOP_UP_MAX_PAGES = 3

# `search_boards` (per-symbol PTT best-effort match) fetches each candidate post's own page to
# search its BODY text, not just the title — most real posts about a stock never name it in the
# title (confirmed empirically: a week of Stock/Tech_Job/Warrant index titles for 2330/台積電
# had zero title hits despite real market chatter existing). This is one extra request PER
# candidate post, so it is hard-capped regardless of how many posts the lookback window
# contains — a quiet week costs a handful of fetches, a busy one still costs at most this many,
# never an unbounded fan-out across 3 boards x 12 pages each.
MAX_BODY_FETCHES_PER_SEARCH = 60
# How many body-fetches run concurrently — bounded so a search doesn't open 60 simultaneous
# connections to ptt.cc at once (impolite and likely to trip rate limiting).
BODY_FETCH_CONCURRENCY = 6

_PREV_PAGE_LABEL = "上頁"
_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})")

_STOCK_BUZZ_TAG = "stock_buzz"
_OFFTOPIC_TAG = "offtopic"

_BOARD_TAGS: dict[str, str] = {
    "Stock": _STOCK_BUZZ_TAG,
    "Tech_Job": _STOCK_BUZZ_TAG,
    "Warrant": _STOCK_BUZZ_TAG,
    "Beauty": _OFFTOPIC_TAG,
    "Gossiping": _OFFTOPIC_TAG,
}

_STOCK_CAVEAT = (
    "PTT board-index listing — unverified retail/industry chatter, not vetted analysis; "
    "buzz/attention signal only, never a trading recommendation."
)
_OFFTOPIC_CAVEAT = (
    "PTT off-topic board listing — general-buzz content for the mascot surface only; "
    "excluded from any stock-analysis or market claim."
)


@dataclass(frozen=True)
class _BoardParseResult:
    posts: list[PttPost]
    deleted_count: int
    prev_page_href: str | None  # relative href of the "上頁" (older posts) nav link, if present


def _resolve_posted_date(raw_date: str, *, now: datetime) -> str | None:
    """PTT's index carries a bare `M/D` with no year — resolve against `now`: a month greater
    than the current month must be last year (an index page never shows a future date), same
    disambiguation every PTT scraper needs. Returns `None` (never a guess) if the text doesn't
    match the expected shape at all."""
    match = _DATE_RE.search(raw_date)
    if match is None:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    try:
        resolved = date(now.year, month, day)
    except ValueError:
        return None
    # Later than tomorrow means last year: 10/19 seen on 10/16 is last October, not this one. One
    # day of slack because `now` is UTC and a Taipei post can already carry tomorrow's date.
    if resolved > now.date() + timedelta(days=1):
        try:
            resolved = date(now.year - 1, month, day)
        except ValueError:  # 2/29 in a non-leap year
            return None
    return resolved.isoformat()


def _parse_entry(
    entry: Node, *, board: str, tag: str, now: datetime
) -> tuple[PttPost | None, bool]:
    """One `div.r-ent` listing row -> `(post, is_deleted)`. `is_deleted=True` only for a
    removed/self-deleted post (still occupies a row but has no title/link node — never
    fabricate a record for it); a row with a link but empty title text yields
    `(None, False)` (skipped, but not counted as a deletion — distinct, pre-existing cases).
    Pulled out of `_parse_index_html` purely to keep that function's loop body shallow;
    identical parsing, just named."""
    title_node = entry.css_first("div.title")
    link_node = title_node.css_first("a") if title_node is not None else None
    if title_node is None or link_node is None:
        return None, True
    raw_title = link_node.text(strip=True)
    if not raw_title:
        return None, False
    href = link_node.attributes.get("href") or ""
    link = f"{_BASE_URL}{href}" if href.startswith("/") else href
    author_node = entry.css_first("div.author")
    author = author_node.text(strip=True) if author_node is not None else ""
    push_node = entry.css_first("div.nrec")
    push_count = push_node.text(strip=True) if push_node is not None else ""
    date_node = entry.css_first("div.date")
    posted_date = _resolve_posted_date(date_node.text(strip=True), now=now) if date_node else None
    post = PttPost(
        board=board,
        board_tag=tag,
        title=raw_title,
        post_tag=_extract_post_tag(raw_title),
        author=author,
        push_count=push_count or "0",
        link=link,
        posted_date=posted_date,
    )
    return post, False


def _find_prev_page_href(tree: HTMLParser) -> str | None:
    # ptt.cc renders the link as "&lsaquo; 上頁", so match the label alone.
    for node in tree.css("a.btn.wide"):
        if _PREV_PAGE_LABEL in node.text(strip=True):
            return node.attributes.get("href")
    return None


def _listing_entries(tree: HTMLParser) -> list[Node]:
    """The `div.r-ent` rows above the newest page's `div.r-list-sep` line. Rows below it are the
    board's pinned 置底 posts (板規, 公告), often months old: they are not today's chatter, and
    counting their dates made a multi-day walk stop on page 1 every time. Walks the listing's
    children in page order (a grouped CSS selector does not keep document order)."""
    container = tree.css_first("div.r-list-container")
    if container is None:
        return list(tree.css("div.r-ent"))
    entries: list[Node] = []
    for node in container.iter():
        classes = (node.attributes.get("class") or "").split()
        if "r-list-sep" in classes:
            break
        if "r-ent" in classes:
            entries.append(node)
    return entries


def _parse_index_html(board: str, html: str, *, now: datetime) -> _BoardParseResult:
    tag = _BOARD_TAGS.get(board, _OFFTOPIC_TAG)
    tree = HTMLParser(html)
    posts: list[PttPost] = []
    deleted = 0
    for entry in _listing_entries(tree):
        post, is_deleted = _parse_entry(entry, board=board, tag=tag, now=now)
        if is_deleted:
            deleted += 1
        if post is not None:
            posts.append(post)
    return _BoardParseResult(
        posts=posts, deleted_count=deleted, prev_page_href=_find_prev_page_href(tree)
    )


def _extract_post_tag(title: str) -> str | None:
    stripped = title.strip()
    if stripped.startswith("[") and "]" in stripped:
        return stripped[: stripped.index("]") + 1]
    return None


@dataclass(frozen=True)
class _IndexWalk:
    posts: list[PttPost]
    deleted: int
    error: httpx.HTTPError | None  # the transport failure that ended the walk, if any


def _in_lookback_window(post: PttPost, cutoff: str) -> bool:
    return post.posted_date is None or post.posted_date >= cutoff


def _older_page_url(prev_page_href: str | None) -> str | None:
    return (
        f"{_BASE_URL}{prev_page_href}"
        if prev_page_href and prev_page_href.startswith("/")
        else prev_page_href
    )


def _walk_is_done(
    page_posts: list[PttPost],
    *,
    total_posts: int,
    page_number: int,
    lookback_days: int,
    cutoff: str,
) -> bool:
    if lookback_days <= 1:
        return total_posts >= SINGLE_PAGE_MIN_POSTS or page_number >= SINGLE_PAGE_TOP_UP_MAX_PAGES
    return any(not _in_lookback_window(p, cutoff) for p in page_posts)


async def _get_index_html(url: str, *, tls_policy: TLSPolicy) -> str:
    async with build_async_client(tls_policy=tls_policy) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def _walk_index_pages(
    board: str, *, tls_policy: TLSPolicy, lookback_days: int, now: datetime, cutoff: str
) -> _IndexWalk:
    """Pages of one board's index, newest first, following each page's "上頁" link until the
    walk is done. A transport failure ends the walk and is carried in `error`; the caller
    only reports it when no earlier page produced a post (never discards earlier pages)."""
    all_posts: list[PttPost] = []
    deleted_total = 0
    next_url: str | None = f"{_BASE_URL}/bbs/{board}/index.html"
    for page_number in range(1, MAX_INDEX_PAGES_PER_FETCH + 1):
        if next_url is None:
            break
        try:
            html = await _get_index_html(next_url, tls_policy=tls_policy)
        except httpx.HTTPError as exc:
            return _IndexWalk(posts=all_posts, deleted=deleted_total, error=exc)
        parsed = _parse_index_html(board, html, now=now)
        deleted_total += parsed.deleted_count
        all_posts.extend(parsed.posts)
        if _walk_is_done(
            parsed.posts,
            total_posts=len(all_posts),
            page_number=page_number,
            lookback_days=lookback_days,
            cutoff=cutoff,
        ):
            break
        next_url = _older_page_url(parsed.prev_page_href)
    return _IndexWalk(posts=all_posts, deleted=deleted_total, error=None)


class PttAdapter:
    """The `PTT` source adapter — board-index scrape."""

    source_id = "PTT"
    timing_class = TimingClass.REFERENCE

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.NEWS, Market.TW)}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        posts = raw.get("posts")
        if not isinstance(posts, list):
            raise ValueError("PTT response missing 'posts' list")
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        if cap is not Capability.NEWS:
            return _result(
                board_tag=_OFFTOPIC_TAG,
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail=f"PTT does not serve capability {cap.value}",
                ),
            )
        board = str(params.get("board") or "Stock")
        lookback_days = params.get("lookback_days")
        return await self.fetch_board(
            board=board,
            tls_policy=tls_policy,
            lookback_days=int(lookback_days) if isinstance(lookback_days, int) else 1,
        )

    async def fetch_board(
        self, *, board: str, tls_policy: TLSPolicy = TLSPolicy.STRICT, lookback_days: int = 1
    ) -> SourceResult:
        """Fetch one board's index listing, walking back through older pages (via each page's
        own "上頁" nav link) until a post on the current page is older than `lookback_days`.
        `lookback_days=1` (the default) reads the newest page, plus older pages only while it has
        fewer than `SINGLE_PAGE_MIN_POSTS` posts (see that constant), with no date filter. Pinned
        置底 posts are never included. Bounded by `MAX_INDEX_PAGES_PER_FETCH` so a quiet board
        can't turn a wide lookback into an unbounded fetch chain."""
        tag = _BOARD_TAGS.get(board)
        if tag is None:
            return _result(
                board_tag=_OFFTOPIC_TAG,
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail=f"board {board!r} is not one of the declared PTT boards",
                ),
            )

        now = datetime.now(UTC)
        cutoff = (now - timedelta(days=lookback_days)).date().isoformat()
        walk = await _walk_index_pages(
            board, tls_policy=tls_policy, lookback_days=lookback_days, now=now, cutoff=cutoff
        )
        all_posts = walk.posts
        if not all_posts:
            if walk.error is not None:
                return _result(
                    board_tag=tag,
                    payload={},
                    health=SourceHealth(
                        status=HealthStatus.UNREACHABLE,
                        detail=f"transport error: {type(walk.error).__name__}",
                    ),
                )
            return _result(
                board_tag=tag,
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE,
                    detail=f"no posts parsed from {board} index (deleted={walk.deleted})",
                ),
            )
        # `lookback_days<=1` is the original single-page behavior verbatim — no date filter at
        # all (some real posts carry no parseable date; filtering them out would be a
        # regression for the plain "today's index" caller this tool has always served). A wider
        # lookback DOES filter, since its whole point is bounding the multi-page walk to a window.
        out_posts = (
            all_posts
            if lookback_days <= 1
            else [p for p in all_posts if _in_lookback_window(p, cutoff)]
        )
        return _result(
            board_tag=tag,
            payload={"posts": [post.model_dump(mode="json") for post in out_posts]},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )

    async def search_boards(
        self,
        *,
        boards: list[str],
        needles: list[str],
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
        lookback_days: int = 7,
    ) -> SourceResult:
        """Best-effort per-symbol PTT search across `boards`: fetch each board's index for
        `lookback_days` (via `fetch_board`), then fetch EVERY candidate post's own page and
        check whether its body text contains any of `needles` (a symbol id/company name) —
        title-only matching misses most real posts, since PTT posts rarely name a stock in the
        title itself. Bounded by `MAX_BODY_FETCHES_PER_SEARCH` total across all boards combined
        (not per-board) so a caller can't turn a 3-board x 7-day search into an unbounded fetch
        chain; posts past that cap are simply not checked (never silently claimed as "no
        match" — the caller sees `health` still `ok`, this is a coverage limit, not a failure).
        Still explicitly a best-effort match, never a claim of complete coverage — a post whose
        body text uses a company nickname or only refers to the stock indirectly is missed same
        as before, just far fewer of them than title-only matching missed.
        """
        board_results = await asyncio.gather(
            *(
                self.fetch_board(board=b, tls_policy=tls_policy, lookback_days=lookback_days)
                for b in boards
            )
        )
        candidates: list[PttPost] = []
        for result in board_results:
            raw_posts = result.payload.get("posts")
            if isinstance(raw_posts, list):
                typed_posts = cast("list[dict[str, object]]", raw_posts)
                candidates.extend(PttPost.model_validate(p) for p in typed_posts)
        candidates = candidates[:MAX_BODY_FETCHES_PER_SEARCH]

        semaphore = asyncio.Semaphore(BODY_FETCH_CONCURRENCY)

        async def _check(post: PttPost) -> PttPost | None:
            async with semaphore:
                body = await self._fetch_post_body(post.link, tls_policy=tls_policy)
            if body is None:
                return None
            return post if any(needle in body for needle in needles) else None

        checked = await asyncio.gather(*(_check(post) for post in candidates))
        matched = [post for post in checked if post is not None]
        return _result(
            board_tag=_STOCK_BUZZ_TAG,
            payload={"posts": [post.model_dump(mode="json") for post in matched]},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )

    async def _fetch_post_body(self, url: str, *, tls_policy: TLSPolicy) -> str | None:
        """One post's article body text, comments (`div.push`) excluded — `None` on any
        transport failure (a single unreachable post must not fail the whole search)."""
        try:
            async with build_async_client(tls_policy=tls_policy) as client:
                response = await client.get(url)
                response.raise_for_status()
                html = response.text
        except httpx.HTTPError:
            return None
        tree = HTMLParser(html)
        main = tree.css_first("#main-content")
        if main is None:
            return None
        for push in main.css("div.push"):
            push.decompose()
        return main.text(strip=True)


def _result(*, board_tag: str, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    caveat = _OFFTOPIC_CAVEAT if board_tag == _OFFTOPIC_TAG else _STOCK_CAVEAT
    return SourceResult(
        source_id=PttAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat=caveat,
        payload=payload,
        health=health,
    )


__all__ = ["PttAdapter"]
