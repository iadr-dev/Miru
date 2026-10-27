"""Financial statements (income, balance sheet, cash flow) from the MOPS summary reports.

MOPS answers, for any past period, one report per market and statement with the headline lines of
every company that filed: `https://mopsov.twse.com.tw/mops/web/ajax_<report>` (form POST with
`TYPEK` sii|otc|rotc, the ROC `year` and the `season` 01-04; UTF-8 HTML, thousands of TWD):
`t163sb04` comprehensive income, `t163sb05` balance sheet, `t163sb20` cash flow. Each page holds
one table per industry template (general, bank, financial holding, securities, insurance, other),
told apart by their columns.

These are the AGGREGATE lines, not the full XBRL statement FinMind carries (about 60 balance-sheet
and 30 cash-flow detail lines per company): each mapped line gets FinMind's `item` and
`origin_name`, so a reader that asks for `Revenue`, `TotalAssets` or
`CashFlowsFromOperatingActivities` is served unchanged, and a reader that needs a detail line
(cash, borrowings, capex, depreciation) finds it missing and degrades as it already does for a
company FinMind does not cover. Lines FinMind has no `item` for are left out.

Conventions, matched to FinMind's stored rows:
- unit: TWD (the report is in thousands; EPS is per share and unscaled);
- income statement: the SINGLE QUARTER (FinMind's), while the report is year-to-date. Q1 is the
  report itself; Q2-Q4 subtract the company's latest earlier filing of the year (a company that
  files only half-yearly and annually gets the second half as its Q4, as in FinMind), and a
  company with no earlier filing keeps the year-to-date figure. EPS is the one line that is not
  additive (the share count moves), so a derived EPS is an approximation: it is exact in Q1, and
  from Q2 on its `origin_name` says it was derived;
- balance sheet: the position at the period end;
- cash flow: year-to-date, as FinMind stores it (Q4 is the full year; beginning cash is the
  year's opening balance);
- the row's date is the period end; a quarter end is the only date that carries rows.

Complete from the day after the last statutory deadline of any category (financial holdings,
Q1 5/30, Q2 8/31, Q3 11/29, annual 3/31): a partition with rows is not re-fetched, so serving a
half-filed quarter would freeze it half-filed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as date_
from typing import Final

import structlog

from schemas.enums import Capability
from sources.exchange_whole_market._common import TAIWAN_MARKET, ExchangeFetchError, Row

logger = structlog.get_logger(__name__)

HOST: Final = "mopsov.twse.com.tw"
ENCODING: Final = "utf-8"
MARKETS: Final = (("sii", "listed", "上市"), ("otc", "OTC", "上櫃"), ("rotc", "emerging", "興櫃"))
REQUIRED_MARKETS: Final = frozenset({"sii", "otc"})
_ROC_OFFSET: Final = 1911
_THOUSAND: Final = 1000.0
_SEASON_WORDS: Final = {1: "第一季", 2: "第二季", 3: "第三季", 4: "第四季"}
# Quarter end (month, day) -> season.
_QUARTER_ENDS: Final = {(3, 31): 1, (6, 30): 2, (9, 30): 3, (12, 31): 4}
# The latest statutory deadline of any category, per season (financial holdings); annual is
# due 3/31 of the next year.
_DEADLINES: Final = {1: (5, 30), 2: (8, 31), 3: (11, 29), 4: (3, 31)}
_NO_DATA_MARKER: Final = "查詢無資料"
_PARENT_EQUITY: Final = "歸屬於母公司業主之權益合計"  # FinMind's label for the parent's equity
_TITLE: Final = re.compile(r"<h2>(.*?)</h2>", re.DOTALL)
_TABLE: Final = re.compile(r"<table class='hasBorder'[^>]*>(.*?)</table>", re.DOTALL)
_HEAD_ROW: Final = re.compile(r"<tr class='tblHead'>(.*?)</tr>", re.DOTALL)
_DATA_ROW: Final = re.compile(r"<tr class='(?:even|odd)'>(.*?)</tr>", re.DOTALL)
_CELL: Final = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.DOTALL)
_TAG: Final = re.compile(r"<[^>]*>|\s+")
_MISSING: Final = frozenset({"", "-", "--"})


@dataclass(frozen=True)
class StatementPeriod:
    """The reporting quarter a run date (its end) stands for."""

    year: int
    season: int
    period_end: date_

    @property
    def roc_year(self) -> int:
        return self.year - _ROC_OFFSET

    @property
    def label(self) -> str:
        return f"{self.year}Q{self.season}"


@dataclass(frozen=True)
class Line:
    """One FinMind line: its `item`, its `origin_name`, and whether the report prints it in
    thousands of TWD (`scale`) or unscaled (EPS)."""

    item: str
    origin_name: str
    scale: float = _THOUSAND
    # The `origin_name` once the figure is a difference of two year-to-date reports and the
    # difference is not exact (EPS); `None` for the lines where it is.
    derived_origin_name: str | None = None


@dataclass(frozen=True)
class Report:
    endpoint: str
    statement_type: str
    # The report is cumulative within the year and FinMind stores the quarter alone, so Q2-Q4
    # subtract the previous season's report. (The cash flow is cumulative in both.)
    single_quarter: bool
    lines: Mapping[str, tuple[Line, ...]]


def _one(item: str, origin: str | None = None, *, scale: float = _THOUSAND) -> tuple[Line, ...]:
    return (Line(item, origin or "", scale),)


def _named(lines: dict[str, tuple[Line, ...]]) -> dict[str, tuple[Line, ...]]:
    """Fill a line's `origin_name` with the report label it came from when none was given."""
    return {
        label: tuple(
            Line(ln.item, ln.origin_name or label, ln.scale, ln.derived_origin_name) for ln in v
        )
        for label, v in lines.items()
    }


