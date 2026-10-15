"""US whole-market ingest jobs — the non-TW counterpart to `ingest/jobs.py`'s FinMind pulls.

`ingest/jobs.py::run_whole_market_job` cannot be reused unmodified here: it calls
`ctx.finmind.fetch_whole_market(...)`, a FinMind-only method with no yfinance equivalent, and
expects a FinMind-shaped `SourceResult`. Two jobs live in this module instead:

- **`job_us_price_eod`** — one true batch call (`sources.yfinance.batch.batch_download`,
  `yf.download([...])`), the same "N symbols, one HTTP round trip" shape `ingest/jobs.py`'s
  FinMind jobs get from `fetch_whole_market`'s omit-`data_id` form.
- **`job_us_fundamentals`** — financials/balance-sheet/cash-flow/dividends/splits have no bulk
  endpoint in yfinance, so this calls the existing, already-tested per-symbol
  `YFinanceAdapter.fetch(cap, sym, ...)` once per symbol per capability — reusing
  `sources/yfinance/price_financials.py`'s row-mapping as-is rather than inventing a parallel
  bulk path. This is the one accepted per-symbol-fan-out exception in this module (yfinance
  genuinely has no whole-market financials form), mirrored from how `ingest/jobs.py::
  FINANCIALS_JOB` documents its own irregular-cadence exception rather than hiding it.

Both jobs read their symbol list from `ingest.us_universe.latest_us_symbols` — an empty universe
(the discovery job has never run) is a graceful skipped run, never a crash, matching every other
"nothing to do yet" case elsewhere in `ingest/`.

Symbol/market-scoping, leader-gating, and journaling follow the exact same contract as
`ingest/jobs.py`: `ctx.store.write_partition(dataset, "US", run_date, rows)`, one `JobRun` per
run, `ctx.data_update_publisher.publish_data_updated(...)` on a successful leader write.
"""

from __future__ import annotations

import asyncio
from datetime import date as date_
from datetime import timedelta
from typing import Any, cast

import pandas as pd
import structlog
from sqlalchemy import select

from db.models import AlertRuleRow, WatchlistItemRow
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run
from ingest.spool import RowSpool, sweep_stale_spools
from ingest.us_fundamentals_plan import (
    STATE_DATASET,
    FetchState,
    due_capabilities,
    listed_first,
    load_fetch_state,
)
from ingest.us_universe import latest_us_exchanges, latest_us_symbols
from schemas.enums import Capability, Market
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.yfinance import batch_download

logger = structlog.get_logger(__name__)

US_PRICE_EOD_DATASET = "us_price_eod"

# SPY (S&P 500 ETF) rides along in every `job_us_price_eod` pull as the US broad-market
# reference-index proxy — the direct counterpart to TAIEX riding along in TW's own whole-market
# `TaiwanStockPrice` partition (`analytics/scoring_batch/::_TAIEX_SYMBOL`'s own comment).
# `analytics/scoring_batch/::_reference_index_closes` reads this symbol back out of the same
# partition for the US `taiex_correlation`-equivalent trend-score dimension. A real, liquid,
# continuously-traded ETF (not a synthetic index ticker like `^GSPC`, which yfinance's batch
# `download()` handles inconsistently across multi-symbol requests) — chosen for the same
# "broad, investable, always-quotable" reasons `^GSPC` would be chosen for if it worked reliably
# in this batch path.
US_REFERENCE_INDEX_SYMBOL = "SPY"

# Upper bound on how many user-demanded US symbols ride along in the daily price pull, on top of
# the screener-discovered universe. Alert caps are per user, so total demand is otherwise
# unbounded; this keeps one `batch_download` request a sane size. Beyond it the lowest-sorted
# symbols win (deterministic) and the truncation is logged, never silent.
MAX_DEMANDED_US_SYMBOLS = 500


