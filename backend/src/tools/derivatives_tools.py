"""Derivatives tools — the FinMind Derivative-category datasets. Contracts are identified by
`id` the same way an equity is (futures/option contract code), so every tool here still takes
`SymbolInput`/`DateRangeInput`/`MarketOnlyInput` rather than a bespoke shape. The whole-roster
`futopt_info` and the market-level three-major-institutional-investor day-session flows are
`MarketOnlyInput`; everything scoped to one contract is `DateRangeInput`.

Tick-level datasets (`futures_tick`/`futures_spread_tick`/`option_tick`) and the two after-
hours institutional-flow variants are deliberately left unwrapped this pass — niche,
high-volume, low-marginal-value over the already-wrapped day-session flow and EOD price tools;
documented here rather than silently dropped.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import (
    AssetSwapFixedIncomeDailyRow,
    AssetSwapOptionDailyRow,
    FinalSettlementPriceRow,
    FutOptInfoRow,
    FuturesDailyRow,
    FuturesDealerVolumeRow,
    FuturesInstitutionalFlowRow,
    FuturesOiLargeTradersRow,
    FuturesSpreadTradingRow,
    OptionDailyRow,
    OptionDealerVolumeRow,
    OptionInstitutionalFlowRow,
    OptionOiLargeTradersRow,
    OptionVixRow,
)
from schemas.enums import Capability, DataClass
from schemas.source import HealthStatus
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import DateRangeInput, MarketOnlyInput


class _RowsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus


class GetFutOptRosterOutput(_RowsOutput):
    rows: tuple[FutOptInfoRow, ...] = Field(default_factory=tuple)


class GetFutOptRosterTool(BaseTool):
    """`get_futopt_roster` — wraps `Capability.FUTOPT_INFO` (whole-roster futures/options
    contract-code reference table)."""

    name = "get_futopt_roster"
    description = "Get the whole-market futures/options contract-code reference roster."
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetFutOptRosterOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.FUTOPT_INFO,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=FutOptInfoRow,
            key="rows",
        )
        return GetFutOptRosterOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetFuturesOptionsDailyOutput(_RowsOutput):
    futures: tuple[FuturesDailyRow, ...] = Field(default_factory=tuple)
    options: tuple[OptionDailyRow, ...] = Field(default_factory=tuple)


class GetFuturesOptionsDailyTool(BaseTool):
    """`get_futures_options_daily` — bundles `Capability.FUTURES_DAILY` + `Capability.
    OPTION_DAILY` for one contract code into one report (a caller passes the same `id` for
    both fetches; a contract code is either a futures or an option code in practice, so one of
    the two result sets will typically be empty — that's expected, not an error)."""

    name = "get_futures_options_daily"
    description = (
        "Get futures and option EOD price/volume/open-interest rows for one contract code."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetFuturesOptionsDailyOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "futures_options_daily"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        symbol = typed.to_symbol()
        params = typed.date_params()
        futures_result, futures_rows = await fetch_rows(
            self._router,
            Capability.FUTURES_DAILY,
            symbol,
            tls_policy=ctx.tls_policy,
            model=FuturesDailyRow,
            key="rows",
            params=params,
        )
        _options_result, options_rows = await fetch_rows(
            self._router,
            Capability.OPTION_DAILY,
            symbol,
            tls_policy=ctx.tls_policy,
            model=OptionDailyRow,
            key="rows",
            params=params,
        )
        return GetFuturesOptionsDailyOutput(
            source_id=futures_result.source_id,
            currentness_caveat=futures_result.currentness_caveat,
            health_status=futures_result.health.status,
            futures=futures_rows,
            options=options_rows,
        )


class GetDerivativesInstitutionalOutput(_RowsOutput):
    futures: tuple[FuturesInstitutionalFlowRow, ...] = Field(default_factory=tuple)
    options: tuple[OptionInstitutionalFlowRow, ...] = Field(default_factory=tuple)
    futures_oi_large_traders: tuple[FuturesOiLargeTradersRow, ...] = Field(default_factory=tuple)
    option_oi_large_traders: tuple[OptionOiLargeTradersRow, ...] = Field(default_factory=tuple)


class GetDerivativesInstitutionalTool(BaseTool):
    """`get_derivatives_institutional` — bundles the day-session three-major-institutional-
    investor futures/option flow (`FUTURES_INSTITUTIONAL_FLOW`/`OPTION_INSTITUTIONAL_FLOW`,
    public) with the large-trader open-interest concentration
    (`FUTURES_OI_LARGE_TRADERS`/`OPTION_OI_LARGE_TRADERS`, Sponsor) into one descriptive
    positioning-context report. Whole-market when `id` is omitted's counterpart symbol is
    empty — see each row model's own documented upstream gap; this tool always passes the
    caller's `id`, it does not itself choose to omit it (never a full-market-scan shape)."""

    name = "get_derivatives_institutional"
    description = (
        "Get three-major-institutional-investor futures/option flow plus large-trader "
        "open-interest concentration for one contract code — descriptive positioning context, "
        "the large-trader slice is Sponsor-tier."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetDerivativesInstitutionalOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "derivatives_institutional"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        symbol = typed.to_symbol()
        params = typed.date_params()
        futures_result, futures_rows = await fetch_rows(
            self._router,
            Capability.FUTURES_INSTITUTIONAL_FLOW,
            symbol,
            tls_policy=ctx.tls_policy,
            model=FuturesInstitutionalFlowRow,
            key="rows",
            params=params,
        )
        _options_result, options_rows = await fetch_rows(
            self._router,
            Capability.OPTION_INSTITUTIONAL_FLOW,
            symbol,
            tls_policy=ctx.tls_policy,
            model=OptionInstitutionalFlowRow,
            key="rows",
            params=params,
        )
        _fut_oi_result, fut_oi_rows = await fetch_rows(
            self._router,
            Capability.FUTURES_OI_LARGE_TRADERS,
            symbol,
            tls_policy=ctx.tls_policy,
            model=FuturesOiLargeTradersRow,
            key="rows",
            params=params,
        )
        _opt_oi_result, opt_oi_rows = await fetch_rows(
            self._router,
            Capability.OPTION_OI_LARGE_TRADERS,
            symbol,
            tls_policy=ctx.tls_policy,
            model=OptionOiLargeTradersRow,
            key="rows",
            params=params,
        )
        return GetDerivativesInstitutionalOutput(
            source_id=futures_result.source_id,
            currentness_caveat=futures_result.currentness_caveat,
            health_status=futures_result.health.status,
            futures=futures_rows,
            options=options_rows,
            futures_oi_large_traders=fut_oi_rows,
            option_oi_large_traders=opt_oi_rows,
        )


class GetPutCallRatioOutput(_RowsOutput):
    options: tuple[OptionDailyRow, ...] = Field(default_factory=tuple)


class GetPutCallRatioTool(BaseTool):
    """`get_put_call_ratio` — descriptive P/C positioning context, backed by
    `Capability.OPTION_DAILY` (raw call/put daily volume — no separate ratio dataset exists
    upstream; the ratio itself is a trivial derived arithmetic over these rows, deliberately
    left to the caller/agent rather than computed here, since this tool has no per-contract
    aggregation logic of its own to combine multiple strikes into one ratio honestly)."""

    name = "get_put_call_ratio"
    description = (
        "Get option daily volume rows (split by call/put) for one underlying's contract "
        "series — the raw input for a put/call-ratio read, descriptive positioning context only."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetPutCallRatioOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "put_call_ratio"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.OPTION_DAILY,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=OptionDailyRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetPutCallRatioOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            options=rows,
        )


class GetTaiexVixOutput(_RowsOutput):
    rows: tuple[OptionVixRow, ...] = Field(default_factory=tuple)


class GetTaiexVixTool(BaseTool):
    """`get_taiex_vix` — wraps `Capability.OPTION_VIX` (臺指選擇權波動率指數)."""

    name = "get_taiex_vix"
    description = "Get the TAIEX option volatility index (VIX) reading series (Sponsor-tier)."
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetTaiexVixOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "taiex_vix"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.OPTION_VIX,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=OptionVixRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetTaiexVixOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetDealerVolumeOutput(_RowsOutput):
    futures: tuple[FuturesDealerVolumeRow, ...] = Field(default_factory=tuple)
    options: tuple[OptionDealerVolumeRow, ...] = Field(default_factory=tuple)


class GetDealerVolumeTool(BaseTool):
    """`get_dealer_volume` — bundles `Capability.FUTURES_DEALER_VOLUME` + `Capability.
    OPTION_DEALER_VOLUME` (per-dealer trading volume) for one contract code."""

    name = "get_dealer_volume"
    description = "Get per-dealer futures and option trading-volume rows for one contract code."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetDealerVolumeOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "dealer_volume"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        symbol = typed.to_symbol()
        params = typed.date_params()
        futures_result, futures_rows = await fetch_rows(
            self._router,
            Capability.FUTURES_DEALER_VOLUME,
            symbol,
            tls_policy=ctx.tls_policy,
            model=FuturesDealerVolumeRow,
            key="rows",
            params=params,
        )
        _options_result, options_rows = await fetch_rows(
            self._router,
            Capability.OPTION_DEALER_VOLUME,
            symbol,
            tls_policy=ctx.tls_policy,
            model=OptionDealerVolumeRow,
            key="rows",
            params=params,
        )
        return GetDealerVolumeOutput(
            source_id=futures_result.source_id,
            currentness_caveat=futures_result.currentness_caveat,
            health_status=futures_result.health.status,
            futures=futures_rows,
            options=options_rows,
        )


class GetFuturesSpreadTradingOutput(_RowsOutput):
    rows: tuple[FuturesSpreadTradingRow, ...] = Field(default_factory=tuple)


class GetFuturesSpreadTradingTool(BaseTool):
    """`get_futures_spread_trading` — wraps `Capability.FUTURES_SPREAD_TRADING` (期貨價差
    quote)."""

    name = "get_futures_spread_trading"
    description = "Get futures calendar-spread quote rows for one contract code (Sponsor-tier)."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetFuturesSpreadTradingOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "futures_spread_trading"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.FUTURES_SPREAD_TRADING,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=FuturesSpreadTradingRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetFuturesSpreadTradingOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetFinalSettlementPriceOutput(_RowsOutput):
    futures: tuple[FinalSettlementPriceRow, ...] = Field(default_factory=tuple)
    options: tuple[FinalSettlementPriceRow, ...] = Field(default_factory=tuple)


class GetFinalSettlementPriceTool(BaseTool):
    """`get_final_settlement_price` — bundles `Capability.FUTURES_FINAL_SETTLEMENT_PRICE` +
    `Capability.OPTION_FINAL_SETTLEMENT_PRICE` (最後結算價) for one contract code."""

    name = "get_final_settlement_price"
    description = (
        "Get futures and option final-settlement-price rows for one contract code (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetFinalSettlementPriceOutput
    data_class = DataClass.SPONSOR

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        symbol = typed.to_symbol()
        params = typed.date_params()
        futures_result, futures_rows = await fetch_rows(
            self._router,
            Capability.FUTURES_FINAL_SETTLEMENT_PRICE,
            symbol,
            tls_policy=ctx.tls_policy,
            model=FinalSettlementPriceRow,
            key="rows",
            params=params,
        )
        _options_result, options_rows = await fetch_rows(
            self._router,
            Capability.OPTION_FINAL_SETTLEMENT_PRICE,
            symbol,
            tls_policy=ctx.tls_policy,
            model=FinalSettlementPriceRow,
            key="rows",
            params=params,
        )
        return GetFinalSettlementPriceOutput(
            source_id=futures_result.source_id,
            currentness_caveat=futures_result.currentness_caveat,
            health_status=futures_result.health.status,
            futures=futures_rows,
            options=options_rows,
        )


class GetAssetSwapDailyOutput(_RowsOutput):
    fixed_income: tuple[AssetSwapFixedIncomeDailyRow, ...] = Field(default_factory=tuple)
    option: tuple[AssetSwapOptionDailyRow, ...] = Field(default_factory=tuple)


class GetAssetSwapDailyTool(BaseTool):
    """`get_asset_swap_daily` — bundles `Capability.ASSET_SWAP_FIXED_INCOME_DAILY` +
    `Capability.ASSET_SWAP_OPTION_DAILY` (資產交換 daily trading) for one stock."""

    name = "get_asset_swap_daily"
    description = (
        "Get fixed-income and option asset-swap daily trading rows for one stock (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetAssetSwapDailyOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "asset_swap_daily"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        symbol = typed.to_symbol()
        params = typed.date_params()
        fixed_income_result, fixed_income_rows = await fetch_rows(
            self._router,
            Capability.ASSET_SWAP_FIXED_INCOME_DAILY,
            symbol,
            tls_policy=ctx.tls_policy,
            model=AssetSwapFixedIncomeDailyRow,
            key="rows",
            params=params,
        )
        _option_result, option_rows = await fetch_rows(
            self._router,
            Capability.ASSET_SWAP_OPTION_DAILY,
            symbol,
            tls_policy=ctx.tls_policy,
            model=AssetSwapOptionDailyRow,
            key="rows",
            params=params,
        )
        return GetAssetSwapDailyOutput(
            source_id=fixed_income_result.source_id,
            currentness_caveat=fixed_income_result.currentness_caveat,
            health_status=fixed_income_result.health.status,
            fixed_income=fixed_income_rows,
            option=option_rows,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [
        (GetFutOptRosterTool(router), False),
        (GetFuturesOptionsDailyTool(router), False),
        (GetDerivativesInstitutionalTool(router), False),
        (GetPutCallRatioTool(router), False),
        (GetTaiexVixTool(router), False),
        (GetDealerVolumeTool(router), False),
        (GetFuturesSpreadTradingTool(router), False),
        (GetFinalSettlementPriceTool(router), False),
        (GetAssetSwapDailyTool(router), False),
    ]


__all__ = [
    "GetAssetSwapDailyOutput",
    "GetAssetSwapDailyTool",
    "GetDealerVolumeOutput",
    "GetDealerVolumeTool",
    "GetDerivativesInstitutionalOutput",
    "GetDerivativesInstitutionalTool",
    "GetFinalSettlementPriceOutput",
    "GetFinalSettlementPriceTool",
    "GetFutOptRosterOutput",
    "GetFutOptRosterTool",
    "GetFuturesOptionsDailyOutput",
    "GetFuturesOptionsDailyTool",
    "GetFuturesSpreadTradingOutput",
    "GetFuturesSpreadTradingTool",
    "GetPutCallRatioOutput",
    "GetPutCallRatioTool",
    "GetTaiexVixOutput",
    "GetTaiexVixTool",
    "build_registration_table",
]
