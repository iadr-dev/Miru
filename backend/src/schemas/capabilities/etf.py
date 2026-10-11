"""ETF fund info, NAV, AUM and returns."""

from __future__ import annotations

from datetime import date as date_

from pydantic import BaseModel, ConfigDict

from schemas.enums import Market


class EtfFundInfo(BaseModel):
    """`etf_holdings` (fund-level slice) — issuer/units-outstanding/tracked-index reference
    data for a listed ETF. Does **not** carry per-constituent weights (issuer PCF holdings
    remain an unfilled gap) — this is fund-level metadata only, refreshed on the issuer's
    reporting cadence, not a live figure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    fund_name: str
    fund_type: str  # 基金類型 raw string, e.g. "國內成分證券主動式交易所交易基金(股票)"
    tracked_index: str | None = None
    units_outstanding: int | None = None
    listed_date: date_ | None = None  # 上市日期 (exchange listing)
    inception_date: date_ | None = None  # 成立日期 (fund establishment) — distinct from listed_date
    manager_name: str | None = None  # 基金經理人 (the manager person)
    manager_company: str | None = None  # 投信公司 — None unless a clean company field is sourced
    custodian_bank: str | None = None  # 保管機構


class EtfThemeTags(BaseModel):
    """`etf_theme_tags` — TWSE's own "ETF e添富" (ETFortune) 主題/因子 (theme/factor)
    classification for one listed ETF (e.g. 高股息/科技主題型/公司治理及ESG) — a curated editorial
    tag set distinct from this codebase's AI-researched stock-concept themes
    (`analytics/themes`). Zero, one, or several tags per ETF; `tags` is empty (never `None`)
    when this ETF has no 主題/因子 assigned on the source page.

    `holder_count_wan` (受益人次, in 萬人/10-thousands) is scraped from the SAME page — the
    microsite's own beneficiary-count stat, refreshed on its own cadence; `None` when the page
    doesn't carry the figure for this symbol (never fabricated)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    tags: tuple[str, ...] = ()
    holder_count_wan: float | None = None


class EtfExpenseInfo(BaseModel):
    """`etf_expense_ratio` — internal-fee (內扣費用) disclosure for one fund/ETF, sourced from
    SITCA's (投信投顧公會) monthly fee-rate report. `management_fee_pct`/`custody_fee_pct` are the
    two disclosed line items; a true total-expense-ratio (內扣總費用率, TER) is approximated as
    their sum via `total_expense_ratio_pct_approx` and DOES NOT include other operating costs, so
    it is a floor, not the audited TER — the caveat says so. Any field is `None` when SITCA does
    not disclose it for this fund (never fabricated)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_ | None = None  # the disclosure month SITCA reported
    management_fee_pct: float | None = None  # 經理費 (%)
    custody_fee_pct: float | None = None  # 保管費 (%)
    total_expense_ratio_pct_approx: float | None = None  # 經理費 + 保管費 (approx TER floor)
    custodian_bank: str | None = None  # 保管機構


class EtfNavRow(BaseModel):
    """`etf_nav` — one ETF's premium/discount-to-NAV snapshot, sourced from TWSE MIS's free,
    keyless, whole-market `all_etf.txt` feed (issuer-published estimated NAV, relayed by TWSE —
    not TWSE's own calculation; see the upstream page's own disclaimer). `premium_discount_pct`
    is TWSE's own published 預估折溢價幅度 figure (`(close_price - estimated_nav) /
    estimated_nav * 100`, confirmed live against several symbols), passed through as-is rather
    than recomputed here, so this row never silently diverges from what investors see on the
    source page. `prior_day_nav` is the fund's last confirmed (non-estimated) NAV — the
    estimated NAV is provisional/intraday and can be revised by the issuer.

    Note: units_outstanding is a leading indicator of unusual creation/redemption activity, not
    itself a premium/discount signal — surfaced for completeness since the upstream feed already
    carries it in the same row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    fund_name: str
    units_outstanding: int | None = None  # 已發行受益權單位數
    units_outstanding_change: int | None = None  # 與前日已發行受益單位差異數
    close_price: float | None = None  # 成交價
    estimated_nav: float | None = None  # 投信或總代理人預估淨值
    premium_discount_pct: float | None = None  # 預估折溢價幅度 (%), TWSE's own published figure
    prior_day_nav: float | None = None  # 前一營業日單位淨值


class EtfAumRow(BaseModel):
    """`etf_aum` — one listed TW ETF's total net assets (資產規模). Derived in the ingest layer
    from `EtfNavRow.units_outstanding x prior_day_nav` (TWSE's bulk feed, already ingested as
    `etf_nav`); Yahoo's `totalAssets` (`sources/yfinance/analyst.py::_etf_aum`, per-symbol,
    paced — same accepted exception `ingest/us_jobs.py::job_us_fundamentals` documents) is only
    the fallback for an ETF whose NAV row lacks either input. Yahoo was the sole source before:
    its throttling and missing bond-ETF coverage left a third of the roster without a row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    aum: float | None = None  # 資產規模 (NTD), None when neither source has a figure


class EtfTrailingReturns(BaseModel):
    """`etf_trailing_returns` — one listed ETF's price-return over 6 fixed trailing horizons
    (1個月/1季/半年/1年/3年/5年), computed from ONE split/dividend-adjusted price history fetch
    (`yfinance.Ticker.history(period="5y", auto_adjust=True)`) — never yfinance's own `.info`
    fields (`ytdReturn`/`threeYearAverageReturn`/`fiveYearAverageReturn`), which mix inconsistent
    units (percent vs. fraction, confirmed live) and don't cover 1M/1Q/6M/1Y at all. Computing
    every horizon the same way over the same already-adjusted series means the six numbers are
    genuinely comparable to each other, not stitched from two different methodologies.

    `auto_adjust=True` retroactively folds every split/dividend into the historical close
    series — this is deliberately NOT the raw `Capability.PRICE_EOD` bars this codebase ingests
    elsewhere (those are unadjusted; see `get_fund_comparison`'s own dividend-reinvestment
    simulation, built for exactly that gap, but which itself broke on 0050's real 2025-06-18
    4-for-1 split precisely because the ingested bars are unadjusted — this capability sidesteps
    that whole class of bug by reading Yahoo's pre-adjusted series instead).

    A horizon is `None` (never fabricated) when the 5-year history window doesn't reach back
    far enough for that horizon (e.g. a fund listed 2 years ago has no real 5-year return)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    return_1m_pct: float | None = None
    return_1q_pct: float | None = None
    return_6m_pct: float | None = None
    return_1y_pct: float | None = None
    return_3y_pct: float | None = None
    return_5y_pct: float | None = None