async def demanded_us_symbols(ctx: IngestContext) -> list[str]:
    """US symbols that users currently have an enabled alert on — directly, or through a
    watchlist-scoped rule — so the daily price pull covers them even when they are outside the
    screener-discovered universe. Without this, an alert on such a symbol has no data and never
    fires, with nothing to tell the user why.

    Reads only the symbol column of the relational store (no user identity leaves this function)
    and returns `[]` when no DB session factory is wired, so a Parquet-only ingest composition is
    unaffected. Tickers are normalised to upper case, matching what alert creation stores."""
    if ctx.session_factory is None:
        return []
    direct = select(AlertRuleRow.symbol).where(
        AlertRuleRow.enabled.is_(True),
        AlertRuleRow.market == Market.US.value,
        AlertRuleRow.symbol.is_not(None),
    )
    via_watchlist = (
        select(WatchlistItemRow.symbol)
        .join(AlertRuleRow, AlertRuleRow.watchlist_id == WatchlistItemRow.watchlist_id)
        .where(AlertRuleRow.enabled.is_(True), WatchlistItemRow.market == Market.US.value)
    )
    async with ctx.session_factory() as session:
        found = {
            str(symbol).strip().upper()
            for query in (direct, via_watchlist)
            for (symbol,) in (await session.execute(query)).all()
            if symbol
        }
    symbols = sorted(found)
    if len(symbols) > MAX_DEMANDED_US_SYMBOLS:
        logger.warning(
            "us_demanded_symbols_truncated", demanded=len(symbols), kept=MAX_DEMANDED_US_SYMBOLS
        )
        symbols = symbols[:MAX_DEMANDED_US_SYMBOLS]
    return symbols


# One capability -> Parquet dataset name per fundamentals family this job covers. Mirrors the
# split `ingest/jobs.py::FINANCIALS_JOB`/`BALANCE_SHEET_JOB`/`CASH_FLOW_JOB`/`DIVIDENDS_JOB` take
# for TW, so a US symbol's financials land in the same dataset-name shape a TW symbol's do.
#
# `ANALYST_CONSENSUS`/`INSTITUTIONAL_HOLDERS`/`INSIDER_ACTIVITY`/`SECTOR_CLASS` were added
# alongside the original five so `analytics/scoring_batch/`'s US-native factor/dimension design
# (`analyst_revision`/`institutional_ownership`/`insider_sentiment`/`sector_momentum` — see that
# module's own docstring) has a whole-market-ingested dataset to read back nightly, rather than a
# live per-symbol fetch at score-compute time (which would violate backend/CLAUDE.md §4's "no
# full-market scan" rule the moment the scoring batch iterated the whole universe).
_FUNDAMENTALS_DATASETS: dict[Capability, str] = {
    Capability.FINANCIALS: "us_financials",
    Capability.BALANCE_SHEET: "us_balance_sheet",
    Capability.CASH_FLOW_STATEMENT: "us_cash_flow",
    Capability.DIVIDENDS: "us_dividends",
    Capability.SPLIT_PRICE: "us_split_price",
    Capability.ANALYST_CONSENSUS: "us_analyst_consensus",
    Capability.INSTITUTIONAL_HOLDERS: "us_institutional_holders",
    Capability.INSIDER_ACTIVITY: "us_insider_activity",
    Capability.SECTOR_CLASS: "us_sector_class",
}

_ONE_DAY = timedelta(days=1)

# yfinance's anonymous/free tier rate-limits aggressively: a tight per-symbol loop (250 symbols
# x 9 capabilities = ~2250 calls with no delay) gets almost every call throttled to
# HealthStatus.UNREACHABLE within seconds — confirmed empirically (455 "Too Many Requests" in
# under 4 minutes on the first real run of this job). A fixed inter-call pause is the accepted
# per-symbol-fan-out exception's own mitigation — this is not a batch endpoint, so pacing the
# calls is the only lever available.
_YFINANCE_THROTTLE_SECONDS = 0.5

# `batch_download` genuinely batches within one call, but a long-running backfill drives many
# such calls back-to-back (one per date) — confirmed empirically on the first live US backfill:
# per-day symbol coverage degraded across consecutive calls (244/250 -> 44/250 within ~3 months
# of simulated trading days) until the run stopped making progress entirely. A pause between
# dates is the same mitigation `_YFINANCE_THROTTLE_SECONDS` is for the fundamentals fan-out,
# just at the "one call per date" granularity instead of "one call per symbol".
_US_PRICE_BACKFILL_THROTTLE_SECONDS = 3.0


