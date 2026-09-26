"""`get_us_market_rankings` — the 排行榜 multi-tab US-market ranking card's backend: 熱門美股
(Yahoo's own `most_actives` named screen), 市值/營收/ROE/成交量 (a custom `ScreenerQuery` sorted
by the matching Yahoo screener field). Distinct from `market_tools.py`'s `get_price_momentum`
(a pure close-to-close %-change ranking computed from OUR OWN ingested `us_price_eod` partitions)
— every ranking here is a live, bounded Yahoo screener call (`dataaccess/screener.py`), never a
whole-market scan of our own store: Yahoo's screener endpoint itself does the sorting/filtering
server-side, and this tool only ever requests a small top-N page (`RunEquityScreenerUsTool`'s own
same governance posture — see that tool's docstring in `analyst_tools.py`).

**US-only by construction, no `market` param needed.** Every query condition includes
`region=us` (mirroring `ingest/us_universe.py::_band_query`'s own condition) — there is no
TW/GB/JP/EU equivalent screener path in this codebase today, so this tool simply has no market
axis to branch on, unlike `market_tools.py`'s tools which take a real `market: Market` field.

**The one real bug this module guards against, confirmed live against the real yfinance
package**: Yahoo's `totalrevenues.lasttwelvemonths`/`returnonequity.lasttwelvemonths` screener
sort fields report each company's figures in ITS OWN `financialCurrency` (e.g. a Korean,
Indonesian, or Japanese filer reports in KRW/IDR/JPY), not normalized to USD — a naive top-N by
either field surfaces non-USD-currency reporters ahead of real USD-revenue giants, since their
raw nominal-currency figures (e.g. IDR, a ~15,000:1 unit) vastly outsize USD figures. Confirmed
live: at `REVENUE`'s original $500M market-cap floor, **zero** USD reporters appear even within
Yahoo's full 250-row screener cap — no fetch-more-and-filter multiplier can fix this, since the
non-USD noise floor is that deep. The real fix, also confirmed live: raising `REVENUE`'s market-
cap floor to $50B first shrinks the candidate pool enough that real USD revenue giants (Amazon,
Walmart, Apple, UnitedHealth, ...) surface within the 250-row cap, then the same currency filter
applies on top. `ROE` needed no floor change — confirmed live its non-USD share of the top-N is
small enough that the smaller `_CURRENCY_FILTER_FETCH_MULTIPLIER` cushion already fills the page.
Yahoo's screener query language has no currency-equality condition to push this filter server-side
(confirmed: not present in `EquityQuery.valid_fields`), so it is applied client-side here."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.screener import run_equity_screener as _run_equity_screener
from dataaccess.screener import run_predefined_equity_screener as _run_predefined_equity_screener
from schemas.enums import DataClass
from schemas.screener import (
    ScreenerAssetKind,
    ScreenerCondition,
    ScreenerHit,
    ScreenerOperator,
    ScreenerQuery,
)
from tools.base import BaseTool, Tool, ToolContext, parse_args

# Extra rows fetched past `top_n` for the currency-filtered kinds (REVENUE/ROE) — a cushion so a
# handful of non-USD reporters among the top matches still leaves enough USD rows to fill the
# requested page. Not a hard guarantee (an unusually non-USD-heavy sort could still under-fill),
# but generous enough in practice: confirmed live, USD reporters dominate the US-region top-N for
# every kind tested this session.
_CURRENCY_FILTER_FETCH_MULTIPLIER = 3
_MAX_TOP_N = 50
# Yahoo's own hard cap for a single custom screen (`ScreenerQuery.size`'s own `le=250`, same
# limit `ingest/us_universe.py::_BAND_SIZE` documents) — `REVENUE`'s own fetch size, since a
# smaller multiplier-based cushion under-fills (confirmed live: only 88/250 rows are USD-
# denominated even at the raised market-cap floor).
_SCREENER_HARD_CAP = 250

# Market-cap floor for most kinds (mirrors `ingest/us_universe.py`'s own smallest tracked band)
# — excludes micro-caps/illiquid names Yahoo's screener would otherwise surface, consistent with
# what this codebase already treats as its "tracked US universe" floor.
_MARKET_CAP_FLOOR = 500_000_000

# `REVENUE`'s own much higher floor — see the module docstring's live finding: at the default
# floor, zero USD reporters appear even within Yahoo's full 250-row screener cap, so no
# fetch-more-and-filter multiplier can recover a USD-only top-N. Only a candidate pool this
# small (mega-caps only) shrinks the non-USD noise enough for real USD revenue giants to surface.
_REVENUE_MARKET_CAP_FLOOR = 50_000_000_000

_REGION_US = ScreenerCondition(field="region", op=ScreenerOperator.EQ, value="us")
_MARKET_CAP_FLOOR_CONDITION = ScreenerCondition(
    field="intradaymarketcap", op=ScreenerOperator.GT, value=_MARKET_CAP_FLOOR
)
_REVENUE_MARKET_CAP_FLOOR_CONDITION = ScreenerCondition(
    field="intradaymarketcap", op=ScreenerOperator.GT, value=_REVENUE_MARKET_CAP_FLOOR
)


class RankingKind(StrEnum):
    """One tab of the 排行榜 card. `TRENDING` is Yahoo's own named screen (no sort field of our
    own choosing); every other kind is a custom query sorted by the matching Yahoo screener
    field — see each's own `_SORT_FIELD_BY_KIND`/`_CURRENCY_FILTERED_KINDS` entry below."""

    TRENDING = "trending"  # 熱門美股
    MARKET_CAP = "market_cap"  # 市值
    REVENUE = "revenue"  # 營收
    ROE = "roe"  # ROE
    VOLUME = "volume"  # 成交量


