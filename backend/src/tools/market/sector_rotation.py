"""Sector rotation and the relative-rotation (RRG) graph."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date as date_
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from ingest.jobs import INSTITUTIONAL_FLOW_JOB, STOCK_INFO_JOB
from ingest.store import ParquetStore
from schemas.capabilities import EODBar, InstitutionalFlowRow, StockInfoRow
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from tools._rows import fetch_rows
from tools.base import BaseTool, ToolContext, parse_args
from tools.market._common import (
    ingested_dates,
    latest_dataset_date,
    rows_from_store,
    unavailable_reason_for_window,
)

# How many recent trading days `get_sector_rotation` plots — a rank-bump chart needs enough
# points to show real crossings but stays readable on one screen; 10 matches this dashboard's
# other short-window reads (`_ROLLING_WINDOW_TRADING_DAYS`-style constants elsewhere in this
# codebase) rather than RRG's usual weekly-over-months span, which this daily-flow dataset has
# no natural analog for (see the design research this tool followed: a rank-bump chart, not an
# RRG, is the right fit for "N days of net institutional flow ranked by industry").
_SECTOR_ROTATION_WINDOW_TRADING_DAYS = 10


class SectorRotationDayRow(BaseModel):
    """One industry's rank + net institutional flow for one trading day within the rotation
    window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trade_date: date_
    industry: str
    net_flow: float  # sum(buy - sell) shares across every investor category, this industry
    rank: int  # 1 = most net inflow that day; ties broken by industry name for a stable order


class GetSectorRotationInput(BaseModel):
    """A market + an optional end date (defaults to the latest day `INSTITUTIONAL_FLOW_JOB` has
    actually ingested) — the window always runs backward from there for
    `_SECTOR_ROTATION_WINDOW_TRADING_DAYS` real trading days, never a caller-chosen length (this
    is a fixed short rotation window, not an open-ended range query)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    end_date: date_ | None = Field(
        default=None,
        description="Last trading day of the rotation window (ISO date). Omitted or null "
        "means the latest available trading day.",
    )


class GetSectorRotationOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    trade_dates: tuple[date_, ...] = Field(default_factory=tuple)
    data_available: bool
    unavailable_reason: str | None = None
    rows: tuple[SectorRotationDayRow, ...] = Field(default_factory=tuple)


def _net_flow_by_symbol_for_date(
    store: ParquetStore, market: str, trade_date: date_
) -> dict[str, int]:
    """Sum of `buy - sell` across every investor category, per symbol, for `trade_date` — read
    back from `INSTITUTIONAL_FLOW_JOB`'s already-ingested partition, never fetched live. Same
    shape as `disposition_board_tools._institutional_net_by_symbol`, duplicated rather than
    imported: no tool module in this package imports another (each stays self-contained over the
    store/router it's handed)."""
    if not store.has_partition(INSTITUTIONAL_FLOW_JOB.dataset, market, trade_date):
        return {}
    raw_rows = store.read_partition(INSTITUTIONAL_FLOW_JOB.dataset, market, trade_date)
    net: dict[str, int] = {}
    for row in rows_from_store(InstitutionalFlowRow, raw_rows):
        net[row.symbol] = net.get(row.symbol, 0) + (row.buy - row.sell)
    return net


class GetSectorRotationTool(BaseTool):
    """`get_sector_rotation` — daily industry ranking by net institutional flow (`buy - sell`
    summed across every investor category) over the last `_SECTOR_ROTATION_WINDOW_TRADING_DAYS`
    trading days, the data a rank-bump chart plots (one line per industry, Y = daily rank).
    Joins the already-ingested `STOCK_INFO_JOB` roster against `INSTITUTIONAL_FLOW_JOB`'s
    per-symbol buy/sell rows, summed per industry per day — never a per-symbol live fan-out,
    same whole-market-aggregate reasoning `GetSectorHeatmapTool`'s own docstring gives.

    Deliberately NOT an RRG (Relative Rotation Graph): RRG needs a benchmark index to compute a
    relative-strength ratio and is tuned for weekly-over-months smoothing; a daily net-flow
    figure has no natural "ratio against a benchmark," and 10 days of daily data has neither the
    volume nor the right shape for RRG's own smoothing math. A rank-bump chart needs only an
    orderable value per entity per day, which net flow already is.

    Public tier: both `Capability.STOCK_INFO` and `Capability.INSTITUTIONAL_FLOW` are
    `DataClass.PUBLIC` in their own `_DatasetConfig` — same tier reasoning
    `GetSectorHeatmapTool`'s docstring gives for reading a PUBLIC-tier ingested dataset back from
    the derived store."""

    name = "get_sector_rotation"
    description = (
        "Get each industry's daily rank by net institutional flow (buy - sell) over the last "
        f"{_SECTOR_ROTATION_WINDOW_TRADING_DAYS} trading days (defaults to ending at the "
        "latest available day) — the data behind a sector-rotation rank-bump chart."
    )
    input_model: type[BaseModel] = GetSectorRotationInput
    output_model: type[BaseModel] = GetSectorRotationOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "sector_rotation"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetSectorRotationInput, args)
        market = typed.market.value

        flow_dates = ingested_dates(
            self._store, INSTITUTIONAL_FLOW_JOB.dataset, market, on_or_before=typed.end_date
        )
        if not flow_dates:
            return GetSectorRotationOutput(
                market=market,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window(
                    "institutional_flow", market, typed.end_date
                ),
            )
        window_dates = tuple(sorted(flow_dates)[-_SECTOR_ROTATION_WINDOW_TRADING_DAYS:])

        roster_date = latest_dataset_date(self._store, STOCK_INFO_JOB.dataset, market)
        if roster_date is None:
            return GetSectorRotationOutput(
                market=market,
                trade_dates=window_dates,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window(
                    "stock_info", market, typed.end_date
                ),
            )
        raw_roster = self._store.read_partition(STOCK_INFO_JOB.dataset, market, roster_date)
        roster = rows_from_store(StockInfoRow, raw_roster)
        industry_by_symbol = {
            row.symbol: row.industry_category for row in roster if row.industry_category
        }

        rows: list[SectorRotationDayRow] = []
        for trade_date in window_dates:
            net_by_symbol = _net_flow_by_symbol_for_date(self._store, market, trade_date)
            net_by_industry: dict[str, float] = {}
            for symbol, net in net_by_symbol.items():
                industry = industry_by_symbol.get(symbol)
                if industry is None:
                    continue
                net_by_industry[industry] = net_by_industry.get(industry, 0.0) + net
            ranked_industries = sorted(
                net_by_industry.items(), key=lambda item: (-item[1], item[0])
            )
            for rank, (industry, net_flow) in enumerate(ranked_industries, start=1):
                rows.append(
                    SectorRotationDayRow(
                        trade_date=trade_date, industry=industry, net_flow=net_flow, rank=rank
                    )
                )

        return GetSectorRotationOutput(
            market=market,
            trade_dates=window_dates,
            data_available=True,
            rows=tuple(rows),
        )


