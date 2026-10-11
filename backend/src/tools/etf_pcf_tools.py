"""ETF fund-info tool — wraps `sources/etf_pcf.py` via the router (`Capability.ETF_HOLDINGS`).

This is the fund-level metadata slice only (units outstanding, tracked index, manager company)
— per-constituent holdings/weights and NAV/IOPV remain a documented, unfilled gap in the
adapter itself (see its module docstring); this tool surfaces that gap honestly through
`EtfFundInfo`'s own optional fields rather than fabricating anything.
"""

from __future__ import annotations

from datetime import date as date_
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from config.settings import get_settings
from dataaccess.router import DataAccessRouter
from ingest.jobs import ETF_AUM_DATASET, ETF_NAV_DATASET
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import (
    EtfAumRow,
    EtfExpenseInfo,
    EtfFundInfo,
    EtfNavRow,
    EtfThemeTags,
    EtfTrailingReturns,
)
from schemas.chart_hint import ChartHint, FieldDef, FieldType, Mark
from schemas.enums import Capability, DataClass, Market
from schemas.source import HealthStatus
from tools.base import BaseTool, SyncStoreTool, Tool, ToolContext, parse_args
from tools.common import SymbolInput


class GetEtfFundInfoOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    info: EtfFundInfo | None = None


class GetEtfFundInfoTool(BaseTool):
    """`get_etf_fund_info` — wraps `Capability.ETF_HOLDINGS`'s fund-level slice (issuer,
    units outstanding, tracked index, manager company) for one listed ETF."""

    name = "get_etf_fund_info"
    description = (
        "Get ETF fund-level reference info (issuer, tracked index, units outstanding, "
        "manager company) for one listed ETF — not per-constituent holdings or NAV/IOPV."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetEtfFundInfoOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "etf_fund_info"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result = await self._router.fetch(
            Capability.ETF_HOLDINGS, typed.to_symbol(), tls_policy=ctx.tls_policy
        )
        raw_info = result.payload.get("info")
        info = (
            EtfFundInfo.model_validate(cast("dict[str, object]", raw_info))
            if isinstance(raw_info, dict)
            else None
        )
        return GetEtfFundInfoOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            info=info,
        )


class GetEtfExpenseRatioOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    info: EtfExpenseInfo | None = None


class GetEtfExpenseRatioTool(BaseTool):
    """`get_etf_expense_ratio` — wraps `Capability.ETF_EXPENSE_RATIO` (SITCA fee disclosure) for
    one fund/ETF: 經理費/保管費/保管機構 + an approximate TER (經理費+保管費, a floor). Not the
    audited total-expense-ratio and not NAV/IOPV."""

    name = "get_etf_expense_ratio"
    description = (
        "Get a fund/ETF's internal-fee disclosure (內扣費用): management fee, custody fee, "
        "custodian bank, and an approximate total-expense-ratio floor (management + custody), "
        "from the SITCA monthly report."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetEtfExpenseRatioOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "etf_expense_ratio"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result = await self._router.fetch(
            Capability.ETF_EXPENSE_RATIO, typed.to_symbol(), tls_policy=ctx.tls_policy
        )
        raw_info = result.payload.get("info")
        info = (
            EtfExpenseInfo.model_validate(cast("dict[str, object]", raw_info))
            if isinstance(raw_info, dict)
            else None
        )
        return GetEtfExpenseRatioOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            info=info,
        )


class GetEtfThemeTagsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    tags: tuple[str, ...] = Field(default_factory=tuple)
    holder_count_wan: float | None = None


class GetEtfThemeTagsTool(BaseTool):
    """`get_etf_theme_tags` — wraps `Capability.ETF_THEME_TAGS` (TWSE's own ETF e添富 microsite's
    主題/因子 editorial classification + 受益人次(萬人) holder count) for one listed ETF. Empty
    `tags` / `None` `holder_count_wan` are real, valid results (not every ETF carries a tag, and
    not every page carries the holder-count stat) — never fabricated."""

    name = "get_etf_theme_tags"
    description = (
        "Get TWSE's own 主題/因子 (theme/factor) editorial tags (e.g. 高股息/科技主題型/"
        "公司治理及ESG) plus 受益人次 (holder count, in 萬人) for one listed ETF."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetEtfThemeTagsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "etf_theme_tags"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result = await self._router.fetch(
            Capability.ETF_THEME_TAGS, typed.to_symbol(), tls_policy=ctx.tls_policy
        )
        raw_tags = result.payload.get("tags")
        raw_holder_count = result.payload.get("holder_count_wan")
        parsed = EtfThemeTags.model_validate(
            {
                "symbol": typed.id,
                "market": typed.market.value,
                "tags": raw_tags if isinstance(raw_tags, list) else (),
                "holder_count_wan": raw_holder_count
                if isinstance(raw_holder_count, int | float)
                else None,
            }
        )
        return GetEtfThemeTagsOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            tags=parsed.tags,
            holder_count_wan=parsed.holder_count_wan,
        )


