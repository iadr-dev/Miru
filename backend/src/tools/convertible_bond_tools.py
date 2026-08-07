"""Convertible-bond tools — the FinMind Convertible-Bond-category datasets, bundled into two
report-shaped tools rather than six near-duplicate single-dataset ones (per this registry's
bundling convention): `get_convertible_bond_profile` (roster + daily overview + put schedule —
the "what is this bond" static/slow-moving picture) and `get_convertible_bond_flow` (daily
trading + institutional flow + monthly custody analysis — the "how is it trading" picture).
All six wrapped datasets are Backer/Sponsor-tier, and `symbol` here is the bond's `cb_id`, never
an equity `stock_id`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import (
    ConvertibleBondDailyOverviewRow,
    ConvertibleBondDailyRow,
    ConvertibleBondInfoRow,
    ConvertibleBondInstitutionalFlowRow,
    ConvertibleBondMonthlyAnalysisRow,
    ConvertibleBondPutProvisionRow,
)
from schemas.enums import Capability, DataClass
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import DateRangeInput


class _CaveatOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    currentness_caveat: str = Field(min_length=1)


class GetConvertibleBondProfileOutput(_CaveatOutput):
    info: tuple[ConvertibleBondInfoRow, ...] = Field(default_factory=tuple)
    daily_overview: tuple[ConvertibleBondDailyOverviewRow, ...] = Field(default_factory=tuple)
    put_provision: tuple[ConvertibleBondPutProvisionRow, ...] = Field(default_factory=tuple)


class GetConvertibleBondProfileTool(BaseTool):
    """`get_convertible_bond_profile` — bundles `Capability.CB_INFO` + `Capability.
    CB_DAILY_OVERVIEW` + `Capability.CB_PUT_PROVISION` for one convertible bond (`id` = `cb_id`).
    """

    name = "get_convertible_bond_profile"
    description = (
        "Get one convertible bond's roster info, daily overview (conversion/put/early-"
        "redemption terms), and put(賣回權) schedule (Sponsor-tier; id is the bond's cb_id)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetConvertibleBondProfileOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "cb_profile"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        symbol = typed.to_symbol()
        params = typed.date_params()
        info_result, info_rows = await fetch_rows(
            self._router,
            Capability.CB_INFO,
            symbol,
            tls_policy=ctx.tls_policy,
            model=ConvertibleBondInfoRow,
            key="rows",
        )
        _overview_result, overview_rows = await fetch_rows(
            self._router,
            Capability.CB_DAILY_OVERVIEW,
            symbol,
            tls_policy=ctx.tls_policy,
            model=ConvertibleBondDailyOverviewRow,
            key="rows",
            params=params,
        )
        _put_result, put_rows = await fetch_rows(
            self._router,
            Capability.CB_PUT_PROVISION,
            symbol,
            tls_policy=ctx.tls_policy,
            model=ConvertibleBondPutProvisionRow,
            key="rows",
            params=params,
        )
        return GetConvertibleBondProfileOutput(
            currentness_caveat=info_result.currentness_caveat,
            info=info_rows,
            daily_overview=overview_rows,
            put_provision=put_rows,
        )


class GetConvertibleBondFlowOutput(_CaveatOutput):
    daily: tuple[ConvertibleBondDailyRow, ...] = Field(default_factory=tuple)
    institutional_flow: tuple[ConvertibleBondInstitutionalFlowRow, ...] = Field(
        default_factory=tuple
    )
    monthly_analysis: tuple[ConvertibleBondMonthlyAnalysisRow, ...] = Field(default_factory=tuple)


class GetConvertibleBondFlowTool(BaseTool):
    """`get_convertible_bond_flow` — bundles `Capability.CB_DAILY` + `Capability.
    CB_INSTITUTIONAL_FLOW` + `Capability.CB_MONTHLY_ANALYSIS` for one convertible bond."""

    name = "get_convertible_bond_flow"
    description = (
        "Get one convertible bond's EOD trading, three-major-institutional-investor flow, "
        "and monthly custody-balance analysis (Sponsor-tier; id is the bond's cb_id)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetConvertibleBondFlowOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "cb_flow"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        symbol = typed.to_symbol()
        params = typed.date_params()
        daily_result, daily_rows = await fetch_rows(
            self._router,
            Capability.CB_DAILY,
            symbol,
            tls_policy=ctx.tls_policy,
            model=ConvertibleBondDailyRow,
            key="rows",
            params=params,
        )
        _flow_result, flow_rows = await fetch_rows(
            self._router,
            Capability.CB_INSTITUTIONAL_FLOW,
            symbol,
            tls_policy=ctx.tls_policy,
            model=ConvertibleBondInstitutionalFlowRow,
            key="rows",
            params=params,
        )
        _monthly_result, monthly_rows = await fetch_rows(
            self._router,
            Capability.CB_MONTHLY_ANALYSIS,
            symbol,
            tls_policy=ctx.tls_policy,
            model=ConvertibleBondMonthlyAnalysisRow,
            key="rows",
            params=params,
        )
        return GetConvertibleBondFlowOutput(
            currentness_caveat=daily_result.currentness_caveat,
            daily=daily_rows,
            institutional_flow=flow_rows,
            monthly_analysis=monthly_rows,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [
        (GetConvertibleBondProfileTool(router), False),
        (GetConvertibleBondFlowTool(router), False),
    ]


__all__ = [
    "GetConvertibleBondFlowOutput",
    "GetConvertibleBondFlowTool",
    "GetConvertibleBondProfileOutput",
    "GetConvertibleBondProfileTool",
    "build_registration_table",
]