def _as_float(value: object) -> float | None:
    """Coerce an untyped upstream pandas/numpy scalar to a plain float, treating NaN/None as
    missing — the same isolated 3rd-party edge `sources/yfinance/_shared.py::_as_float` uses,
    duplicated narrowly here rather than imported, since this module's edge is a raw
    `batch_download` DataFrame cell, not a `SourceResult`-bound frame."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(cast("Any", value))
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    as_float = _as_float(value)
    return int(as_float) if as_float is not None else None


def _multi_symbol_row(row: Any, symbol: str, trade_date_iso: str) -> dict[str, object] | None:  # noqa: ANN401 - pandas Series/DataFrame are untyped (no stubs)
    """One symbol's row out of a MultiIndex-columned frame, or `None` if that symbol has no
    `Close` for this trade date (a genuine gap, e.g. a symbol newly listed after this date)."""
    try:
        close = _as_float(row[(symbol, "Close")])
    except KeyError:
        return None
    if close is None:
        return None
    return {
        "symbol": symbol,
        "market": Market.US.value,
        "trade_date": trade_date_iso,
        "open": _as_float(row.get((symbol, "Open"))),
        "high": _as_float(row.get((symbol, "High"))),
        "low": _as_float(row.get((symbol, "Low"))),
        "close": close,
        "volume": _as_int(row.get((symbol, "Volume"))) or 0,
        "turnover": None,
    }


def _single_symbol_row(row: Any, trade_date_iso: str) -> dict[str, object] | None:  # noqa: ANN401 - pandas Series/DataFrame are untyped (no stubs)
    """The single-symbol MultiIndex-collapse case's row — `symbol` is left unset (`None`) since
    this shape carries no symbol column of its own; `job_us_price_eod` backfills it once the one
    requested symbol is known."""
    close = _as_float(row.get("Close"))
    if close is None:
        return None
    return {
        "symbol": None,
        "market": Market.US.value,
        "trade_date": trade_date_iso,
        "open": _as_float(row.get("Open")),
        "high": _as_float(row.get("High")),
        "low": _as_float(row.get("Low")),
        "close": close,
        "volume": _as_int(row.get("Volume")) or 0,
        "turnover": None,
    }


def _frame_symbols(columns: Any) -> list[str]:  # noqa: ANN401 - pandas Series/DataFrame are untyped (no stubs)
    if not isinstance(columns, pd.MultiIndex):
        return []
    pairs = cast("list[tuple[Any, Any]]", list(columns))
    return sorted({str(sym) for sym, _field in pairs})


def _rows_from_price_frame(frame: Any) -> list[dict[str, object]]:  # noqa: ANN401 - pandas Series/DataFrame are untyped (no stubs)
    """Row-map `batch_download`'s raw `yf.download([...], group_by='ticker')` MultiIndex
    DataFrame into the flat per-symbol-per-day rows `ParquetStore.write_partition` expects — the
    "ingest layer's own row-mapper" responsibility `batch.py`'s docstring calls out explicitly.

    `yf.download` with multiple symbols returns columns as a `(symbol, field)` MultiIndex; a
    single-symbol frame collapses to a plain `field` index instead — both shapes are handled so
    a one-symbol universe (e.g. in a test) doesn't need special-casing by the caller.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    symbols = _frame_symbols(frame.columns)
    rows: list[dict[str, object]] = []
    for trade_date, row in frame.iterrows():
        trade_date_iso = pd.Timestamp(cast("Any", trade_date)).date().isoformat()
        if symbols:
            rows.extend(
                mapped
                for symbol in symbols
                if (mapped := _multi_symbol_row(row, symbol, trade_date_iso)) is not None
            )
        else:
            mapped = _single_symbol_row(row, trade_date_iso)
            if mapped is not None:
                rows.append(mapped)
    return rows


