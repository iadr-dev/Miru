"""Cross-ETF money flow."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date as date_

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ingest.jobs import ACTIVE_ETF_HOLDING_CHANGE_JOB, ACTIVE_ETF_HOLDING_JOB
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import ActiveEtfHoldingChangeRow, ActiveEtfHoldingRow
from schemas.enums import DataClass, Market
from tools.base import SyncStoreTool, ToolContext, is_entitled, parse_args
from tools.market._common import (
    closes_by_symbol,
    latest_ingested_date_on_or_before,
    real_calendar,
    shares_outstanding_by_symbol,
)

_MONEY_FLOW_MAX_WINDOW_DAYS = 30  # bounded date range for the cross-ETF aggregation read


_STOCK_SHARES_HELD_PCT_CAVEAT = (
    "佔個股比重為估算值：以市值(TaiwanStockMarketValue) ÷ 當日收盤價 反推流通股數，"
    "非發行公司公告的實際流通股數。"
)


class GetEtfMoneyFlowInput(BaseModel):
    """A bounded date range + market scope — never an "all symbols" shape. The aggregation
    itself happens server-side over every active ETF's ingested holdings for the window, not by
    accepting a symbol list to fan out over."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    start_date: date_
    end_date: date_
    top_n: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def _validate_bounded_range(self) -> GetEtfMoneyFlowInput:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        if (self.end_date - self.start_date).days > _MONEY_FLOW_MAX_WINDOW_DAYS:
            raise ValueError(f"date range must not exceed {_MONEY_FLOW_MAX_WINDOW_DAYS} days")
        return self


class EtfMoneyFlowRow(BaseModel):
    """One constituent stock's cross-ETF aggregation row. `locked=True` rows (governance.md §4
    top-N-preview exception) have every field but `rank` nulled server-side — an unentitled
    caller must not learn which stock a locked row even names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int = 0  # 1-based position in the emitted ranking — real even when locked
    component_stock_id: str | None
    component_stock_name: str | None
    active_etf_holder_count: int | None = None  # 主動式檔數 — None if no holding snapshot found
    held_shares: int | None = None  # 持有張數 — None if no holding snapshot found
    held_market_value: float | None = None  # 持有市值 — None if no holding snapshot found
    # 佔個股比重 (estimated) — held_shares ÷ shares-outstanding, where shares-outstanding is
    # itself derived as MarketValueRow.market_value ÷ that day's close (see
    # `_stock_shares_held_pct`'s own docstring for why this is exact-by-definition, not a rough
    # guess). `None` when either the stock's market-cap or its close is missing from the store.
    stock_shares_held_pct: float | None = None
    net_shares_flow: int | None  # sum(buy - sell) across every active ETF over the window
    buying_etf_count: int | None  # 買 — distinct ETFs net-buying this stock over the window
    selling_etf_count: int | None  # 賣 — distinct ETFs net-selling this stock over the window
    locked: bool = False


class GetEtfMoneyFlowOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    start_date: date_
    end_date: date_
    # The real date the holdings snapshot behind `inflow_ranking`/`outflow_ranking`/
    # `held_market_value` reflects — resolved via `latest_ingested_date_on_or_before`, which can
    # lag `end_date` when the holdings ingest job hasn't caught up to the price calendar yet (the
    # bug `latest_ingested_date_on_or_before` itself was added to fix). `None` only alongside
    # `data_available=False`, since a successful read always resolves a holding date.
    holding_as_of: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    total_stock_count: int = 0  # 共N檔 — every distinct constituent aggregated, not just top_n
    stock_shares_held_pct_caveat: str = _STOCK_SHARES_HELD_PCT_CAVEAT
    free_row_count: int = 0  # governance.md §4 top-N-preview exception
    inflow_ranking: tuple[EtfMoneyFlowRow, ...] = Field(default_factory=tuple)
    outflow_ranking: tuple[EtfMoneyFlowRow, ...] = Field(default_factory=tuple)


@dataclass
class _StockAgg:
    """Mutable running aggregate for one constituent stock — accumulated across every ETF's
    change rows in the window, never itself a canonical/output type."""

    component_stock_name: str
    net_shares_flow: int = 0
    per_etf_net: dict[str, int] = field(default_factory=dict[str, int])


_ETF_MONEY_FLOW_FREE_ROW_COUNT = 2  # governance.md §4 top-N-preview exception


def _ranked_etf_money_flow_rows(rows: Sequence[EtfMoneyFlowRow]) -> tuple[EtfMoneyFlowRow, ...]:
    """Stamps each already-sorted row with its 1-based `rank` — the value that survives
    masking, mirroring `_daily_signal_rows`'s own ranking contract."""
    return tuple(row.model_copy(update={"rank": i}) for i, row in enumerate(rows, start=1))


