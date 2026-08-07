"""FinMind Global-Economic-Data-category datasets — the TWD exchange-rate reference table, the
multi-country central-bank policy-rate panel, world gold/crude-oil commodity benchmarks, the US
Treasury yield curve, and the CNN Fear & Greed sentiment gauge. 6 datasets.
"""

from __future__ import annotations

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol

from ._shared import _as_float, _DatasetConfig, _row_symbol_field


def _map_exchange_rate(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanExchangeRate` — TWD-vs-foreign-currency reference rate; `symbol` is the foreign
    currency code, TWD is the implicit other leg. Modeled `Market.TW`-scoped since this is a
    TWD-context dataset, not a generic world-FX table."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol_field(row, sym, "currency", Market.TW)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": row["date"],
                "cash_buy": _as_float(row.get("cash_buy")),
                "cash_sell": _as_float(row.get("cash_sell")),
                "spot_buy": _as_float(row.get("spot_buy")),
                "spot_sell": _as_float(row.get("spot_sell")),
            }
        )
    return rows_out


def _map_central_bank_rate(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`InterestRate` — inherently a multi-country panel (FED/BOE/RBA/PBOC/BOC/ECB/RBNZ/RBI/
    CBR/BCB/BOJ/SNB) with no single-country home; modeled `Market.GLOBAL` rather than forcing
    one country's `Market` value onto a dozen distinct central banks."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol_field(row, sym, "country", Market.GLOBAL)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "date": row["date"],
                "country": str(row.get("country", "")),
                "full_country_name": str(row.get("full_country_name", "")),
                "interest_rate": _as_float(row.get("interest_rate")),
            }
        )
    return rows_out


def _map_gold_price(rows: list[dict[str, object]], _sym: Symbol | None) -> list[dict[str, object]]:
    """`GoldPrice` — a world commodity benchmark with no `data_id` param at all; modeled
    `Market.GLOBAL`, market-level, no symbol scope."""
    return [
        {
            "market": Market.GLOBAL.value,
            "date": row["date"],
            "price": _as_float(row.get("Price")),
        }
        for row in rows
    ]


def _map_crude_oil_prices(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`CrudeOilPrices` — a world commodity benchmark (WTI/Brent); modeled `Market.GLOBAL`. This
    is a spot price, not a real-time quote: the latest available date is typically several
    trading days (~1 week) behind — always read the actually-returned latest `date`."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol_field(row, sym, "name", Market.GLOBAL)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "date": row["date"],
                "price": _as_float(row.get("price")),
            }
        )
    return rows_out


def _map_government_bond_yield(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`GovernmentBondsYield` — the US Treasury yield curve specifically, a genuine
    single-country home, modeled `Market.US` (unlike `InterestRate`'s multi-country panel)."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol_field(row, sym, "name", Market.US)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "date": row["date"],
                "value": _as_float(row.get("value")),
            }
        )
    return rows_out


def _map_fear_greed_index(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`CnnFearGreedIndex` — a US-market sentiment gauge specifically, a genuine single-country
    home, modeled `Market.US`; market-level, no symbol scope, no `data_id` param exists."""
    return [
        {
            "market": Market.US.value,
            "date": row["date"],
            "fear_greed": _as_float(row.get("fear_greed")),
            "fear_greed_emotion": str(row.get("fear_greed_emotion", "")),
        }
        for row in rows
    ]


DATASETS: dict[Capability, _DatasetConfig] = {
    Capability.EXCHANGE_RATE: _DatasetConfig(
        dataset="TaiwanExchangeRate",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="TWD-vs-foreign-currency reference rate; `symbol` is the foreign-currency code, "
        "TWD is the implicit other leg — modeled TW-scoped as a TWD-context dataset, not a "
        "generic world-FX table.",
        row_key="rows",
        row_mapper=_map_exchange_rate,
        whole_market_ok=True,
    ),
    Capability.CENTRAL_BANK_RATE: _DatasetConfig(
        dataset="InterestRate",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Multi-country central-bank policy-rate panel (FED/BOE/RBA/PBOC/BOC/ECB/RBNZ/"
        "RBI/CBR/BCB/BOJ/SNB) with no single-country home — modeled `Market.GLOBAL`. "
        "Event-driven (policy-meeting dates), not a daily-clock-time series.",
        row_key="rows",
        row_mapper=_map_central_bank_rate,
        whole_market_ok=True,
        market=Market.GLOBAL,
    ),
    Capability.GOLD_PRICE: _DatasetConfig(
        dataset="GoldPrice",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="World gold-price benchmark with no single-country home — modeled "
        "`Market.GLOBAL`; market-level, no `data_id` param exists.",
        row_key="rows",
        row_mapper=_map_gold_price,
        whole_market_ok=True,
        market=Market.GLOBAL,
    ),
    Capability.CRUDE_OIL_PRICES: _DatasetConfig(
        dataset="CrudeOilPrices",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="World crude-oil benchmark (WTI/Brent) with no single-country home — modeled "
        "`Market.GLOBAL`. This is a spot price, not a real-time quote: the latest available "
        "date is typically several trading days (~1 week) behind — always read the "
        "actually-returned latest date, don't assume same-day freshness.",
        row_key="rows",
        row_mapper=_map_crude_oil_prices,
        whole_market_ok=True,
        market=Market.GLOBAL,
    ),
    Capability.GOVERNMENT_BOND_YIELD: _DatasetConfig(
        dataset="GovernmentBondsYield",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="US Treasury yield-curve reading, a genuine single-country home — modeled "
        "`Market.US` (unlike `central_bank_rate`'s multi-country panel).",
        row_key="rows",
        row_mapper=_map_government_bond_yield,
        whole_market_ok=True,
        market=Market.US,
    ),
    Capability.FEAR_GREED_INDEX: _DatasetConfig(
        dataset="CnnFearGreedIndex",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Backer/Sponsor-tier CNN Fear & Greed Index reading, a US-market sentiment "
        "gauge — modeled `Market.US`; market-level, no symbol scope, no `data_id` param "
        "exists.",
        row_key="rows",
        row_mapper=_map_fear_greed_index,
        whole_market_ok=True,
        market=Market.US,
    ),
}

__all__ = ["DATASETS"]