async def job_us_price_eod(run_date: date_, ctx: IngestContext) -> JobRun:
    """Daily US EOD price pull — one `batch_download` call for the whole tracked universe,
    the yfinance equivalent of `ingest/jobs.py::job_price_eod`'s single FinMind whole-market
    call. Calendar-gated against `ctx.us_calendar` — a separate calendar from the TW-oriented
    `ctx.calendar`, since TW-only closures (Lunar New Year, Double Ten, etc.) are ordinary US
    trading days and must not skip this job. Uses the extrapolating check since `run_date` is
    almost always "today", which is always beyond `ctx.us_calendar`'s known range until this
    very call writes its own partition — see
    `TradingDayCalendar.is_trading_day_extrapolating`'s own docstring."""
    if not ctx.us_calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            US_PRICE_EOD_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            US_PRICE_EOD_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    # The screener-discovered universe plus any symbol a user has an alert on (so alerts work for
    # tickers outside the universe too — see `demanded_us_symbols`).
    symbols = sorted({*latest_us_symbols(ctx.store), *await demanded_us_symbols(ctx)})
    if not symbols:
        return journal_run(
            US_PRICE_EOD_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="no US symbol universe yet and no alerted symbols — run "
            "job_us_symbol_universe first",
        )
    # SPY always rides along, even though it's never part of the screener-discovered universe
    # itself — see `US_REFERENCE_INDEX_SYMBOL`'s own comment for why.
    fetch_symbols = [
        *symbols,
        *([] if US_REFERENCE_INDEX_SYMBOL in symbols else [US_REFERENCE_INDEX_SYMBOL]),
    ]

    date_str = run_date.isoformat()
    end_str = (run_date + _ONE_DAY).isoformat()
    try:
        frame = await batch_download(fetch_symbols, start=date_str, end=end_str, interval="1d")
    except Exception as exc:  # never retry-loop tightly — journal and let the next run retry
        logger.warning("us_price_eod_fetch_failed", run_date=date_str, error=str(exc))
        return journal_run(
            US_PRICE_EOD_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=str(exc),
        )

    rows = _rows_from_price_frame(frame)
    if len(fetch_symbols) == 1 and rows:
        # The single-symbol MultiIndex-collapse case (see `_rows_from_price_frame`) — the one
        # requested symbol is unambiguous, so backfill it onto the row the frame-mapper left
        # `symbol=None` on. Effectively dead now that `US_REFERENCE_INDEX_SYMBOL` always makes
        # `fetch_symbols` >= 2, but kept as a defensive fallback rather than removed outright —
        # a future caller of `_rows_from_price_frame` with a genuinely single-symbol frame
        # still needs this backfill.
        for row in rows:
            row["symbol"] = fetch_symbols[0]

    rows_written = ctx.store.write_partition(US_PRICE_EOD_DATASET, Market.US.value, run_date, rows)
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.PRICE_EOD,
        market=Market.US,
        as_of=date_str,
        scope="whole-market",
    )
    return journal_run(
        US_PRICE_EOD_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail="ok",
    )


async def _fetch_one_fundamental(
    ctx: IngestContext, sym: Symbol, cap: Capability
) -> list[dict[str, object]] | None:
    """One (symbol, capability) fetch, classified into rows-to-keep or `None` (a real failure
    the caller should count). Pulled out of `job_us_fundamentals` purely to keep that
    function's loop body shallow; identical fetch/classify logic, just named. A degraded
    (never-raised) result — e.g. rate-limited — is treated the same as a raised exception: both
    must count as a real failure, or a fully-throttled run silently reports SUCCESS with
    rows_written=0 (exactly what happened before this check existed)."""
    try:
        result = await ctx.yfinance.fetch(cap, sym, tls_policy=ctx.tls_policy)  # type: ignore[union-attr]
    except Exception as exc:  # one symbol's failure never aborts the batch
        logger.warning(
            "us_fundamentals_fetch_failed", symbol=sym.id, capability=cap.value, error=str(exc)
        )
        return None
    if result.health.status in (HealthStatus.UNREACHABLE, HealthStatus.UNAVAILABLE_TIER):
        logger.warning(
            "us_fundamentals_fetch_degraded",
            symbol=sym.id,
            capability=cap.value,
            status=result.health.status.value,
            detail=result.health.detail,
        )
        return None
    payload_value = next(iter(result.payload.values()), None)
    if isinstance(payload_value, list):
        return cast("list[dict[str, object]]", payload_value)
    if isinstance(payload_value, dict):
        # `analyst_consensus`/`sector_class` return one row's own dict directly, not a list
        # (`AnalystConsensus`/`SectorClassification` are single-row-per-symbol capabilities,
        # unlike the list-shaped financial-statement-line capabilities `FINANCIALS`/
        # `BALANCE_SHEET`/`CASH_FLOW_STATEMENT`/`DIVIDENDS`/`SPLIT_PRICE` originally fanned out
        # here) — wrap it so the caller's uniform "list of rows" contract still holds.
        return [cast("dict[str, object]", payload_value)]
    return []


