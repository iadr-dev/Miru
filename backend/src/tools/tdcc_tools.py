"""TDCC (集保結算所) OpenData tools — free/public, keyless. 6 router-based capability tools
(mirroring `chip_tools.py`'s "fetch one capability, validate its rows" shape) plus one
direct-adapter-call catch-all escape hatch for querying any other TDCC dataset id by its raw
CSV shape.

`data_class = DataClass.PUBLIC` on every tool here — genuinely free, unauthenticated upstream
data, no Sponsor gate.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from dataaccess.tdcc_query import TdccQueryGateway
from schemas.capabilities import (
    TdccEvotingScheduleRow,
    TdccMonthlyCustodyAccountsRow,
    TdccOffshoreFundNavRow,
    TdccPledgedCustodyShareRow,
    TdccSecuritiesInfoRow,
    TdccShareholdingDistributionRow,
)
from schemas.enums import Capability, DataClass
from schemas.source import HealthStatus
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import MarketOnlyInput, SymbolInput


class _TdccRowsOutput(BaseModel):
    """Shared output shape: source provenance + a tuple of normalized rows. Mirrors
    `chip_tools._RowsOutput` — each concrete tool below subclasses this once per row model so
    MCP still sees a distinctly-named, distinctly-typed output schema per tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus


class GetTdccSecuritiesInfoOutput(_TdccRowsOutput):
    rows: tuple[TdccSecuritiesInfoRow, ...] = Field(default_factory=tuple)


class GetTdccSecuritiesInfoTool(BaseTool):
    """`get_tdcc_securities_info` — wraps `Capability.TDCC_SECURITIES_INFO` (證券基本資料)."""

    name = "get_tdcc_securities_info"
    description = (
        "Get TDCC's securities master reference row (market/registrar/status/par-value) for "
        "one stock — a static reference record, not a price."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetTdccSecuritiesInfoOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.TDCC_SECURITIES_INFO,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=TdccSecuritiesInfoRow,
            key="rows",
        )
        return GetTdccSecuritiesInfoOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetTdccPledgedCustodySharesOutput(_TdccRowsOutput):
    rows: tuple[TdccPledgedCustodyShareRow, ...] = Field(default_factory=tuple)


class GetTdccPledgedCustodySharesTool(BaseTool):
    """`get_tdcc_pledged_custody_shares` — wraps `Capability.TDCC_PLEDGED_CUSTODY_SHARES`
    (集保股份保管明細: 分戶/特定保管股數 split)."""

    name = "get_tdcc_pledged_custody_shares"
    description = (
        "Get TDCC's dispersed-vs-specific custody share-split rows for one stock (集保股份"
        "保管明細) — covers only the subset of securities TDCC reports a split for."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetTdccPledgedCustodySharesOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.TDCC_PLEDGED_CUSTODY_SHARES,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=TdccPledgedCustodyShareRow,
            key="rows",
        )
        return GetTdccPledgedCustodySharesOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetTdccShareholdingDistributionOutput(_TdccRowsOutput):
    rows: tuple[TdccShareholdingDistributionRow, ...] = Field(default_factory=tuple)


class GetTdccShareholdingDistributionTool(BaseTool):
    """`get_tdcc_shareholding_distribution` — wraps
    `Capability.TDCC_SHAREHOLDING_DISTRIBUTION` (集保戶股權分散表), free/weekly, distinct from
    `chip_tools.GetShareholderDispersionTool`'s Sponsor-tier FinMind equivalent."""

    name = "get_tdcc_shareholding_distribution"
    description = (
        "Get TDCC's free weekly shareholding-level distribution (集保戶股權分散表) tier rows "
        "for one stock — a chip-concentration signal, public/free (distinct from the "
        "Sponsor-tier FinMind equivalent)."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetTdccShareholdingDistributionOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "tdcc_shareholding_distribution"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.TDCC_SHAREHOLDING_DISTRIBUTION,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=TdccShareholdingDistributionRow,
            key="rows",
        )
        return GetTdccShareholdingDistributionOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetTdccOffshoreFundNavOutput(_TdccRowsOutput):
    rows: tuple[TdccOffshoreFundNavRow, ...] = Field(default_factory=tuple)


