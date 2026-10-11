"""Government-OpenAPI tools whose data shape genuinely isn't already covered by a FinMind-
routed capability.

TWSE/TPEX OpenAPI's EOD snapshots (`price_eod`) are already wired as same-capability priority-2
fallbacks behind FinMind in `config/source_priority.yaml` — `get_price_quote`
(`tools/market/`) already reaches them transparently through the router's own
fallback, so a second, TWSE/TPEX-specific tool would just be a redundant view over the same
capability and is deliberately not built here.

TAIFEX OpenAPI's `derivatives_oi` is different: no FinMind dataset serves this exact capability
name (FinMind's derivative datasets are modeled under `futures_daily`/`option_daily`/etc, a
distinct set of Capability values — see `sources/taifex_openapi.py`'s own docstring on why it
declares `DERIVATIVES_OI` rather than a new capability), so this is a genuinely new tool, not a
duplicate view.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, DataClass
from schemas.source import HealthStatus
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import SymbolInput


class DerivativesSettlementRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    trade_date: str | None = None
    contract_month: str | None = None
    settlement_price: float
    volume: int
    open_interest: int


class GetDerivativesSettlementOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    record: DerivativesSettlementRecord | None = None


class GetDerivativesSettlementTool(BaseTool):
    """`get_derivatives_settlement` — wraps `Capability.DERIVATIVES_OI` (TAIFEX OpenAPI's
    official daily futures settlement price + volume + open interest for one contract code)."""

    name = "get_derivatives_settlement"
    description = (
        "Get the official latest end-of-day settlement price, volume, and open interest for "
        "one TAIFEX futures/options contract code."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetDerivativesSettlementOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result = await self._router.fetch(
            Capability.DERIVATIVES_OI, typed.to_symbol(), tls_policy=ctx.tls_policy
        )
        raw_record = result.payload.get("record")
        record = (
            DerivativesSettlementRecord.model_validate(cast("dict[str, object]", raw_record))
            if isinstance(raw_record, dict)
            else None
        )
        return GetDerivativesSettlementOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            record=record,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [(GetDerivativesSettlementTool(router), False)]


__all__ = [
    "DerivativesSettlementRecord",
    "GetDerivativesSettlementOutput",
    "GetDerivativesSettlementTool",
    "build_registration_table",
]
