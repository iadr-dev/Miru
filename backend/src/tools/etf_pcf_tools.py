"""ETF fund-info tool — wraps `sources/etf_pcf.py` via the router (`Capability.ETF_HOLDINGS`).

This is the fund-level metadata slice only (units outstanding, tracked index, manager company)
— per-constituent holdings/weights and NAV/IOPV remain a documented, unfilled gap in the
adapter itself (see its module docstring); this tool surfaces that gap honestly through
`EtfFundInfo`'s own optional fields rather than fabricating anything.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import EtfExpenseInfo, EtfFundInfo
from schemas.enums import Capability, DataClass
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


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [
        (GetEtfFundInfoTool(router), False),
        (GetEtfExpenseRatioTool(router), False),
    ]


__all__ = [
    "GetEtfExpenseRatioOutput",
    "GetEtfExpenseRatioTool",
    "GetEtfFundInfoOutput",
    "GetEtfFundInfoTool",
    "build_registration_table",
]
