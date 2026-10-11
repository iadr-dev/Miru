"""Sector heatmap, sector constituents and stock heatmap."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_
from enum import StrEnum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from analytics.symbol_universe import is_scoreable_symbol
from ingest.jobs import MARKET_VALUE_JOB
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import MarketValueRow
from schemas.enums import DataClass, Market
from tools.base import BaseTool, ToolContext, parse_args
from tools.market._common import (
    closes_by_symbol,
    industry_by_symbol_for,
    latest_dataset_date,
    names_by_symbol_for,
    price_eod_dataset_for,
    real_calendar,
    two_day_window,
    unavailable_reason_for_window,
)


def _turnovers_by_symbol(store: ParquetStore, market: str, trade_date: date_) -> dict[str, float]:
    """Every real equity/ETF symbol's trading-value (`turnover`, NT$) on `trade_date` from the
    same price_eod partition `closes_by_symbol` reads (same warrant-exclusion reasoning — see
    that function's docstring) — `get_sector_heatmap`'s optional trading-value weighting input.
    `EODBar.turnover` is `None` on some rows (TWSE OpenAPI's own single-latest-snapshot shape
    never carries it, only FinMind's whole-market pull does; `us_price_eod` writes `None`
    unconditionally, `ingest/us_jobs.py`'s row builders never populate it) — a row with no
    turnover is simply absent from this map, never coerced to a fabricated `0.0` that would
    silently zero-weight it in the weighted average. For US this means `weighted` in
    `_sector_heatmap_rows` always resolves `False` (no member ever has a turnover) — an honest
    degrade to the simple per-stock average, not a crash or a fabricated weighting."""
    market_enum = Market(market)
    raw_bars = store.read_partition(price_eod_dataset_for(market), market, trade_date)
    turnovers: dict[str, float] = {}
    for row in raw_bars:
        symbol = row.get("symbol")
        turnover = row.get("turnover")
        if (
            isinstance(symbol, str)
            and is_scoreable_symbol(symbol, market_enum)
            and isinstance(turnover, (int, float))
        ):
            turnovers[symbol] = float(turnover)
    return turnovers


class SectorHeatmapRow(BaseModel):
    """One industry's aggregate tile — colored by `avg_pct_change`, sized by `stock_count` (or
    `total_turnover`, when trading-value weighting was available for this industry)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    industry: str
    avg_pct_change: float  # a whole-number percent (already `x100`), matching AdvanceDeclineBuckets
    stock_count: int
    advancing_count: int
    declining_count: int
    total_turnover: float | None = None  # sum of this industry's trading value on trade_date, when
    # every one of its members' `turnover` was available; `None` (never partially-summed) if any
    # member's `turnover` was missing, so a caller never mistakes a partial sum for the industry's
    # real total.
    weighted_by_turnover: bool  # whether avg_pct_change was trading-value-weighted (True) or a
    # simple per-stock average (False) — see class docstring for when each applies.
    total_volume: int  # sum of this industry's PRICE_EOD_JOB share volume on trade_date — the
    # 成交量 size_by option.
    total_market_value: float | None = None  # sum of this industry's shares-outstanding x close


# Heatmap lookback period -> trading-day offset for the "prior" side of the pct-change window,
# same convention `_MOMENTUM_PERIOD_TRADING_DAYS` uses for `get_price_momentum`. "immediate" is
# the tool's original single-prior-trading-day behavior; the others let a caller compare against
# a week/two-weeks/month/quarter/half-year/year ago, mirroring the reference app's 漲跌績效
# selector (即時/週/二週/月/季/半年/年).
_SECTOR_HEATMAP_PERIOD_TRADING_DAYS: dict[str, int] = {
    "immediate": 1,
    "week": 5,
    "two_weeks": 10,
    "month": 21,
    "quarter": 63,
    "half_year": 126,
    "year": 252,
}


class SectorHeatmapPeriod(StrEnum):
    IMMEDIATE = "immediate"
    WEEK = "week"
    TWO_WEEKS = "two_weeks"
    MONTH = "month"
    QUARTER = "quarter"
    HALF_YEAR = "half_year"
    YEAR = "year"


class SectorHeatmapSizeBy(StrEnum):
    TURNOVER = "turnover"
    MARKET_VALUE = "market_value"
    VOLUME = "volume"


class GetSectorHeatmapInput(BaseModel):
    """A market + an optional trading day + a lookback period + a size-by metric; omitted
    `trade_date` means "latest available" — same semantics as `GetAdvanceDeclineInput`/
    `GetIndexContributionInput`. `period`/`size_by` mirror the reference 熱力圖 app's own
    漲跌績效/排序方式 selectors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    trade_date: date_ | None = Field(
        default=None,
        description="Trading day to report the sector heatmap for (ISO date). Omitted or null "
        "means the latest available trading day.",
    )
    period: SectorHeatmapPeriod = Field(
        default=SectorHeatmapPeriod.IMMEDIATE,
        description="Lookback window for the pct-change color: 'immediate' (1 trading day), "
        "'week' (5), 'two_weeks' (10), 'month' (21), 'quarter' (63), 'half_year' (126), or "
        "'year' (252 trading days).",
    )
    size_by: SectorHeatmapSizeBy = Field(
        default=SectorHeatmapSizeBy.TURNOVER,
        description="Tile-size metric: 'turnover' (成交額), 'market_value' (市值), or 'volume' "
        "(成交量).",
    )


class GetSectorHeatmapOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    trade_date: date_ | None = None
    prior_trade_date: date_ | None = None
    period: SectorHeatmapPeriod = SectorHeatmapPeriod.IMMEDIATE
    size_by: SectorHeatmapSizeBy = SectorHeatmapSizeBy.TURNOVER
    data_available: bool
    unavailable_reason: str | None = None
    industries: tuple[SectorHeatmapRow, ...] = Field(default_factory=tuple)


@dataclass(frozen=True)
class _SectorHeatmapMetrics:
    """Every per-symbol map `_sector_heatmap_rows` joins against the roster — bundled into one
    object so that function takes one metrics arg instead of five (ruff PLR0913)."""

    today_closes: dict[str, float]
    prior_closes: dict[str, float]
    turnovers: dict[str, float]
    volumes: dict[str, int]
    market_values: dict[str, float]


def _sector_heatmap_rows(
    industry_by_symbol: dict[str, str], metrics: _SectorHeatmapMetrics
) -> list[SectorHeatmapRow]:
    """Join the roster's `industry_category` against each symbol's close-to-close pct change,
    group by industry, and aggregate — trading-value-weighted when every member of that industry
    has a `turnover` figure for `trade_date`, else a simple per-stock average (never a partial
    weighted average silently mixing weighted and unweighted members). `total_volume`/
    `total_market_value` are plain sums over the same member set, for the `size_by` options that
    don't need weighting."""
    by_industry: dict[str, list[tuple[str, float]]] = {}
    for symbol, industry in industry_by_symbol.items():
        close = metrics.today_closes.get(symbol)
        prior_close = metrics.prior_closes.get(symbol)
        if close is None or not prior_close:
            # missing/legitimately-zero prior close — skip this symbol, never divide by it.
            continue
        pct = (close - prior_close) / prior_close * 100
        by_industry.setdefault(industry, []).append((symbol, pct))

    rows: list[SectorHeatmapRow] = []
    for industry, members in by_industry.items():
        pct_changes = [pct for _, pct in members]
        advancing = sum(1 for pct in pct_changes if pct > 0)
        declining = sum(1 for pct in pct_changes if pct < 0)
        member_turnovers = [metrics.turnovers.get(symbol) for symbol, _ in members]
        weighted = (
            all(t is not None for t in member_turnovers)
            and sum(cast("list[float]", member_turnovers)) > 0
        )
        if weighted:
            typed_turnovers = cast("list[float]", member_turnovers)
            total_turnover = sum(typed_turnovers)
            avg_pct_change = (
                sum(pct * t for (_, pct), t in zip(members, typed_turnovers, strict=True))
                / total_turnover
            )
        else:
            total_turnover = None
            avg_pct_change = sum(pct_changes) / len(pct_changes)
        total_volume = sum(metrics.volumes.get(symbol, 0) for symbol, _ in members)
        member_market_values = [metrics.market_values.get(symbol) for symbol, _ in members]
        total_market_value = (
            sum(cast("list[float]", member_market_values))
            if all(v is not None for v in member_market_values)
            else None
        )
        rows.append(
            SectorHeatmapRow(
                industry=industry,
                avg_pct_change=avg_pct_change,
                stock_count=len(members),
                advancing_count=advancing,
                declining_count=declining,
                total_turnover=total_turnover,
                weighted_by_turnover=weighted,
                total_volume=total_volume,
                total_market_value=total_market_value,
            )
        )
    return rows


def _volumes_by_symbol(store: ParquetStore, market: str, trade_date: date_) -> dict[str, int]:
    """Every real equity/ETF symbol's share volume on `trade_date`, from the same price_eod
    partition `closes_by_symbol`/`_turnovers_by_symbol` read (same warrant-exclusion reasoning —
    see `closes_by_symbol`'s own docstring) — `get_sector_heatmap`'s 成交量 size_by option."""
    market_enum = Market(market)
    raw_bars = store.read_partition(price_eod_dataset_for(market), market, trade_date)
    volumes: dict[str, int] = {}
    for row in raw_bars:
        symbol = row.get("symbol")
        volume = row.get("volume")
        if (
            isinstance(symbol, str)
            and is_scoreable_symbol(symbol, market_enum)
            and isinstance(volume, (int, float))
        ):
            volumes[symbol] = int(volume)
    return volumes


def _market_values_by_symbol(
    store: ParquetStore, market: str, trade_date: date_
) -> dict[str, float]:
    """Every real equity/ETF symbol's market capitalization on `trade_date`, read directly from
    `MARKET_VALUE_JOB`'s own already-ingested partition (never recomputed from shares-outstanding
    x close the way `shares_outstanding_by_symbol` does it the other direction) —
    `get_sector_heatmap`'s 市值 size_by option. No US equivalent dataset is ingested —
    `has_partition` below is always `False` for `market="US"`, so this honestly degrades to `{}`
    (the 市值 size_by option simply has no totals for US) rather than needing its own branch."""
    if not store.has_partition(MARKET_VALUE_JOB.dataset, market, trade_date):
        return {}
    raw_values = store.read_partition(MARKET_VALUE_JOB.dataset, market, trade_date)
    market_values: dict[str, float] = {}
    market_enum = Market(market)
    for row in rows_from_store(MarketValueRow, raw_values):
        if is_scoreable_symbol(row.symbol, market_enum):
            market_values[row.symbol] = float(row.market_value)
    return market_values


class GetSectorHeatmapTool(BaseTool):
    """`get_sector_heatmap` — 產業別漲跌熱力圖: joins the already-ingested `STOCK_INFO_JOB` roster
    (each symbol's `industry_category`) against the same two-day `PRICE_EOD_JOB` close window
    `GetAdvanceDeclineTool`/`GetIndexContributionTool` resolve (`two_day_window`), grouping each
    symbol's close-to-close percentage change by industry — never a per-symbol live fan-out, for
    the same whole-market-aggregate reasoning those two tools document. `avg_pct_change` is
    trading-value-weighted (via `PRICE_EOD_JOB`'s own `turnover` field) when every member of an
    industry has one for `trade_date`, else a simple per-stock average — see `SectorHeatmapRow`'s
    `weighted_by_turnover` flag, never silently mixed.

    Public tier: both `Capability.STOCK_INFO` and `Capability.PRICE_EOD` are `DataClass.PUBLIC`
    in their own `_DatasetConfig` (`sources/finmind/technical.py`) — the same tier reasoning
    `GetAdvanceDeclineTool`'s own docstring gives for reading a PUBLIC-tier ingested dataset back
    from the derived store."""

    name = "get_sector_heatmap"
    description = (
        "Get the whole-market industry/sector heatmap for one trading day (defaults to the "
        "latest available day): each industry's average percentage change over a chosen "
        "lookback period (trading-value-weighted when available), constituent stock count, "
        "and total turnover/volume/market-value (for the size_by tile-sizing options)."
    )
    input_model: type[BaseModel] = GetSectorHeatmapInput
    output_model: type[BaseModel] = GetSectorHeatmapOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "sector_heatmap"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetSectorHeatmapInput, args)
        market = typed.market.value
        price_eod_dataset = price_eod_dataset_for(market)
        calendar = real_calendar(self._store, market)
        trade_date = typed.trade_date or latest_dataset_date(self._store, price_eod_dataset, market)
        if trade_date is None or not self._store.has_partition(
            price_eod_dataset, market, trade_date
        ):
            return GetSectorHeatmapOutput(
                market=market,
                trade_date=typed.trade_date,
                period=typed.period,
                size_by=typed.size_by,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window(
                    "price_eod", market, typed.trade_date
                ),
            )
        prior_date = calendar.shift_business_days(
            trade_date, -_SECTOR_HEATMAP_PERIOD_TRADING_DAYS[typed.period.value]
        )
        if not self._store.has_partition(price_eod_dataset, market, prior_date):
            return GetSectorHeatmapOutput(
                market=market,
                trade_date=trade_date,
                period=typed.period,
                size_by=typed.size_by,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window("price_eod", market, trade_date),
            )
        industry_by_symbol = industry_by_symbol_for(self._store, market)
        if not industry_by_symbol:
            return GetSectorHeatmapOutput(
                market=market,
                trade_date=trade_date,
                prior_trade_date=prior_date,
                period=typed.period,
                size_by=typed.size_by,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window("stock_info", market, trade_date),
            )

        today_closes = closes_by_symbol(self._store, market, trade_date)
        prior_closes = closes_by_symbol(self._store, market, prior_date)
        turnovers = _turnovers_by_symbol(self._store, market, trade_date)
        volumes = _volumes_by_symbol(self._store, market, trade_date)
        market_values = _market_values_by_symbol(self._store, market, trade_date)

        rows = _sector_heatmap_rows(
            industry_by_symbol,
            _SectorHeatmapMetrics(
                today_closes=today_closes,
                prior_closes=prior_closes,
                turnovers=turnovers,
                volumes=volumes,
                market_values=market_values,
            ),
        )
        # Every industry present, ranked by move (not top-N truncated) — a heatmap tile list is
        # meant to show the whole market's industry breadth, unlike `get_index_contribution`'s
        # deliberately-capped ranking of individual symbols.
        ranked = tuple(sorted(rows, key=lambda r: r.avg_pct_change, reverse=True))

        return GetSectorHeatmapOutput(
            market=market,
            trade_date=trade_date,
            prior_trade_date=prior_date,
            period=typed.period,
            size_by=typed.size_by,
            data_available=True,
            industries=ranked,
        )


