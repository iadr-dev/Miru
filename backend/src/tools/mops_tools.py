"""MOPS (公開資訊觀測站) disclosure-stream tools — material announcements + director/supervisor
shareholding. Both wrap `sources/mops.py`'s two datasets via the router (`Capability.
CORPORATE_ACTIONS`/`Capability.SHAREHOLDING`); `MopsAdapter.fetch()` only ever serves
`Market.TW`, so both tools inherit that restriction implicitly through the router rather than
re-declaring it in the tool.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import DirectorShareholding, MaterialAnnouncement
from schemas.enums import Capability, DataClass
from schemas.source import HealthStatus
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import SymbolInput


class GetMaterialAnnouncementsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    announcements: tuple[MaterialAnnouncement, ...] = Field(default_factory=tuple)


class GetMaterialAnnouncementsTool(BaseTool):
    """`get_material_announcements` — wraps `Capability.CORPORATE_ACTIONS` (MOPS 重大訊息).

    `MopsAdapter` serves the *current whole-market dump* filtered client-side to one company —
    there is no date-range param upstream, so this tool's input is a bare `SymbolInput`, not
    `DateRangeInput` (a date field here would silently do nothing, which this registry never
    does deliberately)."""

    name = "get_material_announcements"
    description = (
        "Get today's MOPS material-announcement (重大訊息) disclosure rows for one stock — "
        "contract/M&A/litigation/guidance/庫藏股/減增資/澄清-type filings, as currently published."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetMaterialAnnouncementsOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.CORPORATE_ACTIONS,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=MaterialAnnouncement,
            key="announcements",
        )
        return GetMaterialAnnouncementsOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            announcements=rows,
        )


class GetInsiderShareholdingOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    holdings: tuple[DirectorShareholding, ...] = Field(default_factory=tuple)


class GetInsiderShareholdingTool(BaseTool):
    """`get_insider_shareholding` — wraps `Capability.SHAREHOLDING` (MOPS 董監事持股餘額): per-
    person current/at-election shares + pledged-shares ratio (a risk flag when high). Named
    distinctly from the design's broader `get_insider_activity` (which also covers 內部人持股
    轉讓事前申報, a separate MOPS filing this adapter does not cover) — this tool is the
    董監事持股 slice only, honestly scoped to what `sources/mops.py` actually serves."""

    name = "get_insider_shareholding"
    description = (
        "Get the current-month MOPS director/supervisor shareholding (董監事持股餘額) rows for "
        "one stock, including pledged-shares ratio (risk flag)."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetInsiderShareholdingOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "insider_shareholding"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.SHAREHOLDING,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=DirectorShareholding,
            key="holdings",
        )
        return GetInsiderShareholdingOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            holdings=rows,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [
        (GetMaterialAnnouncementsTool(router), False),
        (GetInsiderShareholdingTool(router), False),
    ]


__all__ = [
    "GetInsiderShareholdingOutput",
    "GetInsiderShareholdingTool",
    "GetMaterialAnnouncementsOutput",
    "GetMaterialAnnouncementsTool",
    "build_registration_table",
]