# TWSE's own named industry sub-indices (`TaiwanStockInfo` rows with `industry_category="Index"`)
# — each is a real, independently-published FinMind `TaiwanStockPrice` series (`data_id` below),
# not a stock. This is a deliberately small starter set covering the sectors most retail
# commentary already names day-to-day; TWSE publishes ~30 of these in total (confirmed live
# against `TaiwanStockInfo`) and adding another is a one-line append here, never a new dataset
# integration. `(data_id, display_name)` — `display_name` is the id's own FinMind `stock_name`,
# never an invented label.
SECTOR_RRG_INDEX_IDS: tuple[tuple[str, str], ...] = (
    ("Semiconductor", "半導體類指數"),
    ("Electronic", "電子類指數"),
    ("ElectronicPartsComponents", "電子零組件類指數"),
    ("ComputerPeripheralEquipment", "電腦及週邊設備類指數"),
    ("CommunicationsInternet", "通信網路類指數"),
    ("FinancialInsurance", "金融保險類指數"),
    ("ShippingTransportation", "航運業類指數"),
    ("BiotechnologyMedicalCare", "生技醫療類指數"),
    ("BuildingMaterialConstruction", "建材營造類指數"),
    ("IronSteel", "鋼鐵類指數"),
)


_SECTOR_RRG_BENCHMARK_ID = "TAIEX"


_SECTOR_RRG_BENCHMARK_NAME = "加權指數"


