"""The read-only market-data tools a research run may use next to web search.

Deep research answers from public web pages; for a question about a listed company, Miru's own
market data (revenue, institutional flows, valuation, ...) is often the better evidence. The model
may *request* data from this fixed list only - it never names an arbitrary tool or passes free-form
arguments. Every entry takes one `(symbol, market)` pair, so a request can never ask for the whole
market, and each is public data except where the caller's tier unlocks more (the normal entitlement
gate still applies; a locked result is simply left out).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

DataToolName = Literal[
    "get_company_profile",
    "get_stock_score",
    "get_institutional_flow",
    "get_margin_short",
    "get_month_revenue",
    "get_financial_statement",
    "get_valuation_ratios",
    "get_analyst_consensus",
    "get_dividend_policy_summary",
    "get_technical_indicators",
    "get_foreign_shareholding",
]


@dataclass(frozen=True)
class DataTool:
    name: DataToolName
    summary: str  # what the planner is told this returns
    label_zh: str
    label_en: str
    extra_args: tuple[tuple[str, str], ...] = ()  # fixed arguments besides `id` and `market`

    def label(self, locale: str) -> str:
        return self.label_en if locale.lower().startswith("en") else self.label_zh


DATA_TOOLS: Final[dict[DataToolName, DataTool]] = {
    tool.name: tool
    for tool in (
        DataTool(
            "get_company_profile",
            "company profile and financial snapshot: sector, size, margins, growth",
            "公司基本資料",
            "Company profile",
        ),
        DataTool(
            "get_stock_score",
            "Miru's composite analysis score with its factor breakdown (trend, quality, "
            "valuation, chips)",
            "綜合分析分數",
            "Composite analysis score",
        ),
        DataTool(
            "get_institutional_flow",
            "daily net buying/selling by foreign investors, investment trusts and dealers",
            "法人買賣超",
            "Institutional flows",
        ),
        DataTool(
            "get_margin_short",
            "margin-financing and short-selling balances",
            "融資融券",
            "Margin and short balances",
        ),
        DataTool(
            "get_month_revenue",
            "monthly revenue and year-over-year growth",
            "月營收",
            "Monthly revenue",
        ),
        DataTool(
            "get_financial_statement",
            "income statement lines by quarter",
            "損益表",
            "Income statement",
            extra_args=(("statement_type", "income_statement"),),
        ),
        DataTool(
            "get_valuation_ratios",
            "P/E, P/B and dividend-yield history",
            "評價指標",
            "Valuation ratios",
        ),
        DataTool(
            "get_analyst_consensus",
            "analyst coverage as attributed ratings and estimates",
            "分析師共識",
            "Analyst consensus",
        ),
        DataTool(
            "get_dividend_policy_summary",
            "dividend history, payout and yield",
            "股利政策",
            "Dividend policy",
        ),
        DataTool(
            "get_technical_indicators",
            "moving averages, momentum indicators and support/resistance levels",
            "技術指標",
            "Technical indicators",
        ),
        DataTool(
            "get_foreign_shareholding",
            "foreign ownership ratio over time",
            "外資持股",
            "Foreign shareholding",
        ),
    )
}


def planner_catalog() -> str:
    """The list shown to the planner, one tool per line."""
    return "\n".join(f"- {tool.name}: {tool.summary}" for tool in DATA_TOOLS.values())


__all__ = ["DATA_TOOLS", "DataTool", "DataToolName", "planner_catalog"]
