"""The `_DATASETS` registry — one `_DatasetConfig` per TDCC OpenData dataset this package
serves, each mapped to the `Capability` it backs.

Every dataset id/column name below was verified live against the real
`https://opendata.tdcc.com.tw/getOD.ashx?id=<dataset_id>` endpoint (a `curl` against the real
upstream, not a third party's guess) — see each `_map_*` function's docstring for the exact
upstream header it reads.
"""

from __future__ import annotations

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol

from ._shared import _DatasetConfig, _roc_yearmonth_to_iso, _to_float, _to_int, _yyyymmdd_to_date


def _map_securities_info(rows: list[dict[str, str]], sym: Symbol | None) -> list[dict[str, object]]:
    """Dataset `1-1` — 證券基本資料. Header: 證券代號,證券名稱,市場別,股務單位,停止過戶領回,
    證券狀態,無面無實體,歸戶領回,限制部分帳簿劃撥功能註記,發行幣別,每股面額(元),更新日期."""
    out: list[dict[str, object]] = []
    for row in rows:
        symbol_id = row.get("證券代號", "").strip()
        market = sym.market if sym is not None else Market.TW
        updated = _yyyymmdd_to_date(row.get("更新日期", ""))
        out.append(
            {
                "symbol": symbol_id,
                "market": market.value,
                "name": row.get("證券名稱", "").strip(),
                "listing_market": row.get("市場別", "").strip(),
                "transfer_agent": row.get("股務單位", "").strip(),
                "security_status": row.get("證券狀態", "").strip(),
                "scripless": row.get("無面無實體", "").strip() or None,
                "currency": row.get("發行幣別", "").strip(),
                "par_value": _to_float(row.get("每股面額(元)", "0")),
                "updated_date": updated.isoformat() if updated else None,
            }
        )
    return out


def _map_pledged_custody_shares(
    rows: list[dict[str, str]], sym: Symbol | None
) -> list[dict[str, object]]:
    """Dataset `1-4` — 集保股份保管明細. Header: 資料日期,證券代號,證券名稱,分戶保管股數,
    特定保管股數,強制集保總股數,發行仟股數."""
    out: list[dict[str, object]] = []
    for row in rows:
        symbol_id = row.get("證券代號", "").strip()
        market = sym.market if sym is not None else Market.TW
        as_of = _yyyymmdd_to_date(row.get("資料日期", ""))
        out.append(
            {
                "symbol": symbol_id,
                "market": market.value,
                "name": row.get("證券名稱", "").strip(),
                "as_of": as_of.isoformat() if as_of else None,
                "dispersed_custody_shares": _to_int(row.get("分戶保管股數", "0")),
                "specific_custody_shares": _to_int(row.get("特定保管股數", "0")),
                "mandatory_custody_shares": _to_int(row.get("強制集保總股數", "0")),
                "issued_thousand_shares": _to_int(row.get("發行仟股數", "0")),
            }
        )
    return out


def _map_shareholding_distribution(
    rows: list[dict[str, str]], sym: Symbol | None
) -> list[dict[str, object]]:
    """Dataset `1-5` — 集保戶股權分散表. Header: 資料日期,證券代號,持股分級,人數,股數,
    占集保庫存數比例%. Public/free — distinct from FinMind's Sponsor-tier
    `shareholding_distribution`. 17 tier rows per symbol per day upstream."""
    out: list[dict[str, object]] = []
    for row in rows:
        symbol_id = row.get("證券代號", "").strip()
        market = sym.market if sym is not None else Market.TW
        as_of = _yyyymmdd_to_date(row.get("資料日期", ""))
        out.append(
            {
                "symbol": symbol_id,
                "market": market.value,
                "as_of": as_of.isoformat() if as_of else None,
                "holding_level": row.get("持股分級", "").strip(),
                "holders": _to_int(row.get("人數", "0")),
                "shares": _to_int(row.get("股數", "0")),
                "percent_of_custody": _to_float(row.get("占集保庫存數比例%", "0")),
            }
        )
    return out


def _map_offshore_fund_nav(
    rows: list[dict[str, str]], sym: Symbol | None
) -> list[dict[str, object]]:
    """Dataset `3-4` — 境外基金淨值. Header: 基金代碼,基金名稱,日期,基金淨值(金額),ISINCODE,
    計價幣別,境外基金機構,總代理機構. Fund code is TDCC's own, not an equity `Symbol.id`."""
    out: list[dict[str, object]] = []
    for row in rows:
        fund_code = row.get("基金代碼", "").strip()
        market = sym.market if sym is not None else Market.TW
        nav_date = _yyyymmdd_to_date(row.get("日期", ""))
        out.append(
            {
                "symbol": fund_code,
                "market": market.value,
                "name": row.get("基金名稱", "").strip(),
                "nav_date": nav_date.isoformat() if nav_date else None,
                "nav": _to_float(row.get("基金淨值(金額)", "0")),
                "isin_code": row.get("ISINCODE", "").strip() or None,
                "currency": row.get("計價幣別", "").strip(),
                "offshore_fund_institution": row.get("境外基金機構", "").strip(),
                "master_agent": row.get("總代理機構", "").strip(),
            }
        )
    return out