# Symbols fetched between checkpoints: at 9 calls a symbol and 0.5 s a call that is about two
# minutes of work, which is all a killed run can lose.
_CHECKPOINT_EVERY_SYMBOLS = 25


async def _collect_fundamentals_rows(  # noqa: PLR0913 - the universe, what is stored, where rows go
    ctx: IngestContext,
    symbols: list[str],
    spool: RowSpool,
    *,
    run_date: date_,
    state: FetchState,
    exchanges: dict[str, str],
) -> int:
    """The paced per-(symbol, capability) fetch loop, pulled out of `job_us_fundamentals`
    purely to keep that function's own branching shallow. Only the capabilities whose stored copy
    is stale are fetched (`us_fundamentals_plan`); each successful fetch is recorded in the state
    dataset, so what came back empty is not retried until its window passes while what failed is.
    Rows go to the spool (a few symbols in memory, a checkpoint to disk every
    `_CHECKPOINT_EVERY_SYMBOLS`), and symbols an earlier, interrupted run already finished are
    skipped. Returns the total failure count (raised exceptions + degraded results, see
    `_fetch_one_fundamental`), earlier run's included."""
    batch: list[str] = []
    batch_failures = 0
    first_call = True
    for symbol_id in symbols:
        if symbol_id in spool.done:
            continue
        sym = Symbol(id=symbol_id, market=Market.US)
        due = due_capabilities(
            state, symbol_id, exchanges.get(symbol_id), run_date, list(_FUNDAMENTALS_DATASETS)
        )
        for cap in due:
            dataset = _FUNDAMENTALS_DATASETS[cap]
            if first_call:
                first_call = False
            else:
                # Paced, not concurrent: yfinance has no bulk fundamentals endpoint, and an
                # unthrottled per-symbol loop over the full universe gets almost every call
                # rate-limited within seconds (empirically: 455 "Too Many Requests" in under
                # 4 minutes on the first real run with no delay). This is the mitigation, not
                # a nice-to-have.
                await asyncio.sleep(_YFINANCE_THROTTLE_SECONDS)
            rows = await _fetch_one_fundamental(ctx, sym, cap)
            if rows is None:
                batch_failures += 1
                continue
            spool.add(dataset, rows)
            spool.add(
                STATE_DATASET,
                [
                    {
                        "symbol": symbol_id,
                        "capability": cap.value,
                        "fetched_on": run_date.isoformat(),
                    }
                ],
            )
        batch.append(symbol_id)
        if len(batch) >= _CHECKPOINT_EVERY_SYMBOLS:
            spool.checkpoint(batch, batch_failures)
            batch, batch_failures = [], 0
    if batch:
        spool.checkpoint(batch, batch_failures)
    return spool.failures


async def _write_fundamentals_partitions(
    ctx: IngestContext, run_date: date_, spool: RowSpool
) -> int:
    """Write each capability's partition and publish the invalidation signal for the ones that
    got new rows — pulled out of `job_us_fundamentals` for the same shallow-branching reason as
    `_collect_fundamentals_rows`. A partition is built from the spool's files plus the previous
    partition's rows for every symbol that was not refreshed this run (`carry_from`), so a symbol
    skipped as still fresh keeps its data. Returns the rows of the partitions written."""
    market = Market.US.value
    total_written = 0
    for cap, dataset in _FUNDAMENTALS_DATASETS.items():
        files = spool.files(dataset)
        previous = ctx.store.latest_partition_on_or_before(dataset, market, run_date)
        carried = previous[1] if previous is not None else []
        if not files and not carried:
            continue
        total_written += ctx.store.write_partition_from_files(
            dataset, market, run_date, files, carry_from=carried
        )
        if files:
            await ctx.data_update_publisher.publish_data_updated(
                capability=cap,
                market=Market.US,
                as_of=run_date.isoformat(),
                scope="whole-market",
            )
    # The fetch dates are bookkeeping, not data: not counted, not announced.
    state_files = spool.files(STATE_DATASET)
    previous_state = ctx.store.latest_partition_on_or_before(STATE_DATASET, market, run_date)
    if state_files or previous_state is not None:
        ctx.store.write_partition_from_files(
            STATE_DATASET,
            market,
            run_date,
            state_files,
            carry_from=previous_state[1] if previous_state is not None else [],
            key=("symbol", "capability"),
        )
    return total_written


