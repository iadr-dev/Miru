"""Shared ingest run context — bundles the collaborators every job/backfill/preflight call
needs (`FinMindAdapter`, `ParquetStore`, `LeaderElection`, `TradingDayCalendar`, TLS policy)
so call sites take one object instead of five positional/keyword dependencies. Pure wiring,
no behavior of its own.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dataaccess.fred import FredGateway
from ingest.data_updates import DataUpdatePublisher, NoOpDataUpdatePublisher
from ingest.leader import LeaderElection
from ingest.store import ParquetStore
from schemas.enums import Market, TLSPolicy
from sources.base import WholeMarketSource
from sources.finmind import FinMindAdapter
from sources.yfinance import YFinanceAdapter


@dataclass(frozen=True)
class IngestContext:
    """Everything a job function needs to run one whole-market-by-date pass."""

    finmind: FinMindAdapter
    store: ParquetStore
    leader: LeaderElection
    calendar: TradingDayCalendar
    tls_policy: TLSPolicy = TLSPolicy.STRICT
    market: Market = Market.TW
    # A separate calendar for `ingest/us_jobs.py`'s own gating, built from US price partitions —
    # deliberately NOT the same object as `calendar` above. TW and US holiday sets diverge (e.g.
    # Lunar New Year/Double Ten are TW-only closures where the US market trades normally); a
    # calendar built from one market's known trading dates would wrongly gate the other market's
    # jobs. Defaults to a fresh naive Mon-Fri `TradingDayCalendar()` so any existing test/caller
    # that never sets this keeps the prior degraded-but-safe behavior.
    us_calendar: TradingDayCalendar = field(default_factory=TradingDayCalendar)
    # The batch-completion invalidation signal — only the leader publishes it (the same
    # leadership gate that guards the write). Defaults to a no-op so an ingest run with no
    # transport wired still lands its partition; the client falls back to boundary revalidation.
    data_update_publisher: DataUpdatePublisher = field(default_factory=NoOpDataUpdatePublisher)
    # A DB session factory for jobs that write the relational store (the theme seed) rather than
    # the Parquet store. Defaults to None: the whole-market Parquet jobs never touch it, and only
    # a relational job asserts its presence — so the common ingest path composes without a DB.
    session_factory: async_sessionmaker[AsyncSession] | None = None
    # Only the US whole-market jobs (`ingest/us_jobs.py`) use this — defaults to `None` so every
    # existing FinMind-only job/test is unaffected; those jobs assert it's set themselves rather
    # than this dataclass enforcing a cross-field invariant it has no business knowing about.
    yfinance: YFinanceAdapter | None = None
    # Only `ingest/jobs/fred.py` uses this (US macro statistics). `None` or an unconfigured API
    # key makes that job journal SKIPPED.
    fred: FredGateway | None = None
    # The free official TWSE + TPEx reports, asked BEFORE FinMind for the datasets they cover (the
    # order is `config/whole_market_source_priority.yaml`; `ingest/jobs/source_chain.py` walks it).
    # `None` leaves FinMind as the only source - the default, so a test or caller that never wires
    # it makes no exchange request.
    exchange: WholeMarketSource | None = None
    # The free official company filings (monthly revenue, ...), asked before FinMind for the
    # period-keyed datasets they cover - same chain, same `None`-means-FinMind-only default.
    filings: WholeMarketSource | None = None


__all__ = ["IngestContext"]
