"""TDCC (Taiwan Depository & Clearing) rows."""

from __future__ import annotations

from datetime import date as date_

from pydantic import BaseModel, ConfigDict

from schemas.enums import Market


class TdccSecuritiesInfoRow(BaseModel):
    """`tdcc_securities_info` — one 證券基本資料 (securities master reference) row from TDCC
    OpenData id `1-1`: market/registrar/status/par-value reference facts, not a price."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    name: str
    listing_market: str  # upstream "市場別", e.g. "上市"/"上櫃"/"興櫃"/"終止上市櫃"
    transfer_agent: str  # upstream "股務單位"
    security_status: str  # upstream "證券狀態", e.g. "正常"
    scripless: str | None = None  # upstream "無面無實體" Y/blank
    currency: str
    par_value: float
    updated_date: date_


class TdccPledgedCustodyShareRow(BaseModel):
    """`tdcc_pledged_custody_shares` — one 集保股份保管明細 row from TDCC OpenData id `1-4`:
    the split between dispersed (分戶) vs specific/mandatory (集保) custody for one stock's
    total issued shares, as of the dump's single reported day."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    name: str
    as_of: date_
    dispersed_custody_shares: int  # upstream "分戶保管股數"
    specific_custody_shares: int  # upstream "特定保管股數"
    mandatory_custody_shares: int  # upstream "強制集保總股數"
    issued_thousand_shares: int  # upstream "發行仟股數"


class TdccShareholdingDistributionRow(BaseModel):
    """`tdcc_shareholding_distribution` — one 集保戶股權分散表 (shareholding-level distribution)
    tier row from TDCC OpenData id `1-5`. Public/free, distinct from FinMind's Sponsor-tier
    `shareholding_distribution` capability (`TaiwanStockHoldingSharesPer`) — same concept,
    different source, deliberately not the same `Capability` name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    holding_level: str  # upstream "持股分級" — a numbered tier code (1-17), not itself a range
    holders: int  # upstream "人數"
    shares: int  # upstream "股數"
    percent_of_custody: float  # upstream "占集保庫存數比例%"


class TdccOffshoreFundNavRow(BaseModel):
    """`tdcc_offshore_fund_nav` — one 境外基金淨值 (offshore-fund NAV) row from TDCC OpenData id
    `3-4`. Offshore funds are identified by TDCC's own fund code, not a `Market`-scoped equity
    symbol — `symbol` here is that fund code, `market` is fixed `Market.TW` (the distribution
    channel, not the fund's own domicile) purely to satisfy the shared `(capability, market)`
    routing shape every other TW-side capability in this registry uses."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # upstream "基金代碼" — TDCC's own offshore-fund code
    market: Market
    name: str
    nav_date: date_
    nav: float
    isin_code: str | None = None
    currency: str
    offshore_fund_institution: str  # upstream "境外基金機構"
    master_agent: str  # upstream "總代理機構"


class TdccEvotingScheduleRow(BaseModel):
    """`tdcc_evoting_schedule` — one 股東會電子投票起訖日 (shareholder-meeting e-voting window)
    row from TDCC OpenData id `6-2`. Reference/scheduling data, never a vote tally or result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    name: str
    fiscal_year_roc: str  # upstream "年度" — ROC year, kept as the upstream's own reference unit
    meeting_date: date_
    voting_start_date: date_
    voting_end_date: date_


class TdccMonthlyCustodyAccountsRow(BaseModel):
    """`tdcc_monthly_custody_accounts` — one whole-market monthly 集保帳戶統計 row from TDCC
    OpenData id `2-1`: new/closed/total custody-account counts for the month. Market-level, no
    per-symbol scope — the same "reference roster" shape as `MarketOnlyInput`-backed tools
    elsewhere in this registry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    as_of_month: str  # ISO "YYYY-MM", converted from upstream ROC "資料年月"
    new_accounts: int
    closed_accounts: int
    net_accounts: int  # upstream "總開戶數" — net change in the month, not a running total
    cumulative_accounts: int  # upstream "累計開戶數"