_INCOME: Final = _named(
    {
        # general industry
        "營業收入": _one("Revenue"),
        "營業成本": _one("CostOfGoodsSold"),
        "原始認列生物資產及農產品之利益（損失）": _one(
            "GainsOnInitialRecognitionOfBiologicalAssetsForCurrentPeriod"
        ),
        "生物資產當期公允價值減出售成本之變動利益（損失）": _one(
            "GainsOnChangesInFairValueLessCosts2SellOfBiologicalAssetsForCurrentPeriod"
        ),
        "營業毛利（毛損）": _one("GrossProfit"),
        "未實現銷貨（損）益": _one("UnrealizedGain"),
        "已實現銷貨（損）益": _one("RealizedGain"),
        # FinMind keeps the net-of-unrealized figure only where there is no plain gross profit;
        # the first line printed for an item wins, and this label comes after the plain one.
        "營業毛利（毛損）淨額": _one("GrossProfit"),
        "營業費用": _one("OperatingExpenses"),
        "其他收益及費損淨額": _one("OTHNOE"),
        "營業利益（損失）": _one("OperatingIncome"),
        "營業外收入及支出": _one("TotalNonoperatingIncomeAndExpense"),
        "稅前淨利（淨損）": _one("PreTaxIncome"),
        "所得稅費用（利益）": _one("TAX"),
        "繼續營業單位本期淨利（淨損）": _one("IncomeFromContinuingOperations"),
        "停業單位損益": _one("IncomeLossFromDiscontinuedOperation"),
        "本期淨利（淨損）": _one("IncomeAfterTaxes"),
        "其他綜合損益（淨額）": _one("OtherComprehensiveIncome"),
        "本期綜合損益總額": _one("TotalConsolidatedProfitForThePeriod"),
        "淨利（淨損）歸屬於母公司業主": _one("EquityAttributableToOwnersOfParent"),
        "淨利（淨損）歸屬於非控制權益": _one("NoncontrollingInterests"),
        "綜合損益總額歸屬於非控制權益": _one(
            "ComprehensiveIncomeConsolidatedNetIncomeAttributedNonControllingInterest"
        ),
        "基本每股盈餘（元）": (Line("EPS", "基本每股盈餘", 1.0, "基本每股盈餘（累計差額推算）"),),
        # bank
        "利息淨收益": _one("NetInterestIncome"),
        "利息以外淨損益": _one("NetNonInterestIncome"),
        "呆帳費用、承諾及保證責任準備提存": _one("BadDebts"),
        "繼續營業單位稅前淨利（淨損）": _one("IncomeBeforeTaxFromContinuingOperations"),
        "繼續營業單位本期稅後淨利（淨損）": _one("IncomeFromContinuingOperations"),
        "本期稅後淨利（淨損）": _one("IncomeAfterTax"),
        "其他綜合損益（稅後）": _one("OtherComprehensiveIncome"),
        "淨利（損）歸屬於母公司業主": _one("EquityAttributableToOwnersOfParent"),
        "淨利（損）歸屬於非控制權益": _one("NoncontrollingInterests"),
        # financial holding
        "淨收益": _one("Revenue"),
        "利息以外淨收益": _one("NetNonInterestIncome"),
        "保險負債準備淨變動": _one("NetChangeInProvisionsForInsuranceLiabilities"),
        "繼續營業單位稅前損益": _one("PreTaxIncome"),
        "所得稅（費用）利益": _one("TAX"),
        "本期其他綜合損益（稅後淨額）": _one("OtherComprehensiveIncomeAfterTaxThePeriod"),
        # securities
        "收益": _one("Income"),
        "支出及費用": _one("Expense"),
        "營業利益": _one("OperatingIncome"),
        "營業外損益": _one("TotalNonbusinessIncome"),
        # insurance
        "繼續營業單位稅前純益（純損）": _one("PreTaxIncome"),
        "繼續營業單位本期純益（純損）": _one("IncomeFromContinuingOperations"),
        # other
        "收入": _one("Revenue"),
        "支出": _one("CostOfGoodsSold"),
        "其他綜合損益": _one("OtherComprehensiveIncome"),
    }
)

