"""MOPS (公開資訊觀測站) disclosure-stream tools — material announcements + director/supervisor
shareholding (both per-symbol, live-fetched through the router) plus two whole-market feed
tools (material announcements + insider advance-transfer declarations) that read the
ingest-owned Parquet store instead — see
`GetMaterialAnnouncementFeedTool`/`GetInsiderTransferDeclarationTool` below for why those two
need a different read path than the per-symbol pair above them.
"""

from __future__ import annotations

from datetime import date as date_

from pydantic import BaseModel, ConfigDict, Field

from config.settings import get_settings
from dataaccess.router import DataAccessRouter
from ingest.jobs import INSIDER_TRANSFER_DECLARATION_DATASET, MATERIAL_ANNOUNCEMENTS_DATASET
from ingest.store import ParquetStore
from schemas.capabilities import (
    DirectorShareholding,
    InsiderTransferDeclarationRow,
    MaterialAnnouncement,
    MaterialAnnouncementRow,
    SelfAssessedEarningsRow,
)
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
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


class GetSelfAssessedEarningsInput(BaseModel):
    """A symbol + the ISO "YYYY-MM" monthly period to check + which exchange it lists on
    (`security_type`: "twse"/"tpex" — needed because `t138sb01`'s upstream form takes a market
    code per POST, not a symbol-derived lookup)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, description="Exchange-scoped symbol id, e.g. '2330'.")
    market: Market
    period: str = Field(description='ISO "YYYY-MM" — the monthly filing period to check.')
    security_type: str = Field(description='"twse" (上市) or "tpex" (上櫃).')


class GetSelfAssessedEarningsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    rows: tuple[SelfAssessedEarningsRow, ...] = Field(default_factory=tuple)


class GetSelfAssessedEarningsTool(BaseTool):
    """`get_self_assessed_earnings` — wraps `Capability.SELF_ASSESSED_EARNINGS` (MOPS 自結損益
    公告, `t138sb01`). Bounded per-symbol: the upstream form has no whole-history date-range
    query (only one market/period per POST), so this tool takes an explicit `period` — there is
    no "give me everything this company has ever filed" call. See `sources/mops.py`'s own
    `_fetch_self_assessed_earnings` docstring for the bounded-fetch reasoning."""

    name = "get_self_assessed_earnings"
    description = (
        "Get one stock's MOPS self-assessed/preliminary P&L (自結損益公告) filing for one "
        "monthly period, if the company filed one — a voluntary, unaudited figure, not a "
        "confirmed final result."
    )
    input_model: type[BaseModel] = GetSelfAssessedEarningsInput
    output_model: type[BaseModel] = GetSelfAssessedEarningsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "self_assessed_earnings"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetSelfAssessedEarningsInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.SELF_ASSESSED_EARNINGS,
            Symbol(id=typed.id, market=typed.market),
            tls_policy=ctx.tls_policy,
            model=SelfAssessedEarningsRow,
            key="rows",
            params={"period": typed.period, "security_type": typed.security_type},
        )
        return GetSelfAssessedEarningsOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


def _rows_from_store[T: BaseModel](model: type[T], raw_rows: list[dict[str, object]]) -> list[T]:
    """Same drop-storage-bookkeeping-columns-before-validating helper
    `tools/market_tools.py::_rows_from_store` already documents — duplicated here (not imported)
    since it's private to that module and this file has no other reason to import from it."""
    field_names = set(model.model_fields)
    return [
        model.model_validate({k: v for k, v in row.items() if k in field_names}) for row in raw_rows
    ]


def _resolve_feed_date(available_dates: tuple[date_, ...], requested: date_ | None) -> date_ | None:
    """The date to actually read: `requested` if it's one of `available_dates`, else the latest
    available date, else `None` (nothing ingested yet). Never guesses a date that was never
    actually written — `available_dates` is the ground truth from `list_partition_dates`."""
    if not available_dates:
        return None
    if requested is not None and requested in available_dates:
        return requested
    return max(available_dates)