# JdK RS-Ratio/RS-Momentum are conventionally computed over WEEKLY closes (RRG's own standard
# smoothing period — daily closes are too noisy for the ratio's own rolling mean/stddev to
# stabilize into interpretable quadrant crossings). `TaiwanStockWeekPrice` is Sponsor-tier
# (confirmed live: 400s for a free-tier key), so weekly closes are resampled here from the
# already-PUBLIC daily `Capability.PRICE_EOD` series instead — last trading day's close each
# ISO week, never an intra-week average (matches how every retail RRG tool defines "the weekly
# close").
_SECTOR_RRG_RS_RATIO_PERIOD_WEEKS = 10  # JdK's own standard smoothing window


_SECTOR_RRG_RS_MOMENTUM_PERIOD_WEEKS = 4  # JdK's own standard momentum window


_SECTOR_RRG_HISTORY_WEEKS = 26  # trailing weeks plotted (tail length), independent of the two


# smoothing windows above — a daily-close lookback long enough to derive this many post-warm-up
# weekly RS-Ratio points is computed from it, not a caller-chosen range (mirrors
# `_SECTOR_ROTATION_WINDOW_TRADING_DAYS`'s own "fixed window, not open-ended" reasoning).
_SECTOR_RRG_DAILY_LOOKBACK_DAYS = (
    _SECTOR_RRG_HISTORY_WEEKS
    + _SECTOR_RRG_RS_RATIO_PERIOD_WEEKS
    + _SECTOR_RRG_RS_MOMENTUM_PERIOD_WEEKS
) * 7 + 14  # +2 weeks slack for holiday-shortened weeks


class SectorRrgWeekPoint(BaseModel):
    """One industry's RS-Ratio/RS-Momentum reading for one ISO week — the JdK relative-rotation
    coordinate pair `RotationCanvas` plots per sector per week."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    week_ending: date_  # last trading day of the ISO week this point summarizes
    rs_ratio: float  # 100 = performing exactly in line with the TAIEX benchmark
    rs_momentum: float  # 100 = the ratio's own trend is flat; >100 accelerating, <100 decelerating
    weekly_change_pct: float  # the index's own week-over-week %, for the rank panel/tooltip


class SectorRrgSeries(BaseModel):
    """One industry sub-index's full RRG history — a trail `CometTail`/`RankPanel` renders."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    industry_id: str  # FinMind data_id, e.g. "Semiconductor" — stable key across weeks
    industry_name: str  # the id's own FinMind stock_name — never an invented label
    weight: float  # 0-1, this sector's share of latest total turnover across the whole set —
    # the only cross-sector figure here, sizes `SectorBadge` (never the RS-Ratio/Momentum axes)
    points: tuple[SectorRrgWeekPoint, ...] = Field(default_factory=tuple)


class GetSectorRotationRrgInput(BaseModel):
    """A market + an optional end date (defaults to today) — same "no per-symbol scope, just a
    window end" shape `GetSectorRotationInput` uses, since this tool has no per-symbol axis
    either (it fetches a fixed, market-level set of industry sub-indices, not a caller-chosen
    one)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    end_date: date_ | None = Field(
        default=None,
        description="Last week's trading day to compute the RRG through (ISO date). Omitted "
        "means today.",
    )


class GetSectorRotationRrgOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    benchmark_id: str = _SECTOR_RRG_BENCHMARK_ID
    benchmark_name: str = _SECTOR_RRG_BENCHMARK_NAME
    week_endings: tuple[date_, ...] = Field(default_factory=tuple)
    data_available: bool
    unavailable_reason: str | None = None
    sectors: tuple[SectorRrgSeries, ...] = Field(default_factory=tuple)


def _weekly_closes_from_daily(bars: Sequence[EODBar]) -> dict[date_, float]:
    """Last trading day's close per ISO (year, week) — the weekly-close convention this RRG
    computation uses throughout, never an intra-week average."""
    by_iso_week: dict[tuple[int, int], EODBar] = {}
    for bar in bars:
        key = bar.trade_date.isocalendar()[:2]
        current = by_iso_week.get(key)
        if current is None or bar.trade_date > current.trade_date:
            by_iso_week[key] = bar
    return {bar.trade_date: bar.close for bar in by_iso_week.values()}


def _rolling_mean(values: Sequence[float], period: int) -> list[float | None]:
    """`None` for the first `period - 1` points (no full window yet), else the trailing mean —
    the shared rolling-window shape both `_rs_ratio_series` and `_rs_momentum_series` need."""
    out: list[float | None] = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(None)
            continue
        window = values[i - period + 1 : i + 1]
        out.append(sum(window) / period)
    return out


def _rolling_stdev(values: Sequence[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(None)
            continue
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((v - mean) ** 2 for v in window) / period
        out.append(variance**0.5)
    return out


def _rs_ratio_series(
    sector_closes: Sequence[float], benchmark_closes: Sequence[float], period: int
) -> list[float | None]:
    """JdK RS-Ratio — a relative-strength ratio (`sector / benchmark`), itself normalized to a
    rolling z-score rescaled to center on 100, so "100" always means "performing exactly in
    line with the benchmark" regardless of the two series' raw price levels."""
    relative = [s / b for s, b in zip(sector_closes, benchmark_closes, strict=True)]
    means = _rolling_mean(relative, period)
    stdevs = _rolling_stdev(relative, period)
    out: list[float | None] = []
    for rel, mean, stdev in zip(relative, means, stdevs, strict=True):
        if mean is None or stdev is None or stdev == 0:
            out.append(None)
            continue
        out.append(100 + ((rel - mean) / stdev) * 100)
    return out