_BALANCE: Final = _named(
    {
        "流動資產": _one("CurrentAssets", "流動資產合計"),
        "非流動資產": _one("NoncurrentAssets", "非流動資產合計"),
        "資產總計": _one("TotalAssets", "資產總額"),
        "資產總額": _one("TotalAssets"),
        "流動負債": _one("CurrentLiabilities", "流動負債合計"),
        "非流動負債": _one("NoncurrentLiabilities", "非流動負債合計"),
        "負債總計": _one("Liabilities", "負債總額"),
        "負債總額": _one("Liabilities"),
        "股本": _one("CapitalStock", "股本合計"),
        "資本公積": _one("CapitalSurplus", "資本公積合計"),
        "保留盈餘": _one("RetainedEarnings", "保留盈餘合計"),
        "保留盈餘（或累積虧損）": _one("RetainedEarnings", "保留盈餘合計"),
        "其他權益": _one("OtherEquityInterest", "其他權益合計"),
        _PARENT_EQUITY: _one("EquityAttributableToOwnersOfParent"),
        "歸屬於母公司業主權益合計": _one("EquityAttributableToOwnersOfParent", _PARENT_EQUITY),
        "歸屬於母公司業主之權益": _one("EquityAttributableToOwnersOfParent", _PARENT_EQUITY),
        "非控制權益": _one("NoncontrollingInterests"),
        "權益總計": _one("Equity", "權益總額"),
        "權益總額": _one("Equity"),
        "現金及約當現金": _one("CashAndCashEquivalents"),
    }
)

_CASH_FLOW: Final = _named(
    {
        "營業活動之淨現金流入（流出）": (
            Line("CashFlowsFromOperatingActivities", "營業活動之淨現金流入（流出）"),
            Line("NetCashInflowFromOperatingActivities", "營業活動之淨現金流入"),
        ),
        "投資活動之淨現金流入（流出）": _one("CashProvidedByInvestingActivities"),
        "籌資活動之淨現金流入（流出）": _one("CashFlowsProvidedFromFinancingActivities"),
        "本期現金及約當現金增加（減少）數": _one("CashBalancesIncrease"),
        "期初現金及約當現金餘額": _one("CashBalancesBeginningOfPeriod"),
        "期末現金及約當現金餘額": _one("CashBalancesEndOfPeriod"),
    }
)

REPORTS: Final[dict[Capability, Report]] = {
    Capability.FINANCIALS: Report("t163sb04", "income_statement", True, _INCOME),
    Capability.BALANCE_SHEET: Report("t163sb05", "balance_sheet", False, _BALANCE),
    Capability.CASH_FLOW_STATEMENT: Report("t163sb20", "cash_flow", False, _CASH_FLOW),
}

# code -> report label -> printed figure (None for '--')
type Table = dict[str, dict[str, float | None]]


def period_for(run_date: date_) -> StatementPeriod | None:
    """The quarter ending on `run_date`; `None` for any other day (no statement has that date)."""
    season = _QUARTER_ENDS.get((run_date.month, run_date.day))
    return None if season is None else StatementPeriod(run_date.year, season, run_date)


def deadline(period: StatementPeriod) -> date_:
    """The last day any company (a financial holding, the latest category) may file the quarter."""
    month, day = _DEADLINES[period.season]
    year = period.year + 1 if period.season == _QUARTER_ENDS[(12, 31)] else period.year
    return date_(year, month, day)


def is_complete(period: StatementPeriod, today: date_) -> bool:
    return today > deadline(period)


