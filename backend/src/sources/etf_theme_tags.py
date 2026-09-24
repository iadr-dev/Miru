"""ETF 主題/因子 (theme/factor) tag adapter — TWSE's own "ETF e添富" (ETFortune) microsite,
`https://www.twse.com.tw/zh/ETFortune/etfInfo/{symbol}`, a plain server-rendered HTML page (no
auth, no cookies, confirmed live via a bare `curl`). This is TWSE's own curated editorial
classification (高股息/高息低波動/因子投資/等權重/全市場指數/大型權值/中小型權值/科技主題型/金融/
綠能及電動車/生技/公司治理及ESG at the time of writing) — distinct from this codebase's
AI-researched stock-concept themes (`analytics/themes`), which cover common stocks, not ETFs,
and carry a `confidence` score rather than an editorial guarantee.

One GET per symbol (no whole-market dump exists for this microsite — each ETF has its own page),
parsed by locating the page's own `<div class="etf-result">...主題/因子：...</div>` block and
extracting every `<a href="/zh/ETFortune/products?tag=...">LABEL</a>` inside it. Zero tags is a
real, valid result (not every ETF is tagged) — `EtfThemeTags.tags` is `()`, never fabricated.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime

import httpx
import structlog

from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_ETF_INFO_URL = "https://www.twse.com.tw/zh/ETFortune/etfInfo/{symbol}"

_CAVEAT = (
    "主題/因子 is TWSE's own ETF e添富 editorial tag classification — refreshed on the "
    "microsite's own cadence, not derived from this codebase's holdings/industry data, and "
    "carries no history (current tags only)."
)

# Matches the run of zero-or-more `<span class="result">...</span>` immediately following the
# page's own `主題/因子：</div>` label (that label sits inside its own nested
# `<div class="text">` which closes right after it) — stopping at the first element that isn't a
# tag span (the block's own closing `</div>`, or whatever follows for a zero-tag ETF), so an
# unrelated same-shaped link elsewhere on the page (e.g. a footer nav item) is never picked up.
_THEME_BLOCK_RE = re.compile(
    r'主題/因子[：:]\s*</div>((?:\s*<span class="result">.*?</span>)*)', re.DOTALL
)
_TAG_LINK_RE = re.compile(r'<a\s+href="/zh/ETFortune/products\?tag=[^"]*">([^<]+)</a>')


def _parse_theme_tags(page_html: str) -> tuple[str, ...] | None:
    """The theme/factor labels found in the page's own result block, or `None` if the block
    itself isn't present at all (a genuinely different page shape/error page — distinct from a
    real ETF with zero tags, which still has an empty block)."""
    block_match = _THEME_BLOCK_RE.search(page_html)
    if block_match is None:
        return None
    return tuple(
        html.unescape(m.group(1)).strip() for m in _TAG_LINK_RE.finditer(block_match.group(1))
    )


class EtfThemeTagsAdapter:
    """The `ETF_THEME_TAGS` source adapter — see module docstring for exactly what it scrapes."""

    source_id = "ETF_THEME_TAGS"
    timing_class = TimingClass.OFFICIAL_EOD

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.ETF_THEME_TAGS, Market.TW)}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        tags = raw.get("tags")
        if tags is not None and not isinstance(tags, list):
            raise ValueError("ETF theme-tags payload's 'tags' is not a list")
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        del params
        if cap is not Capability.ETF_THEME_TAGS or sym.market is not Market.TW:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail=f"ETF_THEME_TAGS does not serve {cap.value} for {sym.market.value}",
                ),
            )

        try:
            async with build_async_client(tls_policy=tls_policy) as client:
                response = await client.get(_ETF_INFO_URL.format(symbol=sym.id))
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
                ),
            )

        tags = _parse_theme_tags(response.text)
        if tags is None:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE,
                    detail=f"symbol {sym.id} not found on the ETF e添富 microsite",
                ),
            )
        return _result(
            payload={"tags": list(tags)},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=EtfThemeTagsAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


__all__ = ["EtfThemeTagsAdapter"]