def _fundamentals_status(*, failures: int, total_written: int) -> RunStatus:
    if not failures:
        return RunStatus.SUCCESS
    return RunStatus.FAILED if total_written == 0 else RunStatus.PARTIAL


async def job_us_fundamentals(run_date: date_, ctx: IngestContext) -> JobRun:
    """Weekly US financials/balance-sheet/cash-flow/dividends/splits/analyst-consensus/
    institutional-holders/insider-activity/sector-class pull — per-symbol, since
    yfinance has no bulk fundamentals endpoint (the one accepted per-symbol-fan-out exception in
    this module; see the module docstring). One failing symbol never aborts the run: it's
    counted, journaled as a `PARTIAL` run if any symbol failed, and every other symbol's data
    still lands."""
    if not ctx.leader.is_leader():
        return journal_run(
            "us_fundamentals",
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )
    if ctx.yfinance is None:
        return journal_run(
            "us_fundamentals",
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="no YFinanceAdapter wired on this IngestContext",
        )

    exchanges = latest_us_exchanges(ctx.store)
    symbols = listed_first(latest_us_symbols(ctx.store), exchanges)
    if not symbols:
        return journal_run(
            "us_fundamentals",
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="no US symbol universe yet — run job_us_symbol_universe first",
        )

    sweep_stale_spools(ctx.store.root)
    spool = RowSpool(ctx.store.root, "us_fundamentals", run_date)
    spool.resume()  # a run killed part-way for this date continues instead of starting over
    state = load_fetch_state(ctx.store, run_date, _FUNDAMENTALS_DATASETS)
    failures = await _collect_fundamentals_rows(
        ctx, symbols, spool, run_date=run_date, state=state, exchanges=exchanges
    )
    total_written = await _write_fundamentals_partitions(ctx, run_date, spool)
    spool.discard()
    status = _fundamentals_status(failures=failures, total_written=total_written)
    return journal_run(
        "us_fundamentals",
        run_date,
        rows_written=total_written,
        status=status,
        detail=f"{failures} symbol fetch(es) failed" if failures else "ok",
    )


async def backfill_us_price_range(
    ctx: IngestContext,
    *,
    start: date_,
    end: date_,
    only_missing: bool = True,
) -> tuple[JobRun, ...]:
    """Gap-fill/backfill `job_us_price_eod` over `[start, end]` — the US-price counterpart to
    `ingest/backfill.py::backfill_range`, kept here rather than there since that module's
    `detect_missing_trading_days`/`backfill_range` are typed against `WholeMarketJobSpec`
    (FinMind-shaped), while this job takes no spec at all. Same idempotency/resumability
    contract: each date is independently checked/run, so a partial backfill can simply be
    re-invoked."""
    dates = ctx.us_calendar.business_days_between(start, end)
    if only_missing:
        market = Market.US.value
        dates = tuple(
            d for d in dates if not ctx.store.has_partition(US_PRICE_EOD_DATASET, market, d)
        )
    runs: list[JobRun] = []
    for i, run_date in enumerate(dates):
        if i:
            # Paced, not back-to-back: see `_US_PRICE_BACKFILL_THROTTLE_SECONDS`'s docstring —
            # `batch_download`'s own internal per-symbol threading has no cooldown of its own,
            # and consecutive calls within one backfill run compound into escalating rate-limits.
            await asyncio.sleep(_US_PRICE_BACKFILL_THROTTLE_SECONDS)
        runs.append(await job_us_price_eod(run_date, ctx))
    return tuple(runs)


__all__ = [
    "US_PRICE_EOD_DATASET",
    "backfill_us_price_range",
    "job_us_fundamentals",
    "job_us_price_eod",
]