def _rs_momentum_series(rs_ratio: Sequence[float | None], period: int) -> list[float | None]:
    """JdK RS-Momentum — the RS-Ratio's own rate of change, itself normalized the same way (a
    rolling z-score of the ratio's `period`-week-over-`period`-week percent change, rescaled to
    center on 100)."""
    roc: list[float | None] = []
    for i, current in enumerate(rs_ratio):
        prior = rs_ratio[i - period] if i >= period else None
        if current is None or prior is None or prior == 0:
            roc.append(None)
            continue
        roc.append((current / prior - 1) * 100)
    numeric_roc = [v for v in roc if v is not None]
    if not numeric_roc:
        return [None] * len(roc)
    mean = sum(numeric_roc) / len(numeric_roc)
    variance = sum((v - mean) ** 2 for v in numeric_roc) / len(numeric_roc)
    stdev = variance**0.5
    if stdev == 0:
        return [100.0 if v is not None else None for v in roc]
    return [100 + ((v - mean) / stdev) * 100 if v is not None else None for v in roc]


def _sector_rrg_points(
    sector_series: Sequence[float],
    benchmark_series: Sequence[float],
    common_weeks: Sequence[date_],
    plotted_weeks: set[date_],
) -> list[SectorRrgWeekPoint]:
    """One sector's plotted `SectorRrgWeekPoint`s — the per-sector RS-Ratio/RS-Momentum
    computation and week-filtering `GetSectorRotationRrgTool.run` used to do inline in its own
    per-sector loop, pulled out to keep that method's cognitive complexity within the linter's
    limit."""
    rs_ratio = _rs_ratio_series(sector_series, benchmark_series, _SECTOR_RRG_RS_RATIO_PERIOD_WEEKS)
    rs_momentum = _rs_momentum_series(rs_ratio, _SECTOR_RRG_RS_MOMENTUM_PERIOD_WEEKS)
    points: list[SectorRrgWeekPoint] = []
    for i, week in enumerate(common_weeks):
        if week not in plotted_weeks:
            continue
        ratio = rs_ratio[i]
        momentum = rs_momentum[i]
        if ratio is None or momentum is None:
            continue
        prior_close = sector_series[i - 1] if i > 0 else None
        weekly_change_pct = (
            (sector_series[i] / prior_close - 1) * 100
            if prior_close is not None and prior_close != 0
            else 0.0
        )
        points.append(
            SectorRrgWeekPoint(
                week_ending=week,
                rs_ratio=ratio,
                rs_momentum=momentum,
                weekly_change_pct=weekly_change_pct,
            )
        )
    return points


def _weighted_by_latest_abs_change(
    sectors: Sequence[SectorRrgSeries],
) -> tuple[SectorRrgSeries, ...]:
    """Each sector's `weight` = its latest weekly |% change| as a share of the total across every
    plotted sector — the only cross-sector figure in this tool's output, computed once every
    sector's own points are known rather than inline in `run`."""
    latest_abs_change = {s.industry_id: abs(s.points[-1].weekly_change_pct) for s in sectors}
    total_abs_change = sum(latest_abs_change.values()) or 1.0
    return tuple(
        s.model_copy(update={"weight": latest_abs_change[s.industry_id] / total_abs_change})
        for s in sectors
    )