class GetTdccOffshoreFundNavTool(BaseTool):
    """`get_tdcc_offshore_fund_nav` — wraps `Capability.TDCC_OFFSHORE_FUND_NAV` (境外基金淨值).

    Input is still `SymbolInput`, but `id` names TDCC's own offshore-fund code (e.g.
    `"000AMEEBEI"`), not a `Market`-scoped equity ticker — the same "borrow the shared
    `(symbol, market)` shape even for a non-equity id" precedent `chip_tools`'s convertible-bond
    tools already establish for `cb_id`."""

    name = "get_tdcc_offshore_fund_nav"
    description = (
        "Get TDCC's offshore-fund NAV rows for one fund by its TDCC fund code (境外基金淨值) — "
        "not a live quote; carries the fund's own reporting lag."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetTdccOffshoreFundNavOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.TDCC_OFFSHORE_FUND_NAV,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=TdccOffshoreFundNavRow,
            key="rows",
        )
        return GetTdccOffshoreFundNavOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetTdccEvotingScheduleOutput(_TdccRowsOutput):
    rows: tuple[TdccEvotingScheduleRow, ...] = Field(default_factory=tuple)


class GetTdccEvotingScheduleTool(BaseTool):
    """`get_tdcc_evoting_schedule` — wraps `Capability.TDCC_EVOTING_SCHEDULE` (股東會電子投票
    起訖日): a scheduling record only, never a vote tally/result."""

    name = "get_tdcc_evoting_schedule"
    description = (
        "Get TDCC's shareholder-meeting e-voting window (股東會電子投票起訖日) rows for one "
        "stock — meeting date + voting start/end, never a vote result."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetTdccEvotingScheduleOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "tdcc_evoting_schedule"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.TDCC_EVOTING_SCHEDULE,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=TdccEvotingScheduleRow,
            key="rows",
        )
        return GetTdccEvotingScheduleOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetTdccMonthlyCustodyAccountsOutput(_TdccRowsOutput):
    rows: tuple[TdccMonthlyCustodyAccountsRow, ...] = Field(default_factory=tuple)