class GetMaterialAnnouncementFeedInput(BaseModel):
    """A market + an optional trading day + an optional category filter; omitted trade_date
    means the latest date MOPS ingest has actually captured so far (see this tool's own
    docstring for why that's rarely "today" on a fresh deploy)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    trade_date: date_ | None = None
    category: str | None = None


class GetMaterialAnnouncementFeedOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    report_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    category_counts: dict[str, int] = Field(default_factory=dict)
    available_dates: tuple[date_, ...] = Field(default_factory=tuple)
    rows: tuple[MaterialAnnouncementRow, ...] = Field(default_factory=tuple)


class GetMaterialAnnouncementFeedTool(BaseTool):
    """`get_material_announcement_feed` — 重大訊息 whole-market feed for one day, read from the
    already-ingested `material_announcements` partition tree (`job_material_announcements`,
    `ingest/jobs.py`) — never a live fetch. Distinct from `get_material_announcements`
    (per-symbol, live-fetched, unchanged above): this is the whole-market list-view shape, with
    a derived `category` per row and a `category_counts` breakdown for a filter-chip UI.

    **History only exists from whenever ingest first ran.** MOPS's own upstream has no date
    parameter — every ingest run only ever captures "today's" dump — so `available_dates` may
    be a single day for a while after this feature ships, growing by one day per successful
    ingest run. This is not a bug; see `job_material_announcements`'s own docstring."""

    name = "get_material_announcement_feed"
    description = (
        "Get the whole-market MOPS material-announcement (重大訊息) feed for one day (defaults "
        "to the latest day MOPS ingest has captured), optionally filtered to one category "
        "bucket, with a per-category count breakdown."
    )
    input_model: type[BaseModel] = GetMaterialAnnouncementFeedInput
    output_model: type[BaseModel] = GetMaterialAnnouncementFeedOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "material_announcement_feed"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetMaterialAnnouncementFeedInput, args)
        market = typed.market.value
        available_dates = self._store.list_partition_dates(MATERIAL_ANNOUNCEMENTS_DATASET, market)
        report_date = _resolve_feed_date(available_dates, typed.trade_date)
        if report_date is None:
            return GetMaterialAnnouncementFeedOutput(
                market=market,
                data_available=False,
                unavailable_reason="MOPS 重大訊息 ingest has not captured any day yet for this "
                "market — history only starts accumulating once the daily ingest job first "
                "runs; there is no backfill (see the tool's own docstring).",
            )

        raw_rows = self._store.read_partition(MATERIAL_ANNOUNCEMENTS_DATASET, market, report_date)
        all_rows = _rows_from_store(MaterialAnnouncementRow, raw_rows)

        # Computed from the WHOLE day's rows, never the category-filtered subset below — a
        # caller with one category chip active must still see every other chip's real count,
        # not just the active one's, so the chip row itself never has to re-fetch to render.
        category_counts: dict[str, int] = {}
        for row in all_rows:
            category_counts[row.category] = category_counts.get(row.category, 0) + 1

        rows = all_rows
        if typed.category is not None:
            rows = [row for row in all_rows if row.category == typed.category]

        return GetMaterialAnnouncementFeedOutput(
            market=market,
            report_date=report_date,
            data_available=True,
            category_counts=category_counts,
            available_dates=available_dates,
            rows=tuple(rows),
        )


class GetInsiderTransferDeclarationInput(BaseModel):
    """A market + an optional trading day; omitted trade_date means the latest date MOPS ingest
    has actually captured so far."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    trade_date: date_ | None = None


class GetInsiderTransferDeclarationOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    report_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    available_dates: tuple[date_, ...] = Field(default_factory=tuple)
    rows: tuple[InsiderTransferDeclarationRow, ...] = Field(default_factory=tuple)


class GetInsiderTransferDeclarationTool(BaseTool):
    """`get_insider_transfer_declarations` — 內部人股權轉讓事前申報 whole-market feed for one
    day, read from the already-ingested `insider_transfer_declaration` partition tree
    (`job_insider_transfer_declaration`, `ingest/jobs.py`, `t187ap12_L` only — see
    `InsiderTransferDeclarationRow`'s own docstring for why `t187ap13_L` is out of scope) —
    never a live fetch. Same forward-only-history caveat as
    `GetMaterialAnnouncementFeedTool` — see its own docstring."""

    name = "get_insider_transfer_declarations"
    description = (
        "Get the whole-market MOPS insider advance-transfer-plan declaration (內部人股權轉讓事前"
        "申報) feed for one day (defaults to the latest day MOPS ingest has captured)."
    )
    input_model: type[BaseModel] = GetInsiderTransferDeclarationInput
    output_model: type[BaseModel] = GetInsiderTransferDeclarationOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "insider_transfer_declaration"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetInsiderTransferDeclarationInput, args)
        market = typed.market.value
        available_dates = self._store.list_partition_dates(
            INSIDER_TRANSFER_DECLARATION_DATASET, market
        )
        report_date = _resolve_feed_date(available_dates, typed.trade_date)
        if report_date is None:
            return GetInsiderTransferDeclarationOutput(
                market=market,
                data_available=False,
                unavailable_reason="MOPS 內部人股權轉讓事前申報 ingest has not captured any day "
                "yet for this market — history only starts accumulating once the daily ingest "
                "job first runs; there is no backfill (see the tool's own docstring).",
            )

        raw_rows = self._store.read_partition(
            INSIDER_TRANSFER_DECLARATION_DATASET, market, report_date
        )
        rows = _rows_from_store(InsiderTransferDeclarationRow, raw_rows)

        return GetInsiderTransferDeclarationOutput(
            market=market,
            report_date=report_date,
            data_available=True,
            available_dates=available_dates,
            rows=tuple(rows),
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table. `get_material_announcement_feed`/
    `get_insider_transfer_declarations` read the ingest-owned Parquet store directly (see their
    own class docstrings for why), so this function builds its own `ParquetStore` from
    `config.settings` here — same self-contained-construction pattern
    `market_tools.build_registration_table` already uses for its own store-reading tools,
    rather than changing this function's signature (its one call site, `tools/__init__.py`,
    only ever passes `router`)."""
    store = ParquetStore(root=get_settings().parquet_root)
    return [
        (GetMaterialAnnouncementsTool(router), False),
        (GetInsiderShareholdingTool(router), False),
        (GetMaterialAnnouncementFeedTool(store), False),
        (GetInsiderTransferDeclarationTool(store), False),
        (GetSelfAssessedEarningsTool(router), False),
    ]


__all__ = [
    "GetInsiderShareholdingOutput",
    "GetInsiderShareholdingTool",
    "GetInsiderTransferDeclarationInput",
    "GetInsiderTransferDeclarationOutput",
    "GetInsiderTransferDeclarationTool",
    "GetMaterialAnnouncementFeedInput",
    "GetMaterialAnnouncementFeedOutput",
    "GetMaterialAnnouncementFeedTool",
    "GetMaterialAnnouncementsOutput",
    "GetMaterialAnnouncementsTool",
    "GetSelfAssessedEarningsInput",
    "GetSelfAssessedEarningsOutput",
    "GetSelfAssessedEarningsTool",
    "build_registration_table",
]