class GetSectorRotationRrgTool(BaseTool):
    """`get_sector_rotation_rrg` — a real Relative Rotation Graph (RS-Ratio x RS-Momentum,
    weekly) for TWSE's published industry sub-indices against TAIEX, the classic JdK RRG
    construction (see `_rs_ratio_series`/`_rs_momentum_series` docstrings for the exact formula).

    Unlike `get_sector_rotation` (daily net institutional flow, deliberately NOT an RRG per that
    tool's own docstring — no benchmark, no weekly smoothing), this tool fetches each sub-index's
    OWN published price series (`Capability.PRICE_EOD` with `data_id` set to the TWSE index code,
    e.g. `Semiconductor`) plus `TAIEX` as the benchmark, resamples daily closes to weekly, and
    computes the standard rolling-z-score ratio/momentum — real relative-strength math, not a
    flow proxy standing in for one.

    Public tier: `Capability.PRICE_EOD` is `DataClass.PUBLIC` for every `data_id` this tool
    requests (same dataset, same tier, regardless of whether `data_id` names a stock or an
    index) — no new tier decision needed.
    """

    name = "get_sector_rotation_rrg"
    description = (
        "Get a real weekly Relative Rotation Graph (RS-Ratio x RS-Momentum, JdK construction) "
        "for TWSE's published industry sub-indices against TAIEX — where each sector currently "
        "sits and its recent trail across the leading/weakening/lagging/improving quadrants."
    )
    input_model: type[BaseModel] = GetSectorRotationRrgInput
    output_model: type[BaseModel] = GetSectorRotationRrgOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "sector_rotation_rrg"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetSectorRotationRrgInput, args)
        market = typed.market.value
        end = typed.end_date or date_.today()
        start = end - timedelta(days=_SECTOR_RRG_DAILY_LOOKBACK_DAYS)

        async def _weekly_closes(data_id: str) -> dict[date_, float]:
            _result, bars = await fetch_rows(
                self._router,
                Capability.PRICE_EOD,
                Symbol(id=data_id, market=typed.market),
                tls_policy=ctx.tls_policy,
                model=EODBar,
                key="bars",
                params={"start_date": start.isoformat(), "end_date": end.isoformat()},
            )
            return _weekly_closes_from_daily(bars)

        benchmark_closes_by_week = await _weekly_closes(_SECTOR_RRG_BENCHMARK_ID)
        if not benchmark_closes_by_week:
            return GetSectorRotationRrgOutput(
                market=market,
                data_available=False,
                unavailable_reason="TAIEX benchmark series unavailable for this window.",
            )
        sector_closes_by_week = {
            data_id: await _weekly_closes(data_id) for data_id, _name in SECTOR_RRG_INDEX_IDS
        }

        common_weeks = sorted(
            set(benchmark_closes_by_week).intersection(
                *(set(w) for w in sector_closes_by_week.values() if w)
            )
        )
        min_weeks_needed = (
            _SECTOR_RRG_RS_RATIO_PERIOD_WEEKS + _SECTOR_RRG_RS_MOMENTUM_PERIOD_WEEKS + 1
        )
        if len(common_weeks) < min_weeks_needed:
            return GetSectorRotationRrgOutput(
                market=market,
                data_available=False,
                unavailable_reason="Not enough overlapping weekly history yet for a stable "
                "RS-Ratio/RS-Momentum window.",
            )
        plotted_weeks = common_weeks[-(_SECTOR_RRG_HISTORY_WEEKS):]
        plotted_weeks_set = set(plotted_weeks)
        benchmark_series = [benchmark_closes_by_week[w] for w in common_weeks]

        sectors: list[SectorRrgSeries] = []
        for data_id, display_name in SECTOR_RRG_INDEX_IDS:
            closes_by_week = sector_closes_by_week.get(data_id)
            if not closes_by_week:
                continue
            sector_series = [closes_by_week[w] for w in common_weeks]
            points = _sector_rrg_points(
                sector_series, benchmark_series, common_weeks, plotted_weeks_set
            )
            if points:
                sectors.append(
                    SectorRrgSeries(
                        industry_id=data_id,
                        industry_name=display_name,
                        weight=0.0,  # filled in below once every sector's series is known
                        points=tuple(points),
                    )
                )

        if not sectors:
            return GetSectorRotationRrgOutput(
                market=market,
                data_available=False,
                unavailable_reason="No sector had enough overlapping weekly history to plot.",
            )

        return GetSectorRotationRrgOutput(
            market=market,
            week_endings=tuple(plotted_weeks),
            data_available=True,
            sectors=_weighted_by_latest_abs_change(sectors),
        )