class GetTdccMonthlyCustodyAccountsTool(BaseTool):
    """`get_tdcc_monthly_custody_accounts` — wraps
    `Capability.TDCC_MONTHLY_CUSTODY_ACCOUNTS` (集保帳戶統計; market-level, no per-symbol
    scope, the same `MarketOnlyInput` shape `chip_tools.GetSecuritiesTraderInfoTool` uses)."""

    name = "get_tdcc_monthly_custody_accounts"
    description = "Get TDCC's whole-market monthly custody-account statistics (集保帳戶統計)."
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetTdccMonthlyCustodyAccountsOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.TDCC_MONTHLY_CUSTODY_ACCOUNTS,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=TdccMonthlyCustodyAccountsRow,
            key="rows",
        )
        return GetTdccMonthlyCustodyAccountsOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class TdccRawQueryInput(BaseModel):
    """The catch-all escape hatch's input: TDCC's own raw dataset id (e.g. `"1-9"`) plus an
    optional symbol/fund-code column filter, never a `(capability, market)` pair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(
        min_length=1, description="TDCC OpenData dataset id, e.g. '1-9' (see TDCC's own catalog)."
    )
    symbol_column: str | None = Field(
        default=None,
        description="Upstream CSV header name to filter on, e.g. '證券代號'. Omit for no filter.",
    )
    symbol_value: str | None = Field(
        default=None, description="Value to match in `symbol_column`, if given."
    )
    limit: int = Field(default=50, ge=1, le=500)


class TdccRawQueryOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    currentness_caveat: str = Field(min_length=1)
    dataset_id: str
    header: tuple[str, ...] = Field(default_factory=tuple)
    rows: tuple[dict[str, str], ...] = Field(default_factory=tuple)
    truncated: bool = False


class GetTdccRawDatasetTool(BaseTool):
    """`get_tdcc_raw_dataset` — the generic "query any other TDCC OpenData dataset id" escape
    hatch, for the many real TDCC datasets this pass doesn't give a dedicated typed tool (e.g.
    id `1-9` 可借券賣出額度異動, id `1-16` 私募股份登錄, id `5-x` active-ETF/fund rosters).

    Deliberately **not** a `(capability, market)` router citizen: a raw dataset id is not a
    capability name any other caller could reuse, and TDCC's own catalog is large enough that
    minting a `Capability` enum value per dataset id would defeat the router's entire "callers
    name a capability, never a source" abstraction for what is really a single escape-hatch
    query shape. This mirrors `tools/web_search_tools.py`'s own precedent for "not a `(capability,
    market)` router citizen": that module bypasses `DataAccessRouter` via a thin `dataaccess/`
    gateway wrapping the adapter directly (`WebSearchGateway` -> `WebSearchAdapter`) because a
    query/URL isn't `(capability, symbol)`-shaped; a raw TDCC dataset id is the same kind of
    shape mismatch, so this tool follows the identical "thin gateway -> adapter, no router" path
    via `dataaccess.tdcc_query.TdccQueryGateway` rather than forcing an artificial capability
    mapping onto every current and future TDCC dataset id.
    """

    name = "get_tdcc_raw_dataset"
    description = (
        "Query any TDCC OpenData dataset by its raw dataset id (escape hatch for datasets "
        "without a dedicated tool) — returns the parsed CSV header + optionally-filtered rows, "
        "size-capped."
    )
    input_model: type[BaseModel] = TdccRawQueryInput
    output_model: type[BaseModel] = TdccRawQueryOutput
    data_class = DataClass.PUBLIC

    def __init__(self, gateway: TdccQueryGateway | None = None) -> None:
        self._gateway = gateway or TdccQueryGateway()

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(TdccRawQueryInput, args)
        result = await self._gateway.query(
            typed.dataset_id,
            tls_policy=ctx.tls_policy,
            symbol_column=typed.symbol_column,
            symbol_value=typed.symbol_value,
        )
        raw_header = result.payload.get("header")
        header: tuple[str, ...] = ()
        if isinstance(raw_header, list):
            untyped_header = cast("list[object]", raw_header)
            header = tuple(str(h) for h in untyped_header)
        raw_rows = result.payload.get("rows")
        all_rows: list[object] = (
            cast("list[object]", raw_rows) if isinstance(raw_rows, list) else []
        )
        truncated = len(all_rows) > typed.limit
        limited_rows = all_rows[: typed.limit]
        rows = tuple(
            {str(k): str(v) for k, v in cast("dict[object, object]", row).items()}
            for row in limited_rows
            if isinstance(row, dict)
        )
        return TdccRawQueryOutput(
            currentness_caveat=result.currentness_caveat,
            dataset_id=typed.dataset_id,
            header=header,
            rows=rows,
            truncated=truncated,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [
        (GetTdccSecuritiesInfoTool(router), False),
        (GetTdccPledgedCustodySharesTool(router), False),
        (GetTdccShareholdingDistributionTool(router), False),
        (GetTdccOffshoreFundNavTool(router), False),
        (GetTdccEvotingScheduleTool(router), False),
        (GetTdccMonthlyCustodyAccountsTool(router), False),
        (GetTdccRawDatasetTool(), False),
    ]


__all__ = [
    "GetTdccEvotingScheduleOutput",
    "GetTdccEvotingScheduleTool",
    "GetTdccMonthlyCustodyAccountsOutput",
    "GetTdccMonthlyCustodyAccountsTool",
    "GetTdccOffshoreFundNavOutput",
    "GetTdccOffshoreFundNavTool",
    "GetTdccPledgedCustodySharesOutput",
    "GetTdccPledgedCustodySharesTool",
    "GetTdccRawDatasetTool",
    "GetTdccSecuritiesInfoOutput",
    "GetTdccSecuritiesInfoTool",
    "GetTdccShareholdingDistributionOutput",
    "GetTdccShareholdingDistributionTool",
    "TdccRawQueryInput",
    "TdccRawQueryOutput",
    "build_registration_table",
]
