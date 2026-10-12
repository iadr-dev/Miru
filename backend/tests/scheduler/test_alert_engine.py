"""Event-driven alert evaluation (modules/14): template evaluation over ingested partitions, edge
triggering, trigger modes, watchlist fan-out, the reconciliation sweep, and idempotency — all
against an in-memory fake reader, no Parquet and no network."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from accounts.hooks import EntitlementResolver
from db.models import WatchlistItemRow, WatchlistRow
from governance.textscan import check_output
from ingest.jobs import (
    ATTENTION_STOCKS_DATASET,
    DISPOSITION_EVENTS_DATASET,
    ETF_NAV_DATASET,
    INSTITUTIONAL_FLOW_JOB,
    MARGIN_SHORT_JOB,
    MATERIAL_ANNOUNCEMENTS_DATASET,
    MONTH_REVENUE_JOB,
    PRICE_EOD_JOB,
)
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.us_jobs import US_PRICE_EOD_DATASET
from scheduler.delivery.channels import InAppChannel
from scheduler.evaluator import SnapshotView, evaluate_condition
from scheduler.models import DeliveryChannelKind, TriggerMode
from scheduler.quota import AllowAllQuotaGate
from scheduler.store import SqlAlchemySchedulerStore
from scheduler.templates import (
    AttentionListEntry,
    ClosePrice,
    DailyChange,
    DispositionListEntry,
    EtfPremiumDiscount,
    InstitutionalStreak,
    MarginBalanceChange,
    MaterialAnnouncementPublished,
    MonthRevenuePublished,
    VolumeSurge,
    capability_of,
    dataset_of,
    probe_dataset,
)
from scheduler.worker import SchedulerContext, SchedulerWorker, _safe_body, as_utc
from schemas.alerts import TW_ONLY_KINDS
from schemas.enums import Capability, Market, Tier
from tests.scheduler.conftest import insert_user

PRICE = PRICE_EOD_JOB.dataset
FLOW = INSTITUTIONAL_FLOW_JOB.dataset
TODAY = datetime.now(ZoneInfo("Asia/Taipei")).date()


def day(n: int) -> date:
    return TODAY + timedelta(days=n)


class FakeReader:
    """Stands in for `ParquetMarketDataReader`: `(dataset, market, date) -> rows`."""

    def __init__(self) -> None:
        self.parts: dict[tuple[str, str, date], list[dict[str, object]]] = {}

    def put(
        self, dataset: str, when: date, rows: list[dict[str, object]], market: str = "TW"
    ) -> None:
        self.parts[(dataset, market, when)] = rows

    def read_partition(self, dataset: str, market: str, run_date: date) -> list[dict[str, object]]:
        return list(self.parts.get((dataset, market, run_date), []))

    def has_partition(self, dataset: str, market: str, run_date: date) -> bool:
        return (dataset, market, run_date) in self.parts


def bar(symbol: str, close: float, volume: int = 1000) -> dict[str, object]:
    return {"symbol": symbol, "close": close, "volume": volume}


def flow(symbol: str, buy: int, sell: int, investor: str = "Foreign_Investor") -> dict[str, object]:
    return {"symbol": symbol, "investor": investor, "buy": buy, "sell": sell}


def make_worker(
    session_factory, reader: FakeReader, *, leader: object | None = None
) -> SchedulerWorker:
    return SchedulerWorker(
        SchedulerContext(
            session_factory=session_factory,
            orchestrator=None,  # type: ignore[arg-type]  # alerts never touch the orchestrator
            router=None,  # type: ignore[arg-type]
            channels={DeliveryChannelKind.IN_APP: InAppChannel()},
            entitlement_resolver=EntitlementResolver(),
            leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
            quota_gate=AllowAllQuotaGate(),
            reader=reader,
        )
    )


async def create_rule(session_factory, user: str, condition, **kwargs):
    kwargs.setdefault("symbol", "2330")
    kwargs.setdefault("market", Market.TW)
    async with session_factory() as session:
        rule = await SqlAlchemySchedulerStore(session).create_alert(
            user_id=user, condition=condition, channels=(), **kwargs
        )
        await session.commit()
    return rule


async def notifications(session_factory, user: str):
    async with session_factory() as session:
        return await SqlAlchemySchedulerStore(session).list_notifications(user_id=user)


async def get_rule(session_factory, user: str, rule_id: str):
    async with session_factory() as session:
        return await SqlAlchemySchedulerStore(session).get_alert(user_id=user, alert_id=rule_id)


async def signal(
    worker: SchedulerWorker, cap: Capability, when: date, *, now: datetime | None = None
):
    return await worker.handle_data_updated(
        capability=cap, market=Market.TW, as_of=when.isoformat(), now=now or datetime.now(UTC)
    )


# ---- pure template evaluation ---------------------------------------------------------------


def evaluate(condition, reader: FakeReader, when: date, symbol: str = "2330"):
    return evaluate_condition(
        condition, symbol=symbol, market=Market.TW, as_of=when, view=SnapshotView(reader)
    )


def test_close_price_above_and_below() -> None:
    r = FakeReader()
    r.put(PRICE, day(0), [bar("2330", 612.0)])
    above = evaluate(ClosePrice(comparator="above", value=600), r, day(0))
    below = evaluate(ClosePrice(comparator="below", value=600), r, day(0))
    assert above is not None
    assert above.met is True
    assert "612" in above.message
    assert below is not None
    assert below.met is False


def test_missing_symbol_or_partition_is_not_evaluable() -> None:
    r = FakeReader()
    r.put(PRICE, day(0), [bar("2317", 100.0)])
    condition = ClosePrice(comparator="above", value=1)
    assert evaluate(condition, r, day(0)) is None  # symbol absent
    assert evaluate(condition, r, day(1)) is None  # no partition


def test_daily_change_up_and_down() -> None:
    r = FakeReader()
    r.put(PRICE, day(0), [bar("2330", 100.0)])
    r.put(PRICE, day(1), [bar("2330", 106.0)])
    up = evaluate(DailyChange(direction="up", percent=5), r, day(1))
    down = evaluate(DailyChange(direction="down", percent=5), r, day(1))
    assert up is not None
    assert up.met is True
    assert "+6.00%" in up.message
    assert down is not None
    assert down.met is False


def test_daily_change_without_prior_day_is_not_evaluable() -> None:
    r = FakeReader()
    r.put(PRICE, day(1), [bar("2330", 106.0)])
    assert evaluate(DailyChange(direction="up", percent=5), r, day(1)) is None


def test_volume_surge_against_trailing_average() -> None:
    r = FakeReader()
    for n in range(-6, 0):
        r.put(PRICE, day(n), [bar("2330", 100.0, volume=1000)])
    r.put(PRICE, day(0), [bar("2330", 100.0, volume=3000)])
    hit = evaluate(VolumeSurge(multiple=2, window=5), r, day(0))
    miss = evaluate(VolumeSurge(multiple=4, window=5), r, day(0))
    assert hit is not None
    assert hit.met is True
    assert "3.0 倍" in hit.message
    assert miss is not None
    assert miss.met is False


def test_institutional_streak_counts_consecutive_days() -> None:
    r = FakeReader()
    r.put(FLOW, day(-2), [flow("2330", 500, 100)])
    r.put(FLOW, day(-1), [flow("2330", 700, 200)])
    r.put(FLOW, day(0), [flow("2330", 900, 100)])
    hit = evaluate(InstitutionalStreak(investor="foreign", direction="buy", days=3), r, day(0))
    assert hit is not None
    assert hit.met is True
    assert "連續 3 日淨買超" in hit.message


def test_institutional_streak_broken_by_an_opposite_day() -> None:
    r = FakeReader()
    r.put(FLOW, day(-2), [flow("2330", 500, 100)])
    r.put(FLOW, day(-1), [flow("2330", 100, 700)])  # net sell breaks the run
    r.put(FLOW, day(0), [flow("2330", 900, 100)])
    result = evaluate(InstitutionalStreak(investor="foreign", direction="buy", days=3), r, day(0))
    assert result is not None
    assert result.met is False


def test_institutional_streak_sums_dealer_subaccounts_and_ignores_other_investors() -> None:
    r = FakeReader()
    rows = [
        flow("2330", 100, 0, "Dealer_self"),
        flow("2330", 100, 0, "Dealer_Hedging"),
        flow("2330", 0, 9999, "Foreign_Investor"),
    ]
    r.put(FLOW, day(-1), rows)
    r.put(FLOW, day(0), rows)
    dealer = evaluate(InstitutionalStreak(investor="dealer", direction="buy", days=2), r, day(0))
    assert dealer is not None
    assert dealer.met is True


def test_attention_entry_present_vs_absent_partition() -> None:
    r = FakeReader()
    r.put(ATTENTION_STOCKS_DATASET, day(0), [{"symbol": "2330"}])
    listed = evaluate(AttentionListEntry(), r, day(0))
    not_listed = evaluate(AttentionListEntry(), r, day(0), symbol="2317")
    assert listed is not None
    assert listed.met is True
    assert not_listed is not None
    assert not_listed.met is False  # absent from an ingested list is a real "not flagged"
    assert evaluate(AttentionListEntry(), r, day(1)) is None  # no list ingested that day


def test_every_template_phrasing_passes_the_output_guardrail() -> None:
    r = FakeReader()
    for n in range(-6, 1):
        r.put(PRICE, day(n), [bar("2330", 100.0 + n, volume=1000 if n < 0 else 5000)])
        r.put(FLOW, day(n), [flow("2330", 900, 100)])
    r.put(ATTENTION_STOCKS_DATASET, day(0), [{"symbol": "2330"}])
    conditions = [
        ClosePrice(comparator="above", value=50),
        DailyChange(direction="up", percent=0.5),
        VolumeSurge(multiple=2, window=5),
        InstitutionalStreak(investor="foreign", direction="buy", days=3),
        AttentionListEntry(),
    ]
    for condition in conditions:
        result = evaluate(condition, r, day(0))
        assert result is not None
        verdict = check_output(_safe_body(result.message, condition, "2330"), has_market_data=False)
        assert verdict.reason != "trading_language_redacted", result.message


def test_safe_body_fails_closed_on_flagged_text() -> None:
    body = _safe_body(
        "建議買進 2330，目標價 700", ClosePrice(comparator="above", value=600), "2330"
    )
    assert "買進" not in body
    assert "目標價" not in body
    assert "非投資建議" in body


# ---- engine: delivery, edge-triggering, modes ------------------------------------------------


async def test_fires_when_data_lands_with_fact_body_and_footer(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(session_factory, user, ClosePrice(comparator="above", value=600))
    reader = FakeReader()
    reader.put(PRICE, day(0), [bar("2330", 612.0)])
    fired = await signal(make_worker(session_factory, reader), Capability.PRICE_EOD, day(0))
    assert fired == [rule.id]
    [note] = await notifications(session_factory, user)
    body = str(note.payload["body"])
    assert "收盤價 612" in body
    assert "非投資建議" in body
    assert note.source_id == rule.id


async def test_edge_trigger_fires_on_transition_not_every_day(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(session_factory, user, ClosePrice(comparator="above", value=600))
    reader = FakeReader()
    worker = make_worker(session_factory, reader)
    for n, close in enumerate([612.0, 620.0, 590.0, 605.0]):
        reader.put(PRICE, day(n), [bar("2330", close)])
    assert await signal(worker, Capability.PRICE_EOD, day(0)) == [rule.id]  # crosses above
    assert await signal(worker, Capability.PRICE_EOD, day(1)) == []  # still above: silent
    assert await signal(worker, Capability.PRICE_EOD, day(2)) == []  # fell back below: re-armed
    assert await signal(worker, Capability.PRICE_EOD, day(3)) == [rule.id]  # crosses again
    assert len(await notifications(session_factory, user)) == 2
    assert (await get_rule(session_factory, user, rule.id)).state["fire_count"] == 2


async def test_every_occurrence_fires_each_day_while_true(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(
        session_factory,
        user,
        ClosePrice(comparator="above", value=600),
        trigger_mode=TriggerMode.EVERY_OCCURRENCE,
    )
    reader = FakeReader()
    worker = make_worker(session_factory, reader)
    for n in range(3):
        reader.put(PRICE, day(n), [bar("2330", 610.0 + n)])
        assert await signal(worker, Capability.PRICE_EOD, day(n)) == [rule.id]
    assert len(await notifications(session_factory, user)) == 3


async def test_once_mode_disables_after_firing(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(
        session_factory,
        user,
        ClosePrice(comparator="above", value=600),
        trigger_mode=TriggerMode.ONCE,
    )
    reader = FakeReader()
    reader.put(PRICE, day(0), [bar("2330", 612.0)])
    worker = make_worker(session_factory, reader)
    assert await signal(worker, Capability.PRICE_EOD, day(0)) == [rule.id]
    stored = await get_rule(session_factory, user, rule.id)
    assert stored.enabled is False
    assert stored.auto_paused_reason == "completed"
    reader.put(PRICE, day(1), [bar("2330", 500.0)])
    reader.put(PRICE, day(2), [bar("2330", 700.0)])
    assert await signal(worker, Capability.PRICE_EOD, day(2)) == []  # disabled rules are not run


async def test_same_as_of_is_evaluated_once_and_redelivery_is_a_noop(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(session_factory, user, ClosePrice(comparator="above", value=600))
    reader = FakeReader()
    reader.put(PRICE, day(0), [bar("2330", 612.0)])
    worker = make_worker(session_factory, reader)
    assert await signal(worker, Capability.PRICE_EOD, day(0)) == [rule.id]
    assert await signal(worker, Capability.PRICE_EOD, day(0)) == []  # watermark already at day(0)
    assert len(await notifications(session_factory, user)) == 1
    async with session_factory() as session:
        mark = await SqlAlchemySchedulerStore(session).get_eval_mark(
            capability=Capability.PRICE_EOD.value, market="TW"
        )
    assert mark == day(0).isoformat()


async def test_rule_never_fires_on_data_that_predates_it(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await create_rule(session_factory, user, ClosePrice(comparator="above", value=1))
    reader = FakeReader()
    reader.put(PRICE, day(-1), [bar("2330", 612.0)])  # yesterday's close, before the rule existed
    assert await signal(make_worker(session_factory, reader), Capability.PRICE_EOD, day(-1)) == []
    assert await notifications(session_factory, user) == []


async def test_expired_rule_is_paused_not_fired(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(
        session_factory,
        user,
        ClosePrice(comparator="above", value=1),
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    reader = FakeReader()
    reader.put(PRICE, day(0), [bar("2330", 612.0)])
    assert await signal(make_worker(session_factory, reader), Capability.PRICE_EOD, day(0)) == []
    stored = await get_rule(session_factory, user, rule.id)
    assert stored.enabled is False
    assert stored.auto_paused_reason == "expired"
    assert await notifications(session_factory, user) == []


async def test_cooldown_suppresses_a_second_fire(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(
        session_factory,
        user,
        ClosePrice(comparator="above", value=600),
        trigger_mode=TriggerMode.EVERY_OCCURRENCE,
        cooldown_seconds=3600,
    )
    reader = FakeReader()
    worker = make_worker(session_factory, reader)
    reader.put(PRICE, day(0), [bar("2330", 612.0)])
    reader.put(PRICE, day(1), [bar("2330", 613.0)])
    now = datetime.now(UTC)
    assert await signal(worker, Capability.PRICE_EOD, day(0), now=now) == [rule.id]
    assert await signal(worker, Capability.PRICE_EOD, day(1), now=now + timedelta(minutes=5)) == []


async def test_watchlist_rule_fans_out_per_symbol(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            WatchlistRow(
                id="wl",
                user_id=user,
                name="自選",
                market="TW",
                asset_class="stock",
                sort_order="a",
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        for i, symbol in enumerate(["2330", "2317", "2454"]):
            session.add(
                WatchlistItemRow(
                    id=f"i{i}",
                    watchlist_id="wl",
                    user_id=user,
                    symbol=symbol,
                    market="TW",
                    asset_class="stock",
                    sort_order=str(i),
                    added_at=now,
                )
            )
        await session.commit()
    rule = await create_rule(
        session_factory,
        user,
        ClosePrice(comparator="above", value=100),
        symbol=None,
        market=None,
        watchlist_id="wl",
    )
    reader = FakeReader()
    reader.put(PRICE, day(0), [bar("2330", 150.0), bar("2317", 90.0), bar("2454", 120.0)])
    assert await signal(make_worker(session_factory, reader), Capability.PRICE_EOD, day(0)) == [
        rule.id
    ]
    sources = sorted(n.source_id for n in await notifications(session_factory, user))
    assert sources == [f"{rule.id}:2330", f"{rule.id}:2454"]  # 2317 is below the level
    met = (await get_rule(session_factory, user, rule.id)).state["met"]
    assert met == {"2330": True, "2317": False, "2454": True}


async def test_rule_channels_fall_back_to_user_preferences(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    async with session_factory() as session:
        await SqlAlchemySchedulerStore(session).upsert_preferences(
            user_id=user,
            channels=(DeliveryChannelKind.IN_APP,),
            enabled_categories=(),
            quiet_hours=None,
            morning_digest=False,
        )
        await session.commit()
    await create_rule(session_factory, user, ClosePrice(comparator="above", value=1))
    reader = FakeReader()
    reader.put(PRICE, day(0), [bar("2330", 10.0)])
    await signal(make_worker(session_factory, reader), Capability.PRICE_EOD, day(0))
    [note] = await notifications(session_factory, user)
    assert note.channel is DeliveryChannelKind.IN_APP


# ---- reconciliation sweep & guards ------------------------------------------------------------


async def test_reconcile_catches_data_the_signal_missed(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(session_factory, user, ClosePrice(comparator="above", value=600))
    reader = FakeReader()
    reader.put(PRICE, TODAY, [bar("2330", 612.0)])  # landed; no data.updated was ever delivered
    worker = make_worker(session_factory, reader)
    assert await worker.reconcile_alerts() == [rule.id]
    assert await worker.reconcile_alerts() == []  # idempotent: watermark advanced
    assert len(await notifications(session_factory, user)) == 1


async def test_reconcile_with_no_landed_data_does_nothing(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await create_rule(session_factory, user, ClosePrice(comparator="above", value=1))
    assert await make_worker(session_factory, FakeReader()).reconcile_alerts() == []


async def test_non_leader_evaluates_nothing(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await create_rule(session_factory, user, ClosePrice(comparator="above", value=1))
    reader = FakeReader()
    reader.put(PRICE, TODAY, [bar("2330", 10.0)])
    worker = make_worker(session_factory, reader, leader=NeverLeader())
    assert await worker.reconcile_alerts() == []
    assert await signal(worker, Capability.PRICE_EOD, TODAY) == []
    assert await notifications(session_factory, user) == []


@pytest.mark.parametrize("bad", ["", "not-a-date", "2026-13-40"])
async def test_malformed_signal_as_of_is_ignored(session_factory, bad: str) -> None:
    worker = make_worker(session_factory, FakeReader())
    assert (
        await worker.handle_data_updated(
            capability=Capability.PRICE_EOD, market=Market.TW, as_of=bad
        )
        == []
    )


async def test_rules_only_run_for_their_own_capability(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await create_rule(session_factory, user, ClosePrice(comparator="above", value=1))
    reader = FakeReader()
    reader.put(PRICE, day(0), [bar("2330", 10.0)])
    worker = make_worker(session_factory, reader)
    # A chip-flow update must not evaluate (or mark) price rules.
    assert await signal(worker, Capability.INSTITUTIONAL_FLOW, day(0)) == []
    assert await notifications(session_factory, user) == []


def test_as_utc_labels_naive_values_utc_instead_of_assuming_local() -> None:
    naive = datetime(2026, 10, 3, 14, 0)  # what SQLite hands back for a stored UTC instant
    assert as_utc(naive) == datetime(2026, 10, 3, 14, 0, tzinfo=UTC)
    aware = datetime(2026, 10, 3, 22, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    assert as_utc(aware) == datetime(2026, 10, 3, 14, 0, tzinfo=UTC)


async def test_rule_expiring_in_the_future_is_not_treated_as_expired(session_factory) -> None:
    # Regression: on SQLite expires_at comes back naive-UTC; interpreting it as host-local time
    # made a rule expiring in 2 hours look expired on any machine east of UTC.
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(
        session_factory,
        user,
        ClosePrice(comparator="above", value=1),
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    reader = FakeReader()
    reader.put(PRICE, day(0), [bar("2330", 612.0)])
    fired = await signal(make_worker(session_factory, reader), Capability.PRICE_EOD, day(0))
    assert fired == [rule.id]
    assert (await get_rule(session_factory, user, rule.id)).enabled is True


# ---- US symbols: a different price dataset than Taiwan ------------------------------------
# Regression: the engine used Taiwan's `TaiwanStockPrice` dataset name for every market, so a US
# price alert looked in a partition that never exists and silently never fired.


def ny_today():
    """Evaluated per test, not at import: a module-level date goes stale if the run spans New York's
    midnight, and the engine would then look at a different day than the test seeded."""
    return datetime.now(ZoneInfo("America/New_York")).date()


async def test_us_price_alert_reads_the_us_dataset_and_fires(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(
        session_factory,
        user,
        ClosePrice(comparator="above", value=200),
        symbol="AAPL",
        market=Market.US,
    )
    reader = FakeReader()
    reader.put(US_PRICE_EOD_DATASET, ny_today(), [bar("AAPL", 215.0)], market="US")
    worker = make_worker(session_factory, reader)
    fired = await worker.handle_data_updated(
        capability=Capability.PRICE_EOD, market=Market.US, as_of=ny_today().isoformat()
    )
    assert fired == [rule.id]
    [note] = await notifications(session_factory, user)
    assert "AAPL" in str(note.payload["title"])


async def test_us_reconcile_sweep_finds_the_us_partition(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(
        session_factory,
        user,
        ClosePrice(comparator="above", value=200),
        symbol="AAPL",
        market=Market.US,
    )
    reader = FakeReader()
    reader.put(US_PRICE_EOD_DATASET, ny_today(), [bar("AAPL", 215.0)], market="US")
    assert await make_worker(session_factory, reader).reconcile_alerts() == [rule.id]


async def test_a_us_rule_ignores_the_taiwan_price_partition(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await create_rule(
        session_factory,
        user,
        ClosePrice(comparator="above", value=1),
        symbol="AAPL",
        market=Market.US,
    )
    reader = FakeReader()
    # Only a Taiwan-dataset partition exists (even one carrying an "AAPL" row): never US data.
    reader.put(PRICE, ny_today(), [bar("AAPL", 215.0)], market="US")
    worker = make_worker(session_factory, reader)
    fired = await worker.handle_data_updated(
        capability=Capability.PRICE_EOD, market=Market.US, as_of=ny_today().isoformat()
    )
    assert fired == []


# ---- 處置股 entry and 月營收 published (Taiwan-only event templates) ----------------

DISPOSITION = DISPOSITION_EVENTS_DATASET
MONTH_REVENUE = MONTH_REVENUE_JOB.dataset


def disposition_row(symbol: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "announced_date": day(0).isoformat(),
        "period_start": "2026-10-05",
        "period_end": "2026-10-19",
    }


def revenue_row(symbol: str, trade_date: date, *, year: int = 2026, month: int = 9):
    return {
        "symbol": symbol,
        "trade_date": trade_date.isoformat(),
        "revenue": 1_000_000,
        "revenue_year": year,
        "revenue_month": month,
    }


def test_disposition_entry_listed_with_the_disposal_period() -> None:
    r = FakeReader()
    r.put(DISPOSITION, day(0), [disposition_row("2330")])
    result = evaluate(DispositionListEntry(), r, day(0))
    assert result is not None
    assert result.met is True
    assert "處置期間 2026-10-05 至 2026-10-19" in result.message


def test_disposition_entry_absent_from_an_ingested_list_is_a_real_not_listed() -> None:
    r = FakeReader()
    r.put(DISPOSITION, day(0), [disposition_row("2317")])
    result = evaluate(DispositionListEntry(), r, day(0), symbol="2330")
    assert result is not None
    assert result.met is False
    assert evaluate(DispositionListEntry(), r, day(1)) is None  # no list ingested that day


def test_month_revenue_counts_only_a_row_dated_the_evaluation_day() -> None:
    r = FakeReader()
    r.put(MONTH_REVENUE, day(0), [revenue_row("2330", day(0))])
    hit = evaluate(MonthRevenuePublished(), r, day(0))
    assert hit is not None
    assert hit.met is True
    assert "2026 年 9 月" in hit.message


def test_month_revenue_ignores_rows_dated_on_other_days() -> None:
    # If the upstream ever echoes later/earlier rows inside a day's partition, an old filing must
    # not look like a fresh publication.
    r = FakeReader()
    r.put(MONTH_REVENUE, day(0), [revenue_row("2330", day(-3)), revenue_row("2330", day(2))])
    result = evaluate(MonthRevenuePublished(), r, day(0))
    assert result is not None
    assert result.met is False


def test_month_revenue_no_row_for_the_symbol_is_not_published_and_no_partition_is_unevaluable() -> (
    None
):
    r = FakeReader()
    r.put(MONTH_REVENUE, day(0), [revenue_row("2317", day(0))])
    quiet = evaluate(MonthRevenuePublished(), r, day(0), symbol="2330")
    assert quiet is not None
    assert quiet.met is False
    assert evaluate(MonthRevenuePublished(), r, day(1)) is None


def test_the_new_templates_read_their_own_datasets_and_capabilities() -> None:
    assert dataset_of(DispositionListEntry()) == DISPOSITION
    assert dataset_of(MonthRevenuePublished()) == MONTH_REVENUE
    assert capability_of(DispositionListEntry()) is Capability.DISPOSITION_EVENTS
    assert capability_of(MonthRevenuePublished()) is Capability.MONTH_REVENUE
    assert probe_dataset(Capability.DISPOSITION_EVENTS, Market.TW) == DISPOSITION
    assert probe_dataset(Capability.MONTH_REVENUE, Market.TW) == MONTH_REVENUE


def test_new_template_phrasing_passes_the_output_guardrail() -> None:
    r = FakeReader()
    r.put(DISPOSITION, day(0), [disposition_row("2330")])
    r.put(MONTH_REVENUE, day(0), [revenue_row("2330", day(0))])
    for condition in (DispositionListEntry(), MonthRevenuePublished()):
        result = evaluate(condition, r, day(0))
        assert result is not None
        verdict = check_output(_safe_body(result.message, condition, "2330"), has_market_data=False)
        assert verdict.reason != "trading_language_redacted", result.message


async def test_disposition_rule_fires_on_entry_then_stays_quiet_while_listed(
    session_factory,
) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(session_factory, user, DispositionListEntry())
    reader = FakeReader()
    worker = make_worker(session_factory, reader)
    reader.put(DISPOSITION, day(0), [disposition_row("2330")])
    reader.put(DISPOSITION, day(1), [disposition_row("2330")])  # still listed
    reader.put(DISPOSITION, day(2), [])  # released
    reader.put(DISPOSITION, day(3), [disposition_row("2330")])  # listed again
    cap = Capability.DISPOSITION_EVENTS
    assert await signal(worker, cap, day(0)) == [rule.id]
    assert await signal(worker, cap, day(1)) == []
    assert await signal(worker, cap, day(2)) == []
    assert await signal(worker, cap, day(3)) == [rule.id]
    bodies = [str(n.payload["body"]) for n in await notifications(session_factory, user)]
    assert len(bodies) == 2
    assert all("處置股" in b and "非投資建議" in b for b in bodies)


async def test_month_revenue_rule_fires_only_on_the_publication_day(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(session_factory, user, MonthRevenuePublished())
    reader = FakeReader()
    worker = make_worker(session_factory, reader)
    reader.put(MONTH_REVENUE, day(0), [])
    reader.put(MONTH_REVENUE, day(1), [revenue_row("2330", day(1))])
    reader.put(MONTH_REVENUE, day(2), [])
    cap = Capability.MONTH_REVENUE
    assert await signal(worker, cap, day(0)) == []
    assert await signal(worker, cap, day(1)) == [rule.id]
    assert await signal(worker, cap, day(2)) == []


async def test_reconcile_sweep_discovers_the_new_datasets(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    disposition = await create_rule(session_factory, user, DispositionListEntry())
    revenue = await create_rule(session_factory, user, MonthRevenuePublished())
    reader = FakeReader()
    reader.put(DISPOSITION, TODAY, [disposition_row("2330")])
    reader.put(MONTH_REVENUE, TODAY, [revenue_row("2330", TODAY)])
    fired = await make_worker(session_factory, reader).reconcile_alerts()
    assert sorted(fired) == sorted([disposition.id, revenue.id])


# ---- 融資融券 change and ETF 折溢價 (single-row Taiwan templates) ---------------------

MARGIN = MARGIN_SHORT_JOB.dataset


def margin_row(symbol: str, *, margin: tuple[int, int], short: tuple[int, int] = (100, 100)):
    return {
        "symbol": symbol,
        "margin_purchase_yesterday_balance": margin[0],
        "margin_purchase_today_balance": margin[1],
        "short_sale_yesterday_balance": short[0],
        "short_sale_today_balance": short[1],
    }


def nav_row(symbol: str, pct: float | None, *, close: float = 50.0, nav: float = 49.5):
    return {
        "symbol": symbol,
        "premium_discount_pct": pct,
        "close_price": close,
        "estimated_nav": nav,
    }


def test_margin_increase_measured_from_the_rows_own_two_balances() -> None:
    r = FakeReader()
    r.put(MARGIN, day(0), [margin_row("2330", margin=(1200, 1500))])
    up = evaluate(MarginBalanceChange(side="margin", direction="up", percent=10), r, day(0))
    assert up is not None
    assert up.met is True
    assert "融資餘額由 1,200 變為 1,500（+25.00%）" in up.message
    down = evaluate(MarginBalanceChange(side="margin", direction="down", percent=10), r, day(0))
    assert down is not None
    assert down.met is False


def test_margin_decrease_and_the_short_side_read_their_own_columns() -> None:
    r = FakeReader()
    r.put(MARGIN, day(0), [margin_row("2330", margin=(1500, 1200), short=(100, 160))])
    shrink = evaluate(MarginBalanceChange(side="margin", direction="down", percent=10), r, day(0))
    assert shrink is not None
    assert shrink.met is True
    short_up = evaluate(MarginBalanceChange(side="short", direction="up", percent=50), r, day(0))
    assert short_up is not None
    assert short_up.met is True
    assert "融券餘額" in short_up.message


def test_margin_without_a_base_or_a_row_is_unevaluable_not_met() -> None:
    r = FakeReader()
    r.put(MARGIN, day(0), [margin_row("2330", margin=(0, 500))])
    condition = MarginBalanceChange(side="margin", direction="up", percent=10)
    assert evaluate(condition, r, day(0)) is None  # cannot take a percentage of a zero balance
    assert evaluate(condition, r, day(0), symbol="2317") is None  # no margin row at all


def test_etf_premium_and_discount_use_the_published_figure_and_say_estimated() -> None:
    r = FakeReader()
    r.put(ETF_NAV_DATASET, day(0), [nav_row("0050", 1.5), nav_row("0056", -1.2)])
    premium = evaluate(EtfPremiumDiscount(direction="premium", percent=1), r, day(0), symbol="0050")
    assert premium is not None
    assert premium.met is True
    assert "預估折溢價幅度 +1.50%" in premium.message
    assert "預估淨值" in premium.message
    discount = evaluate(
        EtfPremiumDiscount(direction="discount", percent=1), r, day(0), symbol="0056"
    )
    assert discount is not None
    assert discount.met is True
    not_met = evaluate(
        EtfPremiumDiscount(direction="discount", percent=1), r, day(0), symbol="0050"
    )
    assert not_met is not None
    assert not_met.met is False


def test_etf_without_an_estimate_or_a_nav_row_is_unevaluable() -> None:
    r = FakeReader()
    r.put(ETF_NAV_DATASET, day(0), [nav_row("0050", None)])
    condition = EtfPremiumDiscount(direction="premium", percent=1)
    assert evaluate(condition, r, day(0), symbol="0050") is None  # issuer published no estimate
    assert evaluate(condition, r, day(0), symbol="2330") is None  # a stock has no NAV row


def test_margin_and_etf_phrasing_passes_the_output_guardrail() -> None:
    r = FakeReader()
    r.put(MARGIN, day(0), [margin_row("2330", margin=(1200, 1500))])
    r.put(ETF_NAV_DATASET, day(0), [nav_row("0050", 1.5)])
    cases = [
        (MarginBalanceChange(side="margin", direction="up", percent=10), "2330"),
        (EtfPremiumDiscount(direction="premium", percent=1), "0050"),
    ]
    for condition, symbol in cases:
        result = evaluate(condition, r, day(0), symbol=symbol)
        assert result is not None
        verdict = check_output(_safe_body(result.message, condition, symbol), has_market_data=False)
        assert verdict.reason != "trading_language_redacted", result.message


ALL_TEMPLATES = [
    ClosePrice(comparator="above", value=1),
    DailyChange(direction="up", percent=1),
    VolumeSurge(multiple=2),
    InstitutionalStreak(investor="foreign", direction="buy", days=3),
    AttentionListEntry(),
    DispositionListEntry(),
    MonthRevenuePublished(),
    MaterialAnnouncementPublished(),
    MarginBalanceChange(side="margin", direction="up", percent=1),
    EtfPremiumDiscount(direction="premium", percent=1),
]


@pytest.mark.parametrize("condition", ALL_TEMPLATES, ids=lambda c: c.kind)
def test_the_dataset_a_template_reads_is_the_dataset_its_sweep_probes(condition) -> None:
    # If these ever diverge, the sweep watches one partition while the evaluator reads another and
    # the alert silently never fires, so it is pinned for every template in every market it serves.
    for market in (Market.TW, Market.US):
        assert probe_dataset(capability_of(condition), market) == dataset_of(condition, market)


def test_every_template_kind_is_covered_by_this_invariant() -> None:
    covered = {c.kind for c in ALL_TEMPLATES}
    # A template added to the vocabulary but not listed above would dodge the invariant.
    assert covered >= TW_ONLY_KINDS
    assert {"close_price", "daily_change", "volume_surge"} <= covered


async def test_margin_and_etf_rules_fire_and_the_sweep_finds_both_datasets(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    margin = await create_rule(
        session_factory, user, MarginBalanceChange(side="margin", direction="up", percent=10)
    )
    etf = await create_rule(
        session_factory,
        user,
        EtfPremiumDiscount(direction="premium", percent=1),
        symbol="0050",
    )
    reader = FakeReader()
    reader.put(MARGIN, TODAY, [margin_row("2330", margin=(1200, 1500))])
    reader.put(ETF_NAV_DATASET, TODAY, [nav_row("0050", 1.5)])
    fired = await make_worker(session_factory, reader).reconcile_alerts()
    assert sorted(fired) == sorted([margin.id, etf.id])


def announcement_row(
    symbol: str,
    announced: date,
    *,
    subject: str = "公告本公司董事會決議股利分派",
    time: str = "17:30:00",
):
    return {
        "symbol": symbol,
        "company_name": "台積電",
        "announced_date": announced.isoformat(),
        "announced_time": time,
        "subject": subject,
        "detail": "",
        "category": "其他",
    }


ANNOUNCEMENTS = MATERIAL_ANNOUNCEMENTS_DATASET


def test_announcement_counts_only_filings_dated_the_evaluation_day() -> None:
    r = FakeReader()
    r.put(ANNOUNCEMENTS, day(0), [announcement_row("2330", day(0))])
    hit = evaluate(MaterialAnnouncementPublished(), r, day(0))
    assert hit is not None
    assert hit.met is True
    assert "重大訊息" in hit.message
    assert "公告本公司董事會決議股利分派" in hit.message


def test_announcement_ignores_old_filings_and_other_symbols() -> None:
    r = FakeReader()
    r.put(
        ANNOUNCEMENTS, day(0), [announcement_row("2330", day(-2)), announcement_row("2317", day(0))]
    )
    result = evaluate(MaterialAnnouncementPublished(), r, day(0), symbol="2330")
    assert result is not None
    assert result.met is False
    assert evaluate(MaterialAnnouncementPublished(), r, day(1)) is None  # nothing ingested that day


def test_announcement_reports_the_count_and_the_latest_subject() -> None:
    r = FakeReader()
    r.put(
        ANNOUNCEMENTS,
        day(0),
        [
            announcement_row("2330", day(0), subject="較早的公告", time="09:00:00"),
            announcement_row("2330", day(0), subject="較晚的公告", time="17:00:00"),
        ],
    )
    hit = evaluate(MaterialAnnouncementPublished(), r, day(0))
    assert hit is not None
    assert "2 則" in hit.message
    assert "較晚的公告" in hit.message
    assert "較早的公告" not in hit.message


def test_announcement_subject_is_clipped_and_stripped_of_markup() -> None:
    r = FakeReader()
    nasty = "<script>alert(1)</script> [點此](http://evil.example) " + "長" * 80
    r.put(ANNOUNCEMENTS, day(0), [announcement_row("2330", day(0), subject=nasty)])
    hit = evaluate(MaterialAnnouncementPublished(), r, day(0))
    assert hit is not None
    for ch in "<>[]()`":
        assert ch not in hit.message.split("最新主旨：")[1].split("（你設定")[0]
    assert "…" in hit.message
    assert len(hit.message) < 200


def test_announcement_template_reads_its_own_dataset_and_capability() -> None:
    assert dataset_of(MaterialAnnouncementPublished()) == ANNOUNCEMENTS
    assert capability_of(MaterialAnnouncementPublished()) is Capability.CORPORATE_ACTIONS
    assert probe_dataset(Capability.CORPORATE_ACTIONS, Market.TW) == ANNOUNCEMENTS


def test_announcement_phrasing_passes_the_output_guardrail() -> None:
    r = FakeReader()
    r.put(ANNOUNCEMENTS, day(0), [announcement_row("2330", day(0))])
    result = evaluate(MaterialAnnouncementPublished(), r, day(0))
    assert result is not None
    body = _safe_body(result.message, MaterialAnnouncementPublished(), "2330")
    assert check_output(body, has_market_data=False).reason != "trading_language_redacted"


def test_a_subject_that_trips_the_scan_is_dropped_but_the_fact_is_kept() -> None:
    # A filing's own wording is third-party text. If the scan rewrites it, the notification drops
    # the quote yet still says a filing exists.
    r = FakeReader()
    r.put(ANNOUNCEMENTS, day(0), [announcement_row("2330", day(0), subject="建議買進 目標價 1200")])
    result = evaluate(MaterialAnnouncementPublished(), r, day(0))
    assert result is not None
    assert result.plain_message is not None
    body = _safe_body(result.message, MaterialAnnouncementPublished(), "2330", result.plain_message)
    assert "目標價" not in body
    assert "重大訊息" in body
    assert "非投資建議" in body


def test_a_subject_cannot_break_out_of_its_quotation() -> None:
    r = FakeReader()
    sneaky = "股利公告」。本公司建議投資人逢低承接「"
    r.put(ANNOUNCEMENTS, day(0), [announcement_row("2330", day(0), subject=sneaky)])
    hit = evaluate(MaterialAnnouncementPublished(), r, day(0))
    assert hit is not None
    quoted = hit.message.split("最新主旨：")[1].split("（你設定")[0]
    assert quoted.count("「") == 1
    assert quoted.count("」") == 1
    assert "。" not in quoted


async def test_announcement_rule_fires_on_every_day_a_filing_lands(session_factory) -> None:
    # An event, not a state: a filing today must not be silenced by one yesterday.
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(session_factory, user, MaterialAnnouncementPublished())
    reader = FakeReader()
    worker = make_worker(session_factory, reader)
    reader.put(ANNOUNCEMENTS, day(0), [announcement_row("2330", day(0))])
    reader.put(ANNOUNCEMENTS, day(1), [announcement_row("2330", day(1))])
    reader.put(ANNOUNCEMENTS, day(2), [])
    cap = Capability.CORPORATE_ACTIONS
    assert await signal(worker, cap, day(0)) == [rule.id]
    assert await signal(worker, cap, day(1)) == [rule.id]
    assert await signal(worker, cap, day(2)) == []
    assert len(await notifications(session_factory, user)) == 2


def _fullwidth(text: str) -> str:
    return "".join(chr(ord(ch) + 0xFEE0) if "!" <= ch <= "~" else ch for ch in text)


def test_a_subject_cannot_carry_a_tappable_link() -> None:
    r = FakeReader()
    for sneaky in (
        "詳見 https://evil.example/pay 公告",
        "請至 www.evil.example 查詢",
        "連絡 ceo@evil.example",
        "伺服器 192.168.1.1 公告",
        "伺服器192.168.1.1公告",
        f"詳見 {_fullwidth('www.evil.example')} 公告",
        f"連絡 {_fullwidth('ceo@evil.example')}",
        "請至 evil\u3002example 查詢",
        "請至 123.example 查詢",
    ):
        r.put(ANNOUNCEMENTS, day(0), [announcement_row("2330", day(0), subject=sneaky)])
        hit = evaluate(MaterialAnnouncementPublished(), r, day(0))
        assert hit is not None
        quoted = hit.message.split("最新主旨：")[1].split("（你設定")[0]
        assert "://" not in quoted
        assert not re.search(r"[A-Za-z0-9]\.[A-Za-z0-9]", quoted), quoted
        assert "@" not in quoted
        assert "192.168" not in quoted


def test_ordinary_numbers_in_a_subject_survive() -> None:
    r = FakeReader()
    r.put(ANNOUNCEMENTS, day(0), [announcement_row("2330", day(0), subject="每股配發現金 3.5 元")])
    hit = evaluate(MaterialAnnouncementPublished(), r, day(0))
    assert hit is not None
    assert "3.5" in hit.message


def test_a_keyword_keeps_only_filings_whose_subject_contains_it() -> None:
    r = FakeReader()
    r.put(
        ANNOUNCEMENTS,
        day(0),
        [
            announcement_row("2330", day(0), subject="公告本公司董事會決議股利分派"),
            announcement_row("2330", day(0), subject="公告背書保證"),
        ],
    )
    hit = evaluate(MaterialAnnouncementPublished(keyword="股利"), r, day(0))
    assert hit is not None
    assert hit.met is True
    assert "1 則" in hit.message
    assert "背書" not in hit.message
    miss = evaluate(MaterialAnnouncementPublished(keyword="法說會"), r, day(0))
    assert miss is not None
    assert miss.met is False


def test_a_keyword_match_ignores_case() -> None:
    r = FakeReader()
    r.put(
        ANNOUNCEMENTS,
        day(0),
        [announcement_row("2330", day(0), subject="Board approves ESG report")],
    )
    hit = evaluate(MaterialAnnouncementPublished(keyword="esg"), r, day(0))
    assert hit is not None
    assert hit.met is True


def test_the_keyword_appears_in_the_rule_description() -> None:
    from scheduler.templates import describe  # noqa: PLC0415

    assert describe(MaterialAnnouncementPublished()) == "公布重大訊息"
    assert describe(MaterialAnnouncementPublished(keyword="股利")) == "公布含「股利」的重大訊息"


@pytest.mark.parametrize("bad", ["<script>", "[x](http://e.example)", "目標價 1200", "字" * 21])
def test_an_unsafe_or_overlong_keyword_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        MaterialAnnouncementPublished(keyword=bad)


def test_a_blank_keyword_means_no_filter() -> None:
    assert MaterialAnnouncementPublished(keyword="   ").keyword is None
