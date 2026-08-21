"""FinMind International-Markets-category datasets — US/UK/continental-Europe/Japan stock
reference rosters and EOD price series, plus the Sponsor-tier US-market minute-bar series.
`data_id` for these datasets is that market's own quote symbol, never a Taiwan-style numeric
id. 9 datasets.
"""

from __future__ import annotations

from collections.abc import Callable

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol

from ._shared import _as_date_str, _as_float, _as_int, _DatasetConfig, _row_symbol_field


def _make_international_info_mapper(
    market: Market, region_field: str, sector_field: str | None = None
) -> Callable[[list[dict[str, object]], Symbol | None], list[dict[str, object]]]:
    """`USStockInfo`/`UKStockInfo`/`EuropeStockInfo`/`JapanStockInfo` share an identical shape
    aside from which upstream column carries the region/exchange detail (and whether a `Sector`
    column exists at all — only `JapanStockInfo` has one). No `data_id` is ever meaningful for
    these datasets; the whole roster always comes back."""

    def _mapper(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
        rows_out: list[dict[str, object]] = []
        for row in rows:
            row_sym = _row_symbol_field(row, sym, "stock_id", market)
            rows_out.append(
                {
                    "symbol": row_sym.id,
                    "market": row_sym.market.value,
                    "as_of": row.get("date"),
                    "name": str(row.get("stock_name", "")),
                    "region_detail": row.get(region_field) or None,
                    "sector": (row.get(sector_field) or None) if sector_field else None,
                }
            )
        return rows_out

    return _mapper


_map_us_stock_info = _make_international_info_mapper(Market.US, "Country")
_map_uk_stock_info = _make_international_info_mapper(Market.GB, "Country")
_map_europe_stock_info = _make_international_info_mapper(Market.EU, "Market")
_map_japan_stock_info = _make_international_info_mapper(Market.JP, "Exchange", "Sector")


def _make_international_price_mapper(
    market: Market,
) -> Callable[[list[dict[str, object]], Symbol | None], list[dict[str, object]]]:
    """`USStockPrice`/`UKStockPrice`/`EuropeStockPrice`/`JapanStockPrice` share an identical
    shape; `data_id` is that market's own quote symbol (e.g. `^DJI`, `BP.L`, `AALB.AS`,
    `7203.T`), never a Taiwan-style numeric `stock_id` — passed straight through, never
    reparsed."""

    def _mapper(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
        rows_out: list[dict[str, object]] = []
        for row in rows:
            row_sym = _row_symbol_field(row, sym, "stock_id", market)
            rows_out.append(
                {
                    "symbol": row_sym.id,
                    "market": row_sym.market.value,
                    "trade_date": _as_date_str(row["date"]),
                    "open": _as_float(row.get("Open")),
                    "high": _as_float(row.get("High")),
                    "low": _as_float(row.get("Low")),
                    "close": _as_float(row.get("Close")),
                    "adj_close": _as_float(row.get("Adj_Close")),
                    "volume": _as_int(row.get("Volume")),
                }
            )
        return rows_out

    return _mapper


_map_us_stock_price = _make_international_price_mapper(Market.US)
_map_uk_stock_price = _make_international_price_mapper(Market.GB)
_map_europe_stock_price = _make_international_price_mapper(Market.EU)
_map_japan_stock_price = _make_international_price_mapper(Market.JP)


def _map_us_stock_price_minute(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`USStockPriceMinute` — Backer/Sponsor-only, lowercase OHLCV columns (unlike the daily
    `USStockPrice`'s capitalized columns, and no `Adj_Close`)."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol_field(row, sym, "stock_id", Market.US)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "close": _as_float(row.get("close")),
                "high": _as_float(row.get("high")),
                "low": _as_float(row.get("low")),
                "open": _as_float(row.get("open")),
                "volume": _as_int(row.get("volume")),
            }
        )
    return rows_out


DATASETS: dict[Capability, _DatasetConfig] = {
    Capability.US_STOCK_INFO: _DatasetConfig(
        dataset="USStockInfo",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="Whole-roster US-market stock reference table; no `data_id` is ever meaningful.",
        row_key="rows",
        row_mapper=_map_us_stock_info,
        whole_market_ok=True,
        market=Market.US,
    ),
    Capability.US_STOCK_PRICE: _DatasetConfig(
        dataset="USStockPrice",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="US-market EOD price row; `data_id` is the market's own quote symbol (e.g. "
        "`^DJI`), never a Taiwan-style numeric stock id.",
        row_key="rows",
        row_mapper=_map_us_stock_price,
        market=Market.US,
    ),
    Capability.US_STOCK_PRICE_MINUTE: _DatasetConfig(
        dataset="USStockPriceMinute",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier US-market minute-bar series.",
        row_key="rows",
        row_mapper=_map_us_stock_price_minute,
        market=Market.US,
    ),
    Capability.UK_STOCK_INFO: _DatasetConfig(
        dataset="UKStockInfo",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="Whole-roster UK-market stock reference table; no `data_id` is ever meaningful.",
        row_key="rows",
        row_mapper=_map_uk_stock_info,
        whole_market_ok=True,
        market=Market.GB,
        # Confirmed empirically: a static reference table with no date semantics — passing
        # start_date/end_date doesn't 400, it silently filters the whole response to zero rows.
        no_start_date_param=True,
        no_data_id_param=True,  # per this dataset's own caveat: no data_id is ever meaningful.
    ),
    Capability.UK_STOCK_PRICE: _DatasetConfig(
        dataset="UKStockPrice",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="UK-market EOD price row; `data_id` is the market's own quote symbol (e.g. "
        "`BP.L`), never a Taiwan-style numeric stock id.",
        row_key="rows",
        row_mapper=_map_uk_stock_price,
        market=Market.GB,
    ),
    Capability.EUROPE_STOCK_INFO: _DatasetConfig(
        dataset="EuropeStockInfo",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="Whole-roster continental-Europe stock reference table; no `data_id` is ever "
        "meaningful.",
        row_key="rows",
        row_mapper=_map_europe_stock_info,
        whole_market_ok=True,
        market=Market.EU,
        no_start_date_param=True,  # same static-reference-table behavior as UK_STOCK_INFO.
        no_data_id_param=True,
    ),
    Capability.EUROPE_STOCK_PRICE: _DatasetConfig(
        dataset="EuropeStockPrice",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Continental-Europe EOD price row; `data_id` is the market's own quote symbol "
        "(e.g. `AALB.AS`), never a Taiwan-style numeric stock id.",
        row_key="rows",
        row_mapper=_map_europe_stock_price,
        market=Market.EU,
    ),
    Capability.JAPAN_STOCK_INFO: _DatasetConfig(
        dataset="JapanStockInfo",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="Whole-roster Japan-market stock reference table; no `data_id` is ever meaningful.",
        row_key="rows",
        row_mapper=_map_japan_stock_info,
        whole_market_ok=True,
        market=Market.JP,
        no_start_date_param=True,  # same static-reference-table behavior as UK_STOCK_INFO.
        no_data_id_param=True,
    ),
    Capability.JAPAN_STOCK_PRICE: _DatasetConfig(
        dataset="JapanStockPrice",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Japan-market EOD price row; `data_id` is the market's own quote symbol (e.g. "
        "`7203.T`), never a Taiwan-style numeric stock id.",
        row_key="rows",
        row_mapper=_map_japan_stock_price,
        market=Market.JP,
    ),
}

__all__ = ["DATASETS"]