def _mask_etf_money_flow_rows_beyond(
    rows: Sequence[EtfMoneyFlowRow], free_row_count: int
) -> tuple[EtfMoneyFlowRow, ...]:
    """Rows beyond `free_row_count` get every identity/value field nulled server-side — only
    `rank` survives, matching `_mask_broker_top_rows_beyond`'s contract."""
    masked: list[EtfMoneyFlowRow] = []
    for row in rows:
        if row.rank <= free_row_count:
            masked.append(row)
            continue
        masked.append(
            EtfMoneyFlowRow(
                rank=row.rank,
                component_stock_id=None,
                component_stock_name=None,
                active_etf_holder_count=None,
                held_shares=None,
                held_market_value=None,
                stock_shares_held_pct=None,
                net_shares_flow=None,
                buying_etf_count=None,
                selling_etf_count=None,
                locked=True,
            )
        )
    return tuple(masked)


class GetEtfMoneyFlowTool(SyncStoreTool):
    """`get_etf_money_flow` — cross-ETF 資金流向 aggregated over every active ETF's ingested
    holdings for a bounded date range: 主動式檔數/持有市值 per stock, 流入/流出排行, 多空共識.

    Sourced from the **ingest-owned Parquet store** (`ingest.store.ParquetStore`), not a live
    per-symbol router fan-out: this is a genuinely cross-symbol aggregation (every active ETF's
    holdings, for every day in the window), and the one legitimate whole-market path for that
    shape of read is the store the whole-market ingest job already populates — reading it here
    is not a second whole-market-scan path, it's the same derived-store read every other
    analytics consumer uses. If the ingest job hasn't landed a partition for a requested date
    yet, this tool reports that rather than falling back to an unbounded live fan-out.
    """

    name = "get_etf_money_flow"
    description = (
        "Get the cross-ETF money-flow aggregation over active-management ETF holdings for a "
        "bounded date range: per-stock holder count/held value, inflow/outflow ranking, and "
        "bullish/bearish ETF consensus (Sponsor-tier)."
    )
    input_model: type[BaseModel] = GetEtfMoneyFlowInput
    output_model: type[BaseModel] = GetEtfMoneyFlowOutput
    data_class = DataClass.SPONSOR
    cache_ttl_seconds = 300.0  # whole-market derivation; recomputed when ingest writes new data
    card_type: str | None = "etf_money_flow"
    supports_row_preview: bool = True

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetEtfMoneyFlowInput, args)
        calendar = real_calendar(self._store, typed.market.value)
        window_dates = calendar.business_days_between(typed.start_date, typed.end_date)

        raw_changes = self._store.read_range(
            ACTIVE_ETF_HOLDING_CHANGE_JOB.dataset, typed.market.value, window_dates
        )
        if not raw_changes:
            return GetEtfMoneyFlowOutput(
                market=typed.market.value,
                start_date=typed.start_date,
                end_date=typed.end_date,
                data_available=False,
                unavailable_reason=(
                    "No ingested active-ETF holding-change partition found for "
                    f"{typed.market.value} in [{typed.start_date.isoformat()}, "
                    f"{typed.end_date.isoformat()}] — the whole-market ingest job may not have "
                    "run yet for this range."
                ),
            )
        changes = rows_from_store(ActiveEtfHoldingChangeRow, raw_changes)

        aggregates: dict[str, _StockAgg] = {}
        for change in changes:
            agg = aggregates.get(change.component_stock_id)
            if agg is None:
                agg = _StockAgg(component_stock_name=change.component_stock_name)
                aggregates[change.component_stock_id] = agg
            net = change.buy - change.sell
            agg.per_etf_net[change.symbol] = agg.per_etf_net.get(change.symbol, 0) + net
            agg.net_shares_flow += net

        holding_end_date = latest_ingested_date_on_or_before(
            self._store, ACTIVE_ETF_HOLDING_JOB.dataset, typed.market.value, typed.end_date
        )
        raw_holdings = (
            self._store.read_partition(
                ACTIVE_ETF_HOLDING_JOB.dataset, typed.market.value, holding_end_date
            )
            if holding_end_date is not None
            else []
        )
        holdings = rows_from_store(ActiveEtfHoldingRow, raw_holdings)
        # `holding.market_value` is FinMind's own field, hardcoded `0.0` for every row of this
        # dataset (see `_holdings_market_value`'s docstring) — recompute from shares x that day's
        # real close instead, the same fix `GetActiveEtfReportTool` already applies.
        closes = (
            closes_by_symbol(self._store, typed.market.value, holding_end_date)
            if holding_end_date is not None
            else {}
        )
        shares_outstanding = (
            shares_outstanding_by_symbol(self._store, typed.market.value, holding_end_date)
            if holding_end_date is not None
            else {}
        )
        holder_counts: dict[str, int] = {}
        held_shares: dict[str, int] = {}
        held_values: dict[str, float] = {}
        for holding in holdings:
            stock_id = holding.component_stock_id
            holder_counts[stock_id] = holder_counts.get(stock_id, 0) + 1
            held_shares[stock_id] = held_shares.get(stock_id, 0) + holding.shares
            close = closes.get(stock_id)
            if close is not None:
                held_values[stock_id] = held_values.get(stock_id, 0.0) + holding.shares * close

        rows: list[EtfMoneyFlowRow] = []
        for stock_id, agg in aggregates.items():
            buying = sum(1 for net in agg.per_etf_net.values() if net > 0)
            selling = sum(1 for net in agg.per_etf_net.values() if net < 0)
            shares = held_shares.get(stock_id)
            outstanding = shares_outstanding.get(stock_id)
            shares_held_pct = (
                shares / outstanding * 100 if shares is not None and outstanding else None
            )
            rows.append(
                EtfMoneyFlowRow(
                    component_stock_id=stock_id,
                    component_stock_name=agg.component_stock_name,
                    active_etf_holder_count=holder_counts.get(stock_id),
                    held_shares=shares,
                    held_market_value=held_values.get(stock_id),
                    stock_shares_held_pct=shares_held_pct,
                    net_shares_flow=agg.net_shares_flow,
                    buying_etf_count=buying,
                    selling_etf_count=selling,
                )
            )

        inflow = _ranked_etf_money_flow_rows(
            sorted(rows, key=lambda r: r.net_shares_flow or 0, reverse=True)[: typed.top_n]
        )
        outflow = _ranked_etf_money_flow_rows(
            sorted(rows, key=lambda r: r.net_shares_flow or 0)[: typed.top_n]
        )

        entitled = is_entitled(self.data_class, ctx.tier)
        if not entitled:
            inflow = _mask_etf_money_flow_rows_beyond(inflow, _ETF_MONEY_FLOW_FREE_ROW_COUNT)
            outflow = _mask_etf_money_flow_rows_beyond(outflow, _ETF_MONEY_FLOW_FREE_ROW_COUNT)

        return GetEtfMoneyFlowOutput(
            market=typed.market.value,
            start_date=typed.start_date,
            end_date=typed.end_date,
            holding_as_of=holding_end_date,
            data_available=True,
            total_stock_count=len(aggregates),
            free_row_count=_ETF_MONEY_FLOW_FREE_ROW_COUNT,
            inflow_ranking=inflow,
            outflow_ranking=outflow,
        )