class TrailingReturnPoint(BaseModel):
    """One horizon of `EtfTrailingReturns`, flattened so a chart can plot the six of them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon: str
    return_pct: float


class GetEtfTrailingReturnsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    returns: EtfTrailingReturns | None = None
    # The same six figures as a list, so the bar chart below can plot them. Horizons the history
    # does not reach are simply absent — never drawn as a zero bar.
    series: tuple[TrailingReturnPoint, ...] = Field(default_factory=tuple)
    chart_hint: ChartHint | None = None


def _trailing_series(returns: EtfTrailingReturns | None) -> tuple[TrailingReturnPoint, ...]:
    if returns is None:
        return ()
    horizons = (
        ("1個月", returns.return_1m_pct),
        ("1季", returns.return_1q_pct),
        ("半年", returns.return_6m_pct),
        ("1年", returns.return_1y_pct),
        ("3年", returns.return_3y_pct),
        ("5年", returns.return_5y_pct),
    )
    return tuple(
        TrailingReturnPoint(horizon=label, return_pct=value)
        for label, value in horizons
        if value is not None
    )


class GetEtfTrailingReturnsTool(BaseTool):
    """`get_etf_trailing_returns` — wraps `Capability.ETF_TRAILING_RETURNS` (see
    `EtfTrailingReturns`'s own docstring in `schemas/capabilities.py` for why this is computed
    from a dedicated split/dividend-adjusted yfinance history fetch rather than this codebase's
    ingested — unadjusted — `price_eod` bars). `returns` is `None` only on a genuine fetch
    failure (unreachable/stale); a `None` individual horizon field inside a present `returns` is
    the normal, honest case for a fund whose history doesn't reach that far back yet."""

    name = "get_etf_trailing_returns"
    description = (
        "Get one listed ETF's price return over 6 fixed trailing horizons — 1個月/1季/半年/1年/"
        "3年/5年 — computed from a single split/dividend-adjusted price-history fetch, so a "
        "historical stock split never reads as a fabricated crash."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetEtfTrailingReturnsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "etf_trailing_returns"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result = await self._router.fetch(
            Capability.ETF_TRAILING_RETURNS, typed.to_symbol(), tls_policy=ctx.tls_policy
        )
        raw_returns = result.payload.get("returns")
        returns = (
            EtfTrailingReturns.model_validate(raw_returns)
            if isinstance(raw_returns, dict)
            else None
        )
        series = _trailing_series(returns)
        return GetEtfTrailingReturnsOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            returns=returns,
            series=series,
            chart_hint=ChartHint(
                mark=Mark.BAR,
                title="累計報酬（%，還原權息價格）",
                data_key="series",
                x=FieldDef(field="horizon", type=FieldType.ORDINAL),
                y=FieldDef(field="return_pct", type=FieldType.QUANTITATIVE),
            )
            if series
            else None,
        )


_PREMIUM_DISCOUNT_DAYS_DEFAULT = 60
_PREMIUM_DISCOUNT_DAYS_MIN = 2
_PREMIUM_DISCOUNT_DAYS_MAX = 180
_ETF_AUM_CAVEAT = (
    "資產規模 (AUM) is Yahoo's own totalAssets figure for this fund — refreshed weekly, not a "
    "live figure; may be missing for a recently-listed or thinly-covered fund."
)


class GetEtfPremiumDiscountInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, description="Exchange-scoped symbol id, e.g. '00981A'.")
    market: Market = Field(description="Exchange market, e.g. 'TW'.")
    days: int = Field(
        default=_PREMIUM_DISCOUNT_DAYS_DEFAULT,
        ge=_PREMIUM_DISCOUNT_DAYS_MIN,
        le=_PREMIUM_DISCOUNT_DAYS_MAX,
        description="Bounded trailing trading-day window ending at the latest ingested day.",
    )


