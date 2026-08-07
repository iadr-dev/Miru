"""PTT adapter — social-buzz layer, board-index-listing scrape (MVP scope).

Boards are tagged by purpose, never mixed:

- `Stock` (股票版), `Tech_Job` (科技業板) → `stock_buzz` — retail/industry chatter usable in
  analysis, always caveated **unverified retail opinion**.
- `Beauty` (表特版), `Gossiping` (八卦版) → `offtopic` — general-buzz for a whimsy/mascot
  surface, explicitly excluded from any market claim by the `offtopic` tag downstream
  guardrails key on.

Scope is deliberately the board-index listing only (title, author, push-count, post tag) —
not a full push/boo-count-per-comment scrape of every post body, which is a heavier follow-up.
The upstream HTML is parsed into `PttPost` immediately; no raw HTML crosses this module's
boundary.

Not wired into `config/source_priority.yaml`'s `(market, capability)` priority table — a board
name is a required per-call parameter, so this isn't a drop-in fallback for the `news`
capability the way `twse_openapi` is a fallback for `finmind`. Callers (a dedicated
`ptt_buzz`-style tool) invoke `fetch_board` directly, the same pattern `web_search.py` uses for
`WEB`; `fetch`/`supports` still exist so all network I/O stays inside `sources/` and the
`SourceAdapter`-shaped surface is available if a future router extension wants it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

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

_STOCK_BUZZ_TAG = "stock_buzz"
_OFFTOPIC_TAG = "offtopic"

_BOARD_TAGS: dict[str, str] = {
    "Stock": _STOCK_BUZZ_TAG,
    "Tech_Job": _STOCK_BUZZ_TAG,
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


def _parse_entry(entry: Node, *, board: str, tag: str) -> tuple[PttPost | None, bool]:
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
    post = PttPost(
        board=board,
        board_tag=tag,
        title=raw_title,
        post_tag=_extract_post_tag(raw_title),
        author=author,
        push_count=push_count or "0",
        link=link,
    )
    return post, False


def _parse_index_html(board: str, html: str) -> _BoardParseResult:
    tag = _BOARD_TAGS.get(board, _OFFTOPIC_TAG)
    tree = HTMLParser(html)
    posts: list[PttPost] = []
    deleted = 0
    for entry in tree.css("div.r-ent"):
        post, is_deleted = _parse_entry(entry, board=board, tag=tag)
        if is_deleted:
            deleted += 1
        if post is not None:
            posts.append(post)
    return _BoardParseResult(posts=posts, deleted_count=deleted)


def _extract_post_tag(title: str) -> str | None:
    stripped = title.strip()
    if stripped.startswith("[") and "]" in stripped:
        return stripped[: stripped.index("]") + 1]
    return None


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
        return await self.fetch_board(board=board, tls_policy=tls_policy)

    async def fetch_board(
        self, *, board: str, tls_policy: TLSPolicy = TLSPolicy.STRICT
    ) -> SourceResult:
        """Fetch one board's index-listing page (page 1 = the newest listing)."""
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
        url = f"{_BASE_URL}/bbs/{board}/index.html"
        try:
            async with build_async_client(tls_policy=tls_policy) as client:
                response = await client.get(url)
                response.raise_for_status()
                html = response.text
        except httpx.HTTPError as exc:
            return _result(
                board_tag=tag,
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
                ),
            )

        parsed = _parse_index_html(board, html)
        if not parsed.posts:
            return _result(
                board_tag=tag,
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE,
                    detail=f"no posts parsed from {board} index (deleted={parsed.deleted_count})",
                ),
            )
        return _result(
            board_tag=tag,
            payload={"posts": [post.model_dump(mode="json") for post in parsed.posts]},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )


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