# Ranked output truncated to this many symbols — never a full-market symbol dump. A single
# `industry`'s own roster (the whole point of this tool) can still run past a few hundred names
# for the biggest categories (電子工業 has 260+ members today), so a cap is needed even scoped to
# one industry, mirroring `GetIndexContributionTool`'s/`GetPriceMomentumTool`'s own top-N caps.
_SECTOR_CONSTITUENTS_TOP_N = 100


class SectorConstituentRow(BaseModel):
    """One stock within a `get_sector_heatmap` industry tile — the per-symbol breakdown a
    treemap drill-down needs, which `SectorHeatmapRow` itself deliberately only aggregates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    stock_name: str
    industry: str
    pct_change: float  # close-to-close, a whole-number percent (matches SectorHeatmapRow's own)
    close: float
    volume: int
    turnover: float | None = None  # None when PRICE_EOD_JOB's own row carried no turnover figure


class GetSectorConstituentsInput(BaseModel):
    """A market + an optional industry filter (one of `SectorHeatmapRow.industry`'s own values)
    + an optional trading day — same single-`trade_date` semantics as `GetSectorHeatmapInput`.
    `industry` is a plain scalar filter, never an array, so this can't be turned into a
    full-market scan by passing every industry name at once."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    industry: str | None = Field(
        default=None,
        description="Restrict to one industry (exact match against get_sector_heatmap's own "
        "`industry` values). Omitted or null returns every industry's constituents, ranked "
        "together and capped at the same top-N.",
    )
    trade_date: date_ | None = Field(
        default=None,
        description="Trading day to report constituents for (ISO date). Omitted or null means "
        "the latest available trading day.",
    )


class GetSectorConstituentsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    industry: str | None = None
    trade_date: date_ | None = None
    prior_trade_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    rows: tuple[SectorConstituentRow, ...] = Field(default_factory=tuple)


class GetSectorConstituentsTool(BaseTool):
    """`get_sector_constituents` — the per-stock drill-down for one `get_sector_heatmap` tile:
    every member symbol's own close-to-close percentage change, close, volume, and turnover for
    a chosen industry (or, if `industry` is omitted, every symbol ranked together). Reads the
    exact same two already-ingested partitions `GetSectorHeatmapTool` joins
    (`STOCK_INFO_JOB`'s roster + `PRICE_EOD_JOB`'s closes/volumes/turnovers) — never a per-symbol
    live fan-out, same whole-market-aggregate reasoning as that tool's own docstring. Ranked by
    `pct_change` descending and capped at `_SECTOR_CONSTITUENTS_TOP_N` (governance §9c: bounded
    output, not an unbounded whole-market dump, even though the *input* is a single scalar
    industry filter rather than a symbol array).

    Public tier: same `Capability.STOCK_INFO`/`Capability.PRICE_EOD` PUBLIC-tier reasoning
    `GetSectorHeatmapTool` documents."""

    name = "get_sector_constituents"
    description = (
        "Get the per-stock breakdown (close-to-close % change, close, volume, turnover) for "
        "one sector-heatmap industry tile, or every symbol ranked together if no industry is "
        "given, for one trading day (defaults to the latest available day)."
    )
    input_model: type[BaseModel] = GetSectorConstituentsInput
    output_model: type[BaseModel] = GetSectorConstituentsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "sector_constituents"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetSectorConstituentsInput, args)
        market = typed.market.value
        calendar = real_calendar(self._store, market)
        window = two_day_window(
            self._store, price_eod_dataset_for(market), market, calendar, typed.trade_date
        )
        if window is None:
            return GetSectorConstituentsOutput(
                market=market,
                industry=typed.industry,
                trade_date=typed.trade_date,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window(
                    "price_eod", market, typed.trade_date
                ),
            )
        trade_date, prior_date = window
        names_by_symbol = names_by_symbol_for(self._store, market)
        industry_by_symbol = industry_by_symbol_for(self._store, market)
        if not names_by_symbol or not industry_by_symbol:
            return GetSectorConstituentsOutput(
                market=market,
                industry=typed.industry,
                trade_date=trade_date,
                prior_trade_date=prior_date,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window("stock_info", market, trade_date),
            )

        today_closes = closes_by_symbol(self._store, market, trade_date)
        prior_closes = closes_by_symbol(self._store, market, prior_date)
        turnovers = _turnovers_by_symbol(self._store, market, trade_date)
        volumes_by_symbol = _volumes_by_symbol(self._store, market, trade_date)

        rows: list[SectorConstituentRow] = []
        for symbol, industry in industry_by_symbol.items():
            if typed.industry is not None and industry != typed.industry:
                continue
            close = today_closes.get(symbol)
            prior_close = prior_closes.get(symbol)
            name = names_by_symbol.get(symbol)
            volume = volumes_by_symbol.get(symbol)
            if close is None or not prior_close or name is None or volume is None:
                continue
            pct = (close - prior_close) / prior_close * 100
            rows.append(
                SectorConstituentRow(
                    symbol=symbol,
                    stock_name=name,
                    industry=industry,
                    pct_change=pct,
                    close=close,
                    volume=volume,
                    turnover=turnovers.get(symbol),
                )
            )

        ranked = tuple(
            sorted(rows, key=lambda r: r.pct_change, reverse=True)[:_SECTOR_CONSTITUENTS_TOP_N]
        )

        return GetSectorConstituentsOutput(
            market=market,
            industry=typed.industry,
            trade_date=trade_date,
            prior_trade_date=prior_date,
            data_available=True,
            rows=ranked,
        )