class GetEtfPremiumDiscountOutput(BaseModel):
    """`etf_nav` (+ `etf_aum`) read for one ETF: TWSE's own published 預估折溢價幅度 history —
    never a per-symbol live fetch, this dataset is already ingested daily (`ingest/jobs.py`'s
    `job_etf_nav`). `current_aum` is folded in from the separate `etf_aum` dataset rather than
    a second tool, since it is a daily-latest figure (not a series) a caller wants alongside the
    premium/discount chart, not on its own."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    data_available: bool = True
    unavailable_reason: str | None = None
    rows: tuple[EtfNavRow, ...] = Field(default_factory=tuple)
    current_aum: float | None = None
    aum_caveat: str | None = None
    chart_hint: ChartHint | None = None


class GetEtfPremiumDiscountTool(SyncStoreTool):
    """`get_etf_premium_discount` — one TW ETF's 折溢價 (premium/discount to NAV) history over a
    bounded trailing window, read directly from the already-ingested `etf_nav` Parquet store (no
    router fetch — same "read the ingest-owned store, never re-fetch live" posture as
    `GetEtfListTool`, which already reads this exact dataset for its whole-market projection).
    """

    name = "get_etf_premium_discount"
    description = (
        "Get one TW ETF's premium/discount-to-NAV (折溢價) history over a bounded trailing "
        "trading-day window, plus its latest AUM — from TWSE's own published estimated-NAV feed."
    )
    input_model: type[BaseModel] = GetEtfPremiumDiscountInput
    output_model: type[BaseModel] = GetEtfPremiumDiscountOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "etf_premium_discount"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — reads the ingest-owned Parquet store directly
        typed = parse_args(GetEtfPremiumDiscountInput, args)
        market = typed.market.value
        known_dates = self._store.list_partition_dates(ETF_NAV_DATASET, market)
        if not known_dates:
            return GetEtfPremiumDiscountOutput(
                symbol=typed.id,
                market=typed.market,
                data_available=False,
                unavailable_reason=(
                    f"No ingested etf_nav partition found for {market} — the whole-market "
                    "ingest job may not have run yet."
                ),
            )
        window_dates = sorted(known_dates)[-typed.days :]
        raw_rows = self._store.read_range(
            ETF_NAV_DATASET, market, window_dates, symbols=(typed.id,)
        )
        # A day's partition can carry forward the prior confirmed `EtfNavRow.as_of` (the
        # upstream feed's own stale-carry-forward, confirmed against real ingested data — the
        # partition `date` and the row's own `as_of` are NOT always the same day), so several
        # partitions can yield rows for the same `as_of`. Dedupe on `as_of`, keeping the row
        # read from the latest partition for that `as_of` (the freshest read of that day's
        # disclosure) — never concatenate raw partition rows into a history with repeats.
        by_as_of: dict[date_, EtfNavRow] = {}
        for row in rows_from_store(EtfNavRow, raw_rows):
            if row.symbol == typed.id:
                by_as_of[row.as_of] = row
        rows = tuple(sorted(by_as_of.values(), key=lambda row: row.as_of))
        current_aum, aum_caveat = self._current_aum(market, typed.id)
        if not rows:
            return GetEtfPremiumDiscountOutput(
                symbol=typed.id,
                market=typed.market,
                data_available=False,
                unavailable_reason=(
                    f"No etf_nav rows found for {typed.id} in the last {typed.days} trading "
                    "days — this symbol may not be a listed ETF, or its NAV disclosure is "
                    "not covered by this feed."
                ),
                current_aum=current_aum,
                aum_caveat=aum_caveat,
            )
        return GetEtfPremiumDiscountOutput(
            symbol=typed.id,
            market=typed.market,
            rows=rows,
            current_aum=current_aum,
            aum_caveat=aum_caveat,
            chart_hint=ChartHint(
                mark=Mark.LINE,
                title="預估折溢價幅度（%）",
                data_key="rows",
                x=FieldDef(field="as_of", type=FieldType.TEMPORAL),
                y=FieldDef(field="premium_discount_pct", type=FieldType.QUANTITATIVE),
            ),
        )

    def _current_aum(self, market: str, symbol: str) -> tuple[float | None, str | None]:
        aum_dates = self._store.list_partition_dates(ETF_AUM_DATASET, market)
        if not aum_dates:
            return None, None
        raw_aum = self._store.read_partition(ETF_AUM_DATASET, market, max(aum_dates))
        for row in rows_from_store(EtfAumRow, raw_aum):
            if row.symbol == symbol and row.aum is not None:
                return row.aum, _ETF_AUM_CAVEAT
        return None, None


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    """`get_etf_premium_discount` reads the ingest-owned Parquet store directly (see its class
    docstring), so this function builds its own `ParquetStore` from `config.settings` here,
    same pattern `tools/market/__init__.py::build_registration_table` uses for its own store-reading
    tools alongside its router-backed ones."""
    store = ParquetStore(root=get_settings().parquet_root)
    return [
        (GetEtfFundInfoTool(router), False),
        (GetEtfExpenseRatioTool(router), False),
        (GetEtfThemeTagsTool(router), False),
        (GetEtfTrailingReturnsTool(router), False),
        (GetEtfPremiumDiscountTool(store), False),
    ]


__all__ = [
    "GetEtfExpenseRatioOutput",
    "GetEtfExpenseRatioTool",
    "GetEtfFundInfoOutput",
    "GetEtfFundInfoTool",
    "GetEtfPremiumDiscountInput",
    "GetEtfPremiumDiscountOutput",
    "GetEtfPremiumDiscountTool",
    "GetEtfThemeTagsOutput",
    "GetEtfThemeTagsTool",
    "GetEtfTrailingReturnsOutput",
    "GetEtfTrailingReturnsTool",
    "build_registration_table",
]