def earlier_seasons(period: StatementPeriod) -> list[int]:
    """The seasons of the same year before `period`'s, nearest first."""
    return list(range(period.season - 1, 0, -1))


def has_unresolved(current: Table, earlier: list[Table]) -> bool:
    """Whether some company of `current` has no earlier filing in `earlier` yet."""
    return any(not any(code in table for table in earlier) for code in current)


def form(market_dir: str, roc_year: int, season: int) -> dict[str, str]:
    return {
        "encodeURIComponent": "1",
        "step": "1",
        "firstin": "1",
        "off": "1",
        "isQuery": "Y",
        "TYPEK": market_dir,
        "year": str(roc_year),
        "season": f"{season:02d}",
    }


def endpoint_url(report: Report) -> str:
    return f"https://{HOST}/mops/web/ajax_{report.endpoint}"


def parse_page(html: str, market_dir: str, season: int, report: Report) -> Table:
    """Every company of every template on the page, by its printed figures. An unfiled period is
    the page's own "查詢無資料" (an empty table); a page that is neither that nor the report of
    the requested market and season (a block or challenge page, a changed layout) is an error,
    never an empty period."""
    label = f"{report.endpoint} {market_dir} season {season}"
    title = _TITLE.search(html)
    market_word = next(word for code, _, word in MARKETS if code == market_dir)
    if title is None:
        if _NO_DATA_MARKER in html:
            return {}
        raise ExchangeFetchError(f"{label}: not a report page")
    heading = _TAG.sub("", title.group(1))
    if market_word not in heading or _SEASON_WORDS[season] not in heading:
        raise ExchangeFetchError(f"{label}: answered {heading!r}")
    table: Table = {}
    for body in _TABLE.findall(html):
        _read_template(body, table)
    if not table:
        raise ExchangeFetchError(f"{label}: the page has no company rows")
    return table


def _read_template(body: str, into: Table) -> None:
    head = _HEAD_ROW.search(body)
    if head is None:
        return
    labels = [_TAG.sub("", c) for c in _CELL.findall(head.group(1))]
    if not labels or labels[0] != "公司代號":
        return
    for match in _DATA_ROW.finditer(body):
        cells = [_TAG.sub("", c) for c in _CELL.findall(match.group(1))]
        if len(cells) != len(labels) or not cells[0]:
            continue
        into[cells[0]] = {
            lab: _number(cell) for lab, cell in zip(labels[2:], cells[2:], strict=True)
        }


def _number(text: str) -> float | None:
    cleaned = text.replace(",", "")
    if cleaned in _MISSING:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def statement_rows(
    report: Report, period: StatementPeriod, current: Table, earlier: list[Table]
) -> list[Row]:
    """FinMind-shaped rows for one market. For a single-quarter report `earlier` holds the
    year-to-date tables of the earlier seasons, nearest first: each company is reduced by the
    figures of the nearest one it appears in."""
    rows: list[Row] = []
    for code, printed in current.items():
        rows.extend(_company_rows(report, period, code, printed, _base_for(report, code, earlier)))
    return rows


def _company_rows(
    report: Report,
    period: StatementPeriod,
    code: str,
    printed: dict[str, float | None],
    base: dict[str, float | None] | None,
) -> list[Row]:
    rows: list[Row] = []
    emitted: set[str] = set()
    for label, figure in printed.items():
        if figure is None:
            continue
        for line in report.lines.get(label, ()):
            if line.item in emitted:
                continue
            emitted.add(line.item)
            derived = base is not None and line.derived_origin_name is not None
            rows.append(
                {
                    "symbol": code,
                    "market": TAIWAN_MARKET,
                    "period_end": period.period_end.isoformat(),
                    "statement_type": report.statement_type,
                    "item": line.item,
                    "origin_name": line.derived_origin_name if derived else line.origin_name,
                    "value": (figure - ((base or {}).get(label) or 0.0)) * line.scale,
                }
            )
    return rows


def _base_for(report: Report, code: str, earlier: list[Table]) -> dict[str, float | None] | None:
    """The year-to-date figures to subtract for `code`: those of its nearest earlier filing."""
    if not report.single_quarter:
        return None
    return next((table[code] for table in earlier if code in table), None)


__all__ = [
    "ENCODING",
    "HOST",
    "MARKETS",
    "REPORTS",
    "REQUIRED_MARKETS",
    "Report",
    "StatementPeriod",
    "Table",
    "earlier_seasons",
    "endpoint_url",
    "form",
    "has_unresolved",
    "is_complete",
    "parse_page",
    "period_for",
    "statement_rows",
]