# Ranked output truncated to this many symbols — a flat whole-market stock treemap (unlike
# `get_sector_heatmap`'s per-industry aggregate tiles) needs a real cap: TWSE+TPEx list 1,700+
# symbols today, and a treemap with that many tiles is unreadable regardless of screen size.
# Mirrors `_SECTOR_CONSTITUENTS_TOP_N`'s own reasoning, just for the whole market rather than one
# industry's roster.
_STOCK_HEATMAP_TOP_N = 150


class StockHeatmapRow(BaseModel):
    """One stock's own tile — colored by its close-to-close `pct_change` over `period`, sized by
    whichever `size_by` metric `get_stock_heatmap` was asked for. The per-symbol counterpart to
    `SectorHeatmapRow`'s per-industry aggregate; `industry` rides along so the frontend can group/
    label tiles by sector even though this tool's own ranking is flat across the whole market."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    stock_name: str
    industry: str
    pct_change: float  # a whole-number percent (already `x100`), matching SectorHeatmapRow's own
    close: float
    turnover: float | None = None  # None when PRICE_EOD_JOB's own row carried no turnover figure
    volume: int
    market_value: float | None = None  # None when MARKET_VALUE_JOB had no row for this symbol


class GetStockHeatmapInput(BaseModel):
    """A market + an optional trading day + a lookback period + a size-by metric — same shape as
    `GetSectorHeatmapInput`, just for the flat per-stock ranking rather than industry aggregates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    trade_date: date_ | None = Field(
        default=None,
        description="Trading day to report the stock heatmap for (ISO date). Omitted or null "
        "means the latest available trading day.",
    )
    period: SectorHeatmapPeriod = Field(
        default=SectorHeatmapPeriod.IMMEDIATE,
        description="Lookback window for the pct-change color: 'immediate' (1 trading day), "
        "'week' (5), 'two_weeks' (10), 'month' (21), 'quarter' (63), 'half_year' (126), or "
        "'year' (252 trading days).",
    )
    size_by: SectorHeatmapSizeBy = Field(
        default=SectorHeatmapSizeBy.TURNOVER,
        description="Tile-size metric: 'turnover' (成交額), 'market_value' (市值), or 'volume' "
        "(成交量).",
    )


class GetStockHeatmapOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    trade_date: date_ | None = None
    prior_trade_date: date_ | None = None
    period: SectorHeatmapPeriod = SectorHeatmapPeriod.IMMEDIATE
    size_by: SectorHeatmapSizeBy = SectorHeatmapSizeBy.TURNOVER
    data_available: bool
    unavailable_reason: str | None = None
    stocks: tuple[StockHeatmapRow, ...] = Field(default_factory=tuple)


def _stock_heatmap_size_value(
    symbol: str,
    size_by: SectorHeatmapSizeBy,
    turnover: float | None,
    volume: int,
    market_value: float | None,
) -> float:
    """Picks the ranking value for `size_by` — the flat top-N cut below is ranked by this same
    metric a tile will be SIZED by, so "top 150 by turnover" also means "the 150 biggest tiles",
    never a mismatched rank-by-X-but-size-by-Y. Falls back to `volume` when the requested metric
    is unavailable for this symbol, same never-silently-zero reasoning `tileSizeValue` (frontend
    treemap) and `SectorHeatmapRow.total_turnover`'s own docstring both use."""
    del symbol  # kept for signature symmetry with the per-symbol maps this reads from
    if size_by == SectorHeatmapSizeBy.MARKET_VALUE:
        return market_value if market_value is not None else float(volume)
    if size_by == SectorHeatmapSizeBy.VOLUME:
        return float(volume)
    return turnover if turnover is not None else float(volume)


