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
from ingest.store import ParquetStore
from schemas.capabilities import EtfAumRow, EtfExpenseInfo, EtfFundInfo, EtfNavRow
from schemas.enums import Capability, DataClass, Market
from schemas.source import HealthStatus
from tools.base import BaseTool, Tool, ToolContext, parse_args
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


class GetEtfPremiumDiscountTool(BaseTool):
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

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
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
        for row in _rows_from_store(EtfNavRow, raw_rows):
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
        )

    def _current_aum(self, market: str, symbol: str) -> tuple[float | None, str | None]:
        aum_dates = self._store.list_partition_dates(ETF_AUM_DATASET, market)
        if not aum_dates:
            return None, None
        raw_aum = self._store.read_partition(ETF_AUM_DATASET, market, max(aum_dates))
        for row in _rows_from_store(EtfAumRow, raw_aum):
            if row.symbol == symbol and row.aum is not None:
                return row.aum, _ETF_AUM_CAVEAT
        return None, None


def _rows_from_store[T: BaseModel](model: type[T], raw_rows: list[dict[str, object]]) -> list[T]:
    """Local copy of `market_tools.py::_rows_from_store` — not imported cross-module (private to
    its own file). Drops the Hive-partition bookkeeping columns (`dataset`/`date`/`v`) DuckDB's
    read-back resurfaces alongside the written row data, before validating against this
    `extra="forbid"` model."""
    field_names = set(model.model_fields)
    return [
        model.model_validate({k: v for k, v in row.items() if k in field_names}) for row in raw_rows
    ]


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    """`get_etf_premium_discount` reads the ingest-owned Parquet store directly (see its class
    docstring), so this function builds its own `ParquetStore` from `config.settings` here,
    same pattern `market_tools.py::build_registration_table` uses for its own store-reading
    tools alongside its router-backed ones."""
    store = ParquetStore(root=get_settings().parquet_root)
    return [
        (GetEtfFundInfoTool(router), False),
        (GetEtfExpenseRatioTool(router), False),
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
    "build_registration_table",
]
