"""Per-capability market-data tools — the FinMind/yfinance/TWSE-backed capabilities that have
a real adapter today (`get_price_quote`, `get_dividend_schedule`, `get_institutional_flow`,
`get_company_profile`) plus the sponsor-tier worked example (`get_chip_flows`).

Every tool here validates the router's `SourceResult.payload` into one of
`schemas.capabilities`' normalized per-capability models before returning it — never the raw
dict. Those models' field names were designed (Phase 1/2) to match the adapters' own row-mapper
output 1:1, so `model_validate` on each row is a straight typed pass-through, not a lossy
reshape.
"""

from __future__ import annotations

from config.settings import get_settings
from dataaccess.router import DataAccessRouter
from ingest.store import ParquetStore
from tools.base import Tool
from tools.market._common import HoldingStatus
from tools.market.advance_decline import (
    AdvanceDeclineBuckets,
    GetAdvanceDeclineInput,
    GetAdvanceDeclineOutput,
    GetAdvanceDeclineTool,
)
from tools.market.chip_flows import (
    GetChipFlowsOutput,
    GetChipFlowsTool,
    GetInstitutionalFlowOutput,
    GetInstitutionalFlowTool,
)
from tools.market.company_profile import GetCompanyProfileOutput, GetCompanyProfileTool
from tools.market.daily_signal import (
    DailySignalHighlight,
    DailySignalRow,
    GetDailySignalInput,
    GetDailySignalOutput,
    GetDailySignalTool,
)
from tools.market.dividends import (
    DividendCalendarEventRow,
    GetDividendCalendarInput,
    GetDividendCalendarOutput,
    GetDividendCalendarTool,
    GetDividendScheduleOutput,
    GetDividendScheduleTool,
)
from tools.market.etf_holdings import (
    EtfHoldingRankRow,
    EtfPositionEvent,
    EtfWeightRankRow,
    GetEtfsHoldingStockInput,
    GetEtfsHoldingStockOutput,
    GetEtfsHoldingStockTool,
)
from tools.market.etf_list import EtfListRow, GetEtfListInput, GetEtfListOutput, GetEtfListTool
from tools.market.etf_money_flow import (
    EtfMoneyFlowRow,
    GetEtfMoneyFlowInput,
    GetEtfMoneyFlowOutput,
    GetEtfMoneyFlowTool,
)
from tools.market.etf_report import (
    ActiveEtfHoldingWeightRow,
    ActiveEtfReportRow,
    GetActiveEtfReportInput,
    GetActiveEtfReportOutput,
    GetActiveEtfReportTool,
)
from tools.market.event_study import (
    EventChartRow,
    EventRow,
    GetEventStudyInput,
    GetEventStudyOutput,
    GetEventStudyTool,
)
from tools.market.heatmaps import (
    GetSectorConstituentsInput,
    GetSectorConstituentsOutput,
    GetSectorConstituentsTool,
    GetSectorHeatmapInput,
    GetSectorHeatmapOutput,
    GetSectorHeatmapTool,
    GetStockHeatmapInput,
    GetStockHeatmapOutput,
    GetStockHeatmapTool,
    SectorConstituentRow,
    SectorHeatmapPeriod,
    SectorHeatmapRow,
    SectorHeatmapSizeBy,
    StockHeatmapRow,
)
from tools.market.holder_accumulation import (
    GetHolderAccumulationInput,
    GetHolderAccumulationOutput,
    GetHolderAccumulationTool,
    HolderAccumulationRow,
    HolderThreshold,
    HolderWindow,
)
from tools.market.index_contribution import (
    GetIndexContributionInput,
    GetIndexContributionOutput,
    GetIndexContributionTool,
    IndexContributionRow,
)
from tools.market.momentum import (
    GetPriceMomentumInput,
    GetPriceMomentumOutput,
    GetPriceMomentumTool,
    MomentumPeriod,
    PriceMomentumRow,
)
from tools.market.quotes import (
    GetLivePriceQuoteOutput,
    GetLivePriceQuoteTool,
    GetPriceQuoteOutput,
    GetPriceQuoteTool,
    LivePriceTick,
)
from tools.market.sector_rotation import (
    GetSectorRotationInput,
    GetSectorRotationOutput,
    GetSectorRotationRrgInput,
    GetSectorRotationRrgOutput,
    GetSectorRotationRrgTool,
    GetSectorRotationTool,
    SectorRotationDayRow,
    SectorRrgSeries,
    SectorRrgWeekPoint,
)
from tools.market.strategy_watch import (
    GetStrategyWatchInput,
    GetStrategyWatchOutput,
    GetStrategyWatchTool,
)
from tools.market.symbols import (
    GetPriceHistoryInput,
    GetPriceHistoryOutput,
    GetPriceHistoryTool,
    GetSymbolDirectorySearchInput,
    GetSymbolDirectorySearchOutput,
    GetSymbolDirectorySearchTool,
    GetSymbolInfoInput,
    GetSymbolInfoOutput,
    GetSymbolInfoTool,
    GetSymbolQuotesInput,
    GetSymbolQuotesOutput,
    GetSymbolQuotesTool,
    SymbolQuoteRow,
)


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table.

    `get_company_profile` is a named immediate-core tool, built narrow here (see its
    class docstring). `get_price_quote` backs the basic price-quote read that's also part of the
    immediate-core set. Everything else here is a real, deferred capability.

    `get_etf_money_flow` (and, for its `revenue_yoy`/`margins` fields only, `get_company_profile`)
    read the ingest-owned Parquet store directly (see their class docstrings for why) rather than
    the router, so this function builds its own `ParquetStore` from `config.settings` here rather
    than the router this function otherwise only threads through. `get_advance_decline`,
    `get_index_contribution`, and `get_sector_heatmap` are the same store-reading family — see
    their own class docstrings.
    """
    store = ParquetStore(root=get_settings().parquet_root)
    tools: list[tuple[Tool, bool]] = [
        (GetCompanyProfileTool(router, store), True),
        (GetPriceQuoteTool(router), True),
        (GetLivePriceQuoteTool(router), False),
        (GetDividendScheduleTool(router), False),
        (GetInstitutionalFlowTool(router), False),
        (GetChipFlowsTool(router), False),
        (GetActiveEtfReportTool(router, store), False),
        (GetEtfMoneyFlowTool(store), False),
        (GetEtfsHoldingStockTool(store), False),
        (GetDailySignalTool(store), False),
        (GetAdvanceDeclineTool(store), False),
        (GetPriceMomentumTool(store), False),
        (GetHolderAccumulationTool(store), False),
        (GetIndexContributionTool(store), False),
        (GetSectorHeatmapTool(store), False),
        (GetSectorConstituentsTool(store), False),
        (GetStockHeatmapTool(store), False),
        (GetSectorRotationTool(store), False),
        (GetSectorRotationRrgTool(router), False),
        (GetSymbolInfoTool(store), False),
        (GetSymbolDirectorySearchTool(store), False),
        (GetPriceHistoryTool(store), False),
        (GetSymbolQuotesTool(store), False),
        (GetEtfListTool(store), False),
        (GetDividendCalendarTool(store), False),
        (GetStrategyWatchTool(store), False),
        (GetEventStudyTool(store, router), False),
    ]
    return tools


__all__ = [
    "ActiveEtfHoldingWeightRow",
    "ActiveEtfReportRow",
    "AdvanceDeclineBuckets",
    "DailySignalHighlight",
    "DailySignalRow",
    "DividendCalendarEventRow",
    "EtfHoldingRankRow",
    "EtfListRow",
    "EtfMoneyFlowRow",
    "EtfPositionEvent",
    "EtfWeightRankRow",
    "EventChartRow",
    "EventRow",
    "GetActiveEtfReportInput",
    "GetActiveEtfReportOutput",
    "GetActiveEtfReportTool",
    "GetAdvanceDeclineInput",
    "GetAdvanceDeclineOutput",
    "GetAdvanceDeclineTool",
    "GetChipFlowsOutput",
    "GetChipFlowsTool",
    "GetCompanyProfileOutput",
    "GetCompanyProfileTool",
    "GetDailySignalInput",
    "GetDailySignalOutput",
    "GetDailySignalTool",
    "GetDividendCalendarInput",
    "GetDividendCalendarOutput",
    "GetDividendCalendarTool",
    "GetDividendScheduleOutput",
    "GetDividendScheduleTool",
    "GetEtfListInput",
    "GetEtfListOutput",
    "GetEtfListTool",
    "GetEtfMoneyFlowInput",
    "GetEtfMoneyFlowOutput",
    "GetEtfMoneyFlowTool",
    "GetEtfsHoldingStockInput",
    "GetEtfsHoldingStockOutput",
    "GetEtfsHoldingStockTool",
    "GetEventStudyInput",
    "GetEventStudyOutput",
    "GetEventStudyTool",
    "GetHolderAccumulationInput",
    "GetHolderAccumulationOutput",
    "GetHolderAccumulationTool",
    "GetIndexContributionInput",
    "GetIndexContributionOutput",
    "GetIndexContributionTool",
    "GetInstitutionalFlowOutput",
    "GetInstitutionalFlowTool",
    "GetLivePriceQuoteOutput",
    "GetLivePriceQuoteTool",
    "GetPriceHistoryInput",
    "GetPriceHistoryOutput",
    "GetPriceHistoryTool",
    "GetPriceMomentumInput",
    "GetPriceMomentumOutput",
    "GetPriceMomentumTool",
    "GetPriceQuoteOutput",
    "GetPriceQuoteTool",
    "GetSectorConstituentsInput",
    "GetSectorConstituentsOutput",
    "GetSectorConstituentsTool",
    "GetSectorHeatmapInput",
    "GetSectorHeatmapOutput",
    "GetSectorHeatmapTool",
    "GetSectorRotationInput",
    "GetSectorRotationOutput",
    "GetSectorRotationRrgInput",
    "GetSectorRotationRrgOutput",
    "GetSectorRotationRrgTool",
    "GetSectorRotationTool",
    "GetStockHeatmapInput",
    "GetStockHeatmapOutput",
    "GetStockHeatmapTool",
    "GetStrategyWatchInput",
    "GetStrategyWatchOutput",
    "GetStrategyWatchTool",
    "GetSymbolDirectorySearchInput",
    "GetSymbolDirectorySearchOutput",
    "GetSymbolDirectorySearchTool",
    "GetSymbolInfoInput",
    "GetSymbolInfoOutput",
    "GetSymbolInfoTool",
    "GetSymbolQuotesInput",
    "GetSymbolQuotesOutput",
    "GetSymbolQuotesTool",
    "HolderAccumulationRow",
    "HolderThreshold",
    "HolderWindow",
    "HoldingStatus",
    "IndexContributionRow",
    "LivePriceTick",
    "MomentumPeriod",
    "PriceMomentumRow",
    "SectorConstituentRow",
    "SectorHeatmapPeriod",
    "SectorHeatmapRow",
    "SectorHeatmapSizeBy",
    "SectorRotationDayRow",
    "SectorRrgSeries",
    "SectorRrgWeekPoint",
    "StockHeatmapRow",
    "SymbolQuoteRow",
    "build_registration_table",
]