class GetStockHeatmapTool(BaseTool):
    """`get_stock_heatmap` — 股票熱力圖: the flat, per-stock counterpart to `get_sector_heatmap`'s
    per-industry aggregate tiles (finviz's own individual-ticker map, not its industry-region
    view). Joins the same already-ingested `STOCK_INFO_JOB` roster + two-day `PRICE_EOD_JOB`
    close window `GetSectorHeatmapTool` reads — never a per-symbol live fan-out, same
    whole-market-aggregate reasoning that tool's own docstring gives. Ranked (and capped at
    `_STOCK_HEATMAP_TOP_N`) by the SAME metric `size_by` sizes tiles with, so the top-N cut never
    silently disagrees with tile size.

    Public tier: same `Capability.STOCK_INFO`/`Capability.PRICE_EOD` PUBLIC-tier reasoning
    `GetSectorHeatmapTool`'s own docstring gives."""

    name = "get_stock_heatmap"
    description = (
        "Get the whole-market per-stock heatmap for one trading day (defaults to the latest "
        "available day): each of the top-ranked stocks' own percentage change over a chosen "
        "lookback period, close, turnover/volume/market-value (for the size_by tile-sizing "
        "options), and industry — the flat per-ticker counterpart to get_sector_heatmap's "
        "per-industry aggregate tiles."
    )
    input_model: type[BaseModel] = GetStockHeatmapInput
    output_model: type[BaseModel] = GetStockHeatmapOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "stock_heatmap"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetStockHeatmapInput, args)
        market = typed.market.value
        price_eod_dataset = price_eod_dataset_for(market)
        calendar = real_calendar(self._store, market)
        trade_date = typed.trade_date or latest_dataset_date(self._store, price_eod_dataset, market)
        if trade_date is None or not self._store.has_partition(
            price_eod_dataset, market, trade_date
        ):
            return GetStockHeatmapOutput(
                market=market,
                trade_date=typed.trade_date,
                period=typed.period,
                size_by=typed.size_by,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window(
                    "price_eod", market, typed.trade_date
                ),
            )
        prior_date = calendar.shift_business_days(
            trade_date, -_SECTOR_HEATMAP_PERIOD_TRADING_DAYS[typed.period.value]
        )
        if not self._store.has_partition(price_eod_dataset, market, prior_date):
            return GetStockHeatmapOutput(
                market=market,
                trade_date=trade_date,
                period=typed.period,
                size_by=typed.size_by,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window("price_eod", market, trade_date),
            )
        names_by_symbol = names_by_symbol_for(self._store, market)
        industry_by_symbol = industry_by_symbol_for(self._store, market)
        if not names_by_symbol or not industry_by_symbol:
            return GetStockHeatmapOutput(
                market=market,
                trade_date=trade_date,
                prior_trade_date=prior_date,
                period=typed.period,
                size_by=typed.size_by,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window("stock_info", market, trade_date),
            )

        names_and_industry = {
            symbol: (names_by_symbol[symbol], industry)
            for symbol, industry in industry_by_symbol.items()
            if symbol in names_by_symbol
        }
        today_closes = closes_by_symbol(self._store, market, trade_date)
        prior_closes = closes_by_symbol(self._store, market, prior_date)
        turnovers = _turnovers_by_symbol(self._store, market, trade_date)
        volumes = _volumes_by_symbol(self._store, market, trade_date)
        market_values = _market_values_by_symbol(self._store, market, trade_date)

        rows: list[StockHeatmapRow] = []
        for symbol, (name, industry) in names_and_industry.items():
            close = today_closes.get(symbol)
            prior_close = prior_closes.get(symbol)
            volume = volumes.get(symbol)
            if close is None or not prior_close or volume is None:
                continue
            pct = (close - prior_close) / prior_close * 100
            rows.append(
                StockHeatmapRow(
                    symbol=symbol,
                    stock_name=name,
                    industry=industry,
                    pct_change=pct,
                    close=close,
                    turnover=turnovers.get(symbol),
                    volume=volume,
                    market_value=market_values.get(symbol),
                )
            )

        ranked = tuple(
            sorted(
                rows,
                key=lambda r: _stock_heatmap_size_value(
                    r.symbol, typed.size_by, r.turnover, r.volume, r.market_value
                ),
                reverse=True,
            )[:_STOCK_HEATMAP_TOP_N]
        )

        return GetStockHeatmapOutput(
            market=market,
            trade_date=trade_date,
            prior_trade_date=prior_date,
            period=typed.period,
            size_by=typed.size_by,
            data_available=True,
            stocks=ranked,
        )