_SORT_FIELD_BY_KIND: dict[RankingKind, str] = {
    RankingKind.MARKET_CAP: "intradaymarketcap",
    RankingKind.REVENUE: "totalrevenues.lasttwelvemonths",
    RankingKind.ROE: "returnonequity.lasttwelvemonths",
    RankingKind.VOLUME: "dayvolume",
}

# See the module docstring's currency-normalization finding — only these two kinds' sort fields
# are reported in a non-USD currency for some matches.
_CURRENCY_FILTERED_KINDS = frozenset({RankingKind.REVENUE, RankingKind.ROE})


class GetUsMarketRankingsInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RankingKind
    top_n: int = Field(default=10, ge=1, le=_MAX_TOP_N)


class MarketRankingRow(BaseModel):
    """One ranked row — the exact column set the 排行榜 reference design shows (名稱/代號, 最新價,
    漲跌, 漲跌幅, 成交量). Every field is read straight from `ScreenerHit.matched_fields`
    (Yahoo's own raw quote fields, already a passthrough dict — see `sources/yfinance/
    screener.py::_quote_to_hit`), never a separate fetch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    name: str | None = None
    price: float | None = None
    change: float | None = None
    change_pct: float | None = None
    volume: float | None = None


class GetUsMarketRankingsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RankingKind
    rows: tuple[MarketRankingRow, ...] = Field(default_factory=tuple)
    detail: str = Field(min_length=1)


def _row_from_hit(hit: ScreenerHit) -> MarketRankingRow:
    fields = hit.matched_fields
    return MarketRankingRow(
        symbol=hit.symbol,
        name=hit.name,
        price=_float_field(fields, "regularMarketPrice"),
        change=_float_field(fields, "regularMarketChange"),
        change_pct=_float_field(fields, "regularMarketChangePercent"),
        volume=_float_field(fields, "regularMarketVolume"),
    )


def _float_field(fields: dict[str, float | str | bool | None], key: str) -> float | None:
    value = fields.get(key)
    return value if isinstance(value, float) else None


def _is_usd_reporter(hit: ScreenerHit) -> bool:
    return hit.matched_fields.get("financialCurrency") == "USD"


class GetUsMarketRankingsTool(BaseTool):
    """`get_us_market_rankings` — see module docstring."""

    name = "get_us_market_rankings"
    description = (
        "Get a top-N ranking of US equities for one of: trending (Yahoo's most-active screen), "
        "market cap, revenue, ROE, or volume. Live Yahoo screener query, bounded to top_n rows."
    )
    input_model: type[BaseModel] = GetUsMarketRankingsInput
    output_model: type[BaseModel] = GetUsMarketRankingsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "us_market_ranking"

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — the screener carve-out never routes through DataAccessRouter
        typed = parse_args(GetUsMarketRankingsInput, args)
        if typed.kind is RankingKind.TRENDING:
            result = await _run_predefined_equity_screener("most_actives", typed.top_n)
            rows = tuple(_row_from_hit(hit) for hit in result.hits[: typed.top_n])
            return GetUsMarketRankingsOutput(kind=typed.kind, rows=rows, detail=result.detail)

        sort_field = _SORT_FIELD_BY_KIND[typed.kind]
        market_cap_condition = _MARKET_CAP_FLOOR_CONDITION
        fetch_size = typed.top_n
        if typed.kind is RankingKind.REVENUE:
            # See the module docstring: even with the raised $50B floor, USD reporters are still
            # a minority of the sorted pool (88/250 confirmed live) — fetch the full Yahoo cap
            # rather than a smaller multiplier-based cushion, or the USD filter can under-fill.
            market_cap_condition = _REVENUE_MARKET_CAP_FLOOR_CONDITION
            fetch_size = _SCREENER_HARD_CAP
        elif typed.kind in _CURRENCY_FILTERED_KINDS:
            fetch_size = min(_MAX_TOP_N, typed.top_n * _CURRENCY_FILTER_FETCH_MULTIPLIER)
        query = ScreenerQuery(
            asset_kind=ScreenerAssetKind.EQUITY,
            conditions=(_REGION_US, market_cap_condition),
            size=fetch_size,
            sort_field=sort_field,
            sort_ascending=False,
        )
        result = await _run_equity_screener(query)
        hits = result.hits
        if typed.kind in _CURRENCY_FILTERED_KINDS:
            hits = tuple(hit for hit in hits if _is_usd_reporter(hit))
        rows = tuple(_row_from_hit(hit) for hit in hits[: typed.top_n])
        return GetUsMarketRankingsOutput(kind=typed.kind, rows=rows, detail=result.detail)


def build_registration_table() -> list[tuple[Tool, bool]]:
    return [(GetUsMarketRankingsTool(), False)]


__all__ = [
    "GetUsMarketRankingsInput",
    "GetUsMarketRankingsOutput",
    "GetUsMarketRankingsTool",
    "MarketRankingRow",
    "RankingKind",
    "build_registration_table",
]
