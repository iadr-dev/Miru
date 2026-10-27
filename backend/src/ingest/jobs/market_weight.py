"""Market value and market-value weight derived from closes and shares outstanding.

FinMind's `TaiwanStockMarketValue` / `TaiwanStockMarketValueWeight` are outside its free plan and
no exchange publishes the same figures daily, so the runner asks `DerivedMarketValueSource` first
(`config/whole_market_source_priority.yaml`): for a day whose price partition is already stored,

    market_value = close x issued common shares
    weight_per   = 100 x market_value / sum of market_value over the priced companies

with the shares from the TWSE + TPEx company-basics dumps (`sources/company_shares`). It emits
exactly FinMind's row dicts, so the partitions and every reader are unchanged.

What the numbers are, and are not:

- A plain market-value share, NOT free-float adjusted, over the listed AND OTC companies that have
  a close that day. ETFs, emerging-board stocks and depositary receipts have no share count in the
  free data and are not in the universe. (FinMind's stored weights, while the dataset was served,
  covered the OTC board alone, each partition summing to 100; this universe is both boards.)
- The shares are the CURRENT snapshot, not that day's. There is no free history of share counts, so
  the derivation is forward-only: a day more than `MAX_SNAPSHOT_LAG_DAYS` before the snapshot is not
  derived (it is refused, never approximated), and a snapshot older than that is refused too.
- A company without a close that day (suspended) or without a share count is left out and counted
  in the journal detail; nothing is invented. Never half a market: a board with too few priced
  companies makes the whole answer UNREACHABLE.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Final, Protocol

import structlog

from ingest.jobs.specs import PRICE_EOD_JOB
from ingest.store import ParquetStore
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.company_shares import LISTED, OTC, ShareEntry, SharesSnapshot
from sources.exchange_whole_market._common import ExchangeFetchError, Row

logger = structlog.get_logger(__name__)

SOURCE_ID: Final = "DERIVED_MARKET_VALUE"
# How far a day may be from the shares snapshot's own date: share counts move rarely, and a week
# is the most the measured drift (see docs/backend/modules/22-data-ingest.md section 1g) allows
# before a value would be a guess about a different capital structure.
MAX_SNAPSHOT_LAG_DAYS: Final = 7
# A board with fewer than this share of its companies priced is a partial price partition (one
# exchange's report missing), not a market with some suspended names.
MIN_PRICED_SHARE: Final = 0.8
_ROW_KEY: Final = {Capability.MARKET_VALUE: "rows", Capability.MARKET_VALUE_WEIGHT: "rows"}
_WEIGHT_DECIMALS: Final = 6  # FinMind stored 4; over both boards a small cap needs two more
_CAVEAT: Final = (
    "Derived, not published: close x issued common shares (TWSE + TPEx company basics), a plain "
    "market-value share, not free-float adjusted. Share counts are the current snapshot, so the "
    "figures are only derived for days close to it. ETFs, emerging-board stocks and depositary "
    "receipts are not included."
)


class SharesProvider(Protocol):
    """What the derivation needs of the shares-outstanding source (`CompanySharesAdapter`)."""

    async def fetch_snapshot(self, *, tls_policy: TLSPolicy) -> SharesSnapshot: ...


class DerivedMarketValueSource:
    """`DERIVED_MARKET_VALUE` - `MARKET_VALUE` and `MARKET_VALUE_WEIGHT` from stored closes."""

    source_id = SOURCE_ID

    def __init__(
        self,
        store: ParquetStore,
        shares: SharesProvider,
        *,
        max_lag_days: int = MAX_SNAPSHOT_LAG_DAYS,
    ) -> None:
        self._store = store
        self._shares = shares
        self._max_lag_days = max_lag_days

    @staticmethod
    def supported_capabilities() -> frozenset[Capability]:
        return frozenset(_ROW_KEY)

    async def fetch_whole_market(
        self, cap: Capability, *, tls_policy: TLSPolicy, **params: object
    ) -> SourceResult:
        if cap not in _ROW_KEY:
            return _result(
                cap, [], HealthStatus.UNAVAILABLE_TIER, f"nothing derived for {cap.value}"
            )
        try:
            run_date = date.fromisoformat(str(params.get("start_date")))
        except ValueError:
            return _result(cap, [], HealthStatus.UNREACHABLE, "start_date (YYYY-MM-DD) required")
        closes = self._closes(run_date)
        if not closes:
            return _result(
                cap,
                [],
                HealthStatus.STALE,
                f"no price partition for {run_date.isoformat()} yet: nothing to derive from",
            )
        try:
            snapshot = await self._shares.fetch_snapshot(tls_policy=tls_policy)
        except ExchangeFetchError as exc:
            return _result(cap, [], HealthStatus.UNREACHABLE, f"shares outstanding: {exc.detail}")
        refusal = self._refusal(cap, run_date, snapshot)
        if refusal is not None:
            return refusal
        return self._derive(cap, run_date, snapshot, closes)

    def _closes(self, run_date: date) -> dict[str, float]:
        market = Market.TW.value
        if not self._store.has_rows(PRICE_EOD_JOB.dataset, market, run_date):
            return {}
        closes: dict[str, float] = {}
        for row in self._store.read_partition(PRICE_EOD_JOB.dataset, market, run_date):
            close = row.get("close")
            if isinstance(close, int | float) and close > 0:
                closes[str(row["symbol"])] = float(close)
        return closes

    def _refusal(
        self, cap: Capability, run_date: date, snapshot: SharesSnapshot
    ) -> SourceResult | None:
        gap = (snapshot.as_of - run_date).days
        if gap > self._max_lag_days:
            # Forward-only: the snapshot post-dates the day by more than share counts can be
            # trusted to stand still. `UNAVAILABLE_TIER` = "this source cannot serve this day";
            # with FinMind also denied the runner journals a skip, not a failure.
            return _result(
                cap,
                [],
                HealthStatus.UNAVAILABLE_TIER,
                f"shares snapshot {snapshot.as_of.isoformat()} is {gap} days after "
                f"{run_date.isoformat()}: not derived (no history of share counts)",
            )
        if -gap > self._max_lag_days:
            return _result(
                cap,
                [],
                HealthStatus.UNREACHABLE,
                f"shares snapshot {snapshot.as_of.isoformat()} is {-gap} days older than "
                f"{run_date.isoformat()}: the exchange dump looks stale",
            )
        return None

    def _derive(
        self,
        cap: Capability,
        run_date: date,
        snapshot: SharesSnapshot,
        closes: dict[str, float],
    ) -> SourceResult:
        priced = [e for e in snapshot.entries if e.symbol in closes]
        for board, label in ((LISTED, "TWSE"), (OTC, "TPEx")):
            total, have = snapshot.count(board), sum(1 for e in priced if e.listing_type == board)
            if total == 0 or have < total * MIN_PRICED_SHARE:
                return _result(
                    cap,
                    [],
                    HealthStatus.UNREACHABLE,
                    f"partial: only {have} of {total} {label} companies have a close on "
                    f"{run_date.isoformat()}",
                )
        values = {e.symbol: round(closes[e.symbol] * e.shares) for e in priced}
        rows = (
            _market_value_rows(priced, values, run_date)
            if cap is Capability.MARKET_VALUE
            else _weight_rows(priced, values, run_date)
        )
        no_close = len(snapshot.entries) - len(priced)
        detail = (
            f"close x shares as of {snapshot.as_of.isoformat()}: "
            f"TWSE {sum(1 for e in priced if e.listing_type == LISTED)} + "
            f"TPEx {sum(1 for e in priced if e.listing_type == OTC)} companies"
            f", {no_close} without a close skipped"
            f", {snapshot.without_shares} without a share count skipped"
        )
        logger.info(
            "derived_market_value",
            capability=cap.value,
            run_date=run_date.isoformat(),
            rows=len(rows),
            shares_as_of=snapshot.as_of.isoformat(),
            no_close=no_close,
        )
        return _result(cap, rows, HealthStatus.OK, detail)


def _market_value_rows(entries: list[ShareEntry], values: dict[str, int], day: date) -> list[Row]:
    return [
        {
            "symbol": e.symbol,
            "market": Market.TW.value,
            "trade_date": day.isoformat(),
            "market_value": values[e.symbol],
        }
        for e in entries
    ]


def _weight_rows(entries: list[ShareEntry], values: dict[str, int], day: date) -> list[Row]:
    total = sum(values.values())
    ranked = sorted(entries, key=lambda e: (-values[e.symbol], e.symbol))
    return [
        {
            "symbol": e.symbol,
            "market": Market.TW.value,
            "trade_date": day.isoformat(),
            "rank": rank,
            "stock_name": e.name,
            "weight_per": round(100.0 * values[e.symbol] / total, _WEIGHT_DECIMALS),
            "listing_type": e.listing_type,
        }
        for rank, e in enumerate(ranked, start=1)
    ]


def _result(cap: Capability, rows: list[Row], status: HealthStatus, detail: str) -> SourceResult:
    return SourceResult(
        source_id=SOURCE_ID,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload={_ROW_KEY.get(cap, "rows"): rows},
        health=SourceHealth(status=status, detail=detail),
    )
