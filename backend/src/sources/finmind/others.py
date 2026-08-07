"""FinMind Others-category datasets, plus the active-ETF family that doesn't fit under any
other coverage-pass category — FinMind's own news feed, the whole-market business-climate
indicator, the industry-chain classification roster, and active-ETF roster/holdings/holding-
change. 6 datasets.
"""

from __future__ import annotations

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol

from ._shared import _as_float, _as_int, _DatasetConfig, _row_symbol


def _map_active_etf_info(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockActiveETFInfo` — no `data_id` is ever passed for this dataset (the whole
    roster is always returned), so `sym` is never used to resolve the row's own symbol; each
    row already carries its own `stock_id`."""
    infos: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, None)
        infos.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "as_of": row.get("date"),
                "name": str(row.get("stock_name", "")),
                "category": str(row.get("category", "")),
                "fund_type": str(row.get("type", "")),
            }
        )
    return infos


def _map_active_etf_holding(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    holdings: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        holdings.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": row["date"],
                "component_stock_id": str(row.get("component_stock_id", "")),
                "component_stock_name": str(row.get("component_stock_name", "")),
                "asset_type": str(row.get("asset_type", "")),
                "shares": _as_int(row.get("shares")),
                "weight": _as_float(row.get("weight")),
                "market_value": _as_float(row.get("market_value")),
                "currency": str(row.get("currency", "")),
            }
        )
    return holdings


def _map_active_etf_holding_change(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockActiveETFHoldingChange` — FinMind's own pre-diffed buy/sell per constituent
    per day; this mapper only reshapes column names, it does not compute the diff itself."""
    changes: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        changes.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": row["date"],
                "component_stock_id": str(row.get("component_stock_id", "")),
                "component_stock_name": str(row.get("component_stock_name", "")),
                "buy": _as_int(row.get("buy")),
                "sell": _as_int(row.get("sell")),
            }
        )
    return changes


def _map_stock_news(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    """`TaiwanStockNews` — FinMind's own news dataset, distinct from `sources/news.py`'s
    RSS/GDELT/Finnhub aggregator. Third-party content: cite, never restate as our own
    analysis."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": row["date"],
                "description": str(row.get("description", "")),
                "link": str(row.get("link", "")),
                "source": str(row.get("source", "")),
                "title": str(row.get("title", "")),
            }
        )
    return rows_out


def _map_business_indicator(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanBusinessIndicator` — 景氣對策信號 whole-market monthly reading, no `data_id` param
    exists."""
    return [
        {
            "market": Market.TW.value,
            "date": row["date"],
            "leading": _as_float(row.get("leading")),
            "leading_notrend": _as_float(row.get("leading_notrend")),
            "coincident": _as_float(row.get("coincident")),
            "coincident_notrend": _as_float(row.get("coincident_notrend")),
            "lagging": _as_float(row.get("lagging")),
            "lagging_notrend": _as_float(row.get("lagging_notrend")),
            "monitoring": _as_float(row.get("monitoring")),
            "monitoring_color": str(row.get("monitoring_color", "")),
        }
        for row in rows
    ]


def _map_industry_chain_classification(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockIndustryChain` — the static stock-to-industry-chain classification roster
    underlying `industry_chain_money_flow`'s daily aggregate."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "industry": str(row.get("industry", "")),
                "sub_industry": str(row.get("sub_industry", "")),
                "as_of": row.get("date"),
            }
        )
    return rows_out


DATASETS: dict[Capability, _DatasetConfig] = {
    Capability.ACTIVE_ETF_INFO: _DatasetConfig(
        dataset="TaiwanStockActiveETFInfo",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="Active-ETF roster/basic-info list; a reference list, not a daily-clock-time "
        "market snapshot — no `data_id` is ever meaningful for this dataset (the full roster "
        "comes back regardless of what's passed).",
        row_key="info",
        row_mapper=_map_active_etf_info,
        whole_market_ok=True,
    ),
    Capability.ACTIVE_ETF_HOLDING: _DatasetConfig(
        dataset="TaiwanStockActiveETFHolding",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Sponsor-tier active-ETF daily constituent snapshot; published post-close "
        "(~21:00), not live. Data range starts 2025-05-05.",
        row_key="holdings",
        row_mapper=_map_active_etf_holding,
        whole_market_ok=True,
    ),
    Capability.ACTIVE_ETF_HOLDING_CHANGE: _DatasetConfig(
        dataset="TaiwanStockActiveETFHoldingChange",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Sponsor-tier active-ETF daily constituent buy/sell (FinMind's own diff of "
        "consecutive-day holdings, not a manager's discretionary trade); published post-close "
        "(~21:00), not live. Data range starts 2025-05-05.",
        row_key="changes",
        row_mapper=_map_active_etf_holding_change,
        whole_market_ok=True,
    ),
    Capability.STOCK_NEWS: _DatasetConfig(
        dataset="TaiwanStockNews",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="FinMind's own news dataset, distinct from the RSS/GDELT/Finnhub news "
        "aggregator; single trading day per request upstream.",
        row_key="rows",
        row_mapper=_map_stock_news,
    ),
    Capability.BUSINESS_INDICATOR: _DatasetConfig(
        dataset="TaiwanBusinessIndicator",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Backer/Sponsor-tier whole-market 景氣對策信號 (business-climate indicator) "
        "monthly reading; released on the statutory monthly cadence, not a daily-clock-time "
        "snapshot. No `data_id` param exists.",
        row_key="rows",
        row_mapper=_map_business_indicator,
        whole_market_ok=True,
    ),
    Capability.INDUSTRY_CHAIN_CLASSIFICATION: _DatasetConfig(
        dataset="TaiwanStockIndustryChain",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.REFERENCE,
        caveat="Backer/Sponsor-tier static stock-to-industry-chain classification roster; "
        "the underlying mapping behind `industry_chain_money_flow`'s daily aggregate.",
        row_key="rows",
        row_mapper=_map_industry_chain_classification,
        whole_market_ok=True,
    ),
}

__all__ = ["DATASETS"]