def _map_evoting_schedule(
    rows: list[dict[str, str]], sym: Symbol | None
) -> list[dict[str, object]]:
    """Dataset `6-2` — 股東會電子投票起訖日. Header: 資料日期,年度,證券代號,證券名稱,
    股東會日期,投票起日,投票迄日. Genuinely Big5-encoded upstream despite the response's own
    `charset=UTF-8` header — decoded before this mapper ever sees the row dicts."""
    out: list[dict[str, object]] = []
    for row in rows:
        symbol_id = row.get("證券代號", "").strip()
        market = sym.market if sym is not None else Market.TW
        meeting_date = _yyyymmdd_to_date(row.get("股東會日期", ""))
        voting_start = _yyyymmdd_to_date(row.get("投票起日", ""))
        voting_end = _yyyymmdd_to_date(row.get("投票迄日", ""))
        out.append(
            {
                "symbol": symbol_id,
                "market": market.value,
                "name": row.get("證券名稱", "").strip(),
                "fiscal_year_roc": row.get("年度", "").strip(),
                "meeting_date": meeting_date.isoformat() if meeting_date else None,
                "voting_start_date": voting_start.isoformat() if voting_start else None,
                "voting_end_date": voting_end.isoformat() if voting_end else None,
            }
        )
    return out


def _map_monthly_custody_accounts(
    rows: list[dict[str, str]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """Dataset `2-1` — 集保帳戶統計 (whole-market monthly). Header: 資料年月,新開戶數,解約數,
    總開戶數,累計開戶數. Market-level — no `data_id`/symbol filter exists upstream."""
    out: list[dict[str, object]] = []
    for row in rows:
        as_of_month = _roc_yearmonth_to_iso(row.get("資料年月", ""))
        out.append(
            {
                "market": Market.TW.value,
                "as_of_month": as_of_month,
                "new_accounts": _to_int(row.get("新開戶數", "0")),
                "closed_accounts": _to_int(row.get("解約數", "0")),
                "net_accounts": _to_int(row.get("總開戶數", "0")),
                "cumulative_accounts": _to_int(row.get("累計開戶數", "0")),
            }
        )
    return out


_DATASETS: dict[Capability, _DatasetConfig] = {
    Capability.TDCC_SECURITIES_INFO: _DatasetConfig(
        dataset_id="1-1",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="TDCC securities master reference (證券基本資料) — a static reference record "
        "(registrar/market/status/par-value), not a price or a live status feed; reflects "
        "the dump's own 更新日期, not necessarily today.",
        row_key="rows",
        row_mapper=_map_securities_info,
        symbol_column="證券代號",
    ),
    Capability.TDCC_PLEDGED_CUSTODY_SHARES: _DatasetConfig(
        dataset_id="1-4",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="TDCC pledged/custody share-split (集保股份保管明細) — reflects the single most "
        "recently published day only; this dataset upstream only ever covers a small subset "
        "of securities (those with a dispersed/specific custody split to report), not the "
        "whole market.",
        row_key="rows",
        row_mapper=_map_pledged_custody_shares,
        symbol_column="證券代號",
    ),
    Capability.TDCC_SHAREHOLDING_DISTRIBUTION: _DatasetConfig(
        dataset_id="1-5",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="TDCC shareholding-level distribution (集保戶股權分散表) — public/free, weekly "
        "cadence (published Fridays); a chip-concentration signal, not a live shareholding "
        "figure. Distinct from FinMind's Sponsor-tier `shareholding_distribution` capability.",
        row_key="rows",
        row_mapper=_map_shareholding_distribution,
        symbol_column="證券代號",
    ),
    Capability.TDCC_OFFSHORE_FUND_NAV: _DatasetConfig(
        dataset_id="3-4",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="TDCC offshore-fund NAV (境外基金淨值) — published with the fund's own reporting "
        "lag (often several days behind the dump's own 日期); not a live quote.",
        row_key="rows",
        row_mapper=_map_offshore_fund_nav,
        symbol_column="基金代碼",
    ),
    Capability.TDCC_EVOTING_SCHEDULE: _DatasetConfig(
        dataset_id="6-2",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="TDCC shareholder-meeting e-voting window (股東會電子投票起訖日) — a scheduling "
        "record only, never a vote tally/result; rolls off the upstream dump over time.",
        row_key="rows",
        row_mapper=_map_evoting_schedule,
        symbol_column="證券代號",
        encoding="big5",
    ),
    Capability.TDCC_MONTHLY_CUSTODY_ACCOUNTS: _DatasetConfig(
        dataset_id="2-1",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="TDCC whole-market monthly custody-account statistics (集保帳戶統計) — a "
        "statutory monthly reading, market-level, no per-symbol scope.",
        row_key="rows",
        row_mapper=_map_monthly_custody_accounts,
        symbol_column=None,
    ),
}

__all__ = ["_DATASETS"]
