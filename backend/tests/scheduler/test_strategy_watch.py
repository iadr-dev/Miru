"""策略盯盤 delivery: the morning list and the condition-met alerts, against a fake reader and an
in-memory database. Covers idempotency, scope, masking for a plan without intraday data, the daily
limit, the after-close cutoff and the output scan."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from analytics.strategy_watch.batch import STRATEGY_WATCHLIST_DATASET
from db.models import WatchlistItemRow, WatchlistRow
from governance.textscan import check_output
from ingest.jobs.strategy_intraday import STRATEGY_HITS_DATASET
from scheduler.store import SqlAlchemySchedulerStore
from scheduler.strategy_messages import hit_alert, morning_list, safe_body
from scheduler.strategy_store import STRATEGY_SOURCE_PREFIX, StrategySubscriptionStore
from schemas.alerts import NON_ADVICE_FOOTER
from schemas.enums import Tier
from schemas.strategy_watch import STRATEGY_DEFINITION, STRATEGY_KEYS
from tests.scheduler.conftest import insert_user
from tests.scheduler.test_alert_engine import FakeReader, make_worker

_TAIPEI = ZoneInfo("Asia/Taipei")
_DAY = datetime(2026, 10, 16, tzinfo=_TAIPEI).date()


def _at(hh: int, mm: int) -> datetime:
    return datetime(2026, 10, 16, hh, mm, tzinfo=_TAIPEI).astimezone(UTC)


def _watch(symbol: str, strategy: str = "volatility_squeeze") -> dict[str, object]:
    return {
        "symbol": symbol,
        "name": {"2330": "台積電", "2317": "鴻海"}.get(symbol),
        "strategy": strategy,
        "upper_band": 868.0,
        "low_20d": 900.0,
        "lower_band": 820.0,
        "industry": "半導體業",
        "sector_strength_pct": 93.3,
        "foreign_streak": 3,
        "trust_streak": 0,
        "regime": "neutral",
        "breadth_pct": 46.1,
    }


def _hit(symbol: str, slot: str, *, alertable: bool = True) -> dict[str, object]:
    conditions = [
        {
            "key": "price_above_upper_band",
            "kind": "trigger",
            "met": True,
            "observed": 872.0,
            "required": 868.0,
        },
        {
            "key": "volume_ratio_at_least",
            "kind": "trigger",
            "met": True,
            "observed": 2.45,
            "required": 1.5,
        },
        {"key": "after_opening_window", "kind": "guard", "met": True},
    ]
    return {
        "symbol": symbol,
        "strategy": "volatility_squeeze",
        "slot": slot,
        "alertable": alertable,
        "all_met": True,
        "conditions": json.dumps(conditions, ensure_ascii=False),
    }


def _reader(hits: list[dict[str, object]]) -> FakeReader:
    reader = FakeReader()
    reader.put(STRATEGY_WATCHLIST_DATASET, _DAY, [_watch("2330"), _watch("2317")])
    reader.put(STRATEGY_HITS_DATASET, _DAY, hits)
    return reader


async def _subscribe(session_factory, user: str, **fields) -> None:
    values = {
        "strategies": ("volatility_squeeze",),
        "watchlist_id": None,
        "morning_list": True,
        "channels": (),
        "quiet_hours": None,
        "daily_limit": None,
        "enabled": True,
        **fields,
    }
    async with session_factory() as session:
        await StrategySubscriptionStore(session).put(user_id=user, **values)
        await session.commit()


async def _sent(session_factory, user: str):
    async with session_factory() as session:
        found = await SqlAlchemySchedulerStore(session).list_notifications(user_id=user)
    return [n for n in found if n.source_id.startswith(STRATEGY_SOURCE_PREFIX)]


async def _backdate(
    session_factory, user: str, when: datetime = datetime(2026, 10, 1, tzinfo=UTC)
) -> None:
    """Set when the subscription was made (default: before today, as an existing subscriber's)."""
    from db.models import StrategySubscriptionRow  # noqa: PLC0415

    async with session_factory() as session:
        row = (
            await session.execute(
                StrategySubscriptionRow.__table__.select().where(
                    StrategySubscriptionRow.user_id == user
                )
            )
        ).first()
        assert row is not None
        await session.execute(
            StrategySubscriptionRow.__table__.update()
            .where(StrategySubscriptionRow.user_id == user)
            .values(created_at=when)
        )
        await session.commit()


async def test_morning_list_and_hit_are_each_delivered_once(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _subscribe(session_factory, user)
    await _backdate(session_factory, user)
    worker = make_worker(session_factory, _reader([_hit("2330", "09:50")]))

    await worker.run_strategy_watch(now=_at(10, 1))
    await worker.run_strategy_watch(now=_at(10, 2))

    sent = await _sent(session_factory, user)
    titles = sorted(str(n.payload["title"]) for n in sent)
    assert titles == ["2330 台積電 · 波動率擠壓 條件全數達成", "今日盯盤清單（10/16）"]
    alert = next(n for n in sent if "條件全數達成" in str(n.payload["title"]))
    body = str(alert.payload["body"])
    assert "價格 872 高於布林上軌 868" in body
    assert "外資連 3 日買超" in body
    assert NON_ADVICE_FOOTER in body
    assert check_output(body, has_market_data=False).reason is None


async def test_a_plan_without_intraday_data_gets_the_alert_with_values_masked(
    session_factory,
) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    await _subscribe(session_factory, user, morning_list=False)
    await _backdate(session_factory, user)
    worker = make_worker(session_factory, _reader([_hit("2330", "09:50")]))

    await worker.run_strategy_watch(now=_at(10, 1))

    (alert,) = await _sent(session_factory, user)
    body = str(alert.payload["body"])
    assert "價格高於布林上軌" in body
    assert "872" not in body
    assert "Premium" in body


async def test_a_group_subscription_only_hears_about_its_members(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            WatchlistRow(
                id="wl-1",
                user_id=user,
                name="核心",
                market="TW",
                asset_class="stock",
                sort_order="a",
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        session.add(
            WatchlistItemRow(
                id="wi-1",
                watchlist_id="wl-1",
                user_id=user,
                symbol="2317",
                market="TW",
                asset_class="stock",
                sort_order="a",
                added_at=now,
            )
        )
        await session.commit()
    await _subscribe(session_factory, user, watchlist_id="wl-1", morning_list=False)
    await _backdate(session_factory, user)
    worker = make_worker(session_factory, _reader([_hit("2330", "09:50"), _hit("2317", "10:00")]))

    await worker.run_strategy_watch(now=_at(10, 5))

    assert [str(n.payload["title"])[:4] for n in await _sent(session_factory, user)] == ["2317"]


async def test_daily_limit_caps_condition_alerts(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _subscribe(session_factory, user, morning_list=False, daily_limit=1)
    await _backdate(session_factory, user)
    worker = make_worker(session_factory, _reader([_hit("2330", "09:50"), _hit("2317", "10:00")]))

    await worker.run_strategy_watch(now=_at(10, 5))
    await worker.run_strategy_watch(now=_at(10, 6))

    assert len(await _sent(session_factory, user)) == 1


async def test_near_misses_and_slots_before_the_subscription_are_not_sent(
    session_factory,
) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _subscribe(session_factory, user, morning_list=False)
    await _backdate(session_factory, user, _at(10, 0))  # subscribed at 10:00 today
    hits = [
        _hit("2330", "09:50"),  # before the subscription existed
        _hit("2317", "10:10", alertable=False),  # a near miss
        _hit("2454", "10:20"),  # the only one to send
    ]
    worker = make_worker(session_factory, _reader(hits))

    await worker.run_strategy_watch(now=_at(10, 25))

    assert [str(n.payload["title"])[:4] for n in await _sent(session_factory, user)] == ["2454"]


async def test_no_morning_list_after_the_close(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _subscribe(session_factory, user)
    await _backdate(session_factory, user)
    worker = make_worker(session_factory, _reader([]))

    await worker.run_strategy_watch(now=_at(14, 0))

    assert await _sent(session_factory, user) == []


async def test_a_disabled_subscription_sends_nothing(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _subscribe(session_factory, user, enabled=False)
    await _backdate(session_factory, user)
    worker = make_worker(session_factory, _reader([_hit("2330", "09:50")]))

    assert await worker.run_strategy_watch(now=_at(10, 1)) == 0
    assert await _sent(session_factory, user) == []


def test_morning_list_groups_by_strategy_with_the_key_level() -> None:
    listing = morning_list([_watch("2330"), _watch("2317", "ad_divergence")], _DAY)
    assert listing is not None
    _, body = listing
    assert "波動率擠壓 1 檔：2330 台積電（上軌 868）" in body
    assert "量價背離 1 檔：2317 鴻海（20 日低點 900）" in body
    assert check_output(f"{body}\n{NON_ADVICE_FOOTER}", has_market_data=False).reason is None


def test_a_flagged_headline_is_dropped_not_sent() -> None:
    watch = {**_watch("2330"), "headline": "董事會決議，建議買進並設定目標價 1200 元"}
    title, body, plain = hit_alert(_hit("2330", "09:50"), watch, masked=False)
    text = safe_body(body, plain, "fallback")
    assert "目標價" not in text
    assert "價格 872 高於布林上軌 868" in text
    assert title.startswith("2330")


def _accumulation_watch() -> dict[str, object]:
    return {
        "symbol": "2409",
        "name": "友達",
        "strategy": "chip_accumulation",
        "range_high_20d": 33.5,
        "range_low_20d": 30.2,
        "large_holder_pct": 51.7,
        "large_holder_change_4w": 6.0,
        "margin_change_20d_pct": -6.0,
        "days_on_list": 4,
        "regime": "neutral",
        "breadth_pct": 46.1,
    }


async def test_a_close_confirmation_is_its_own_alert_after_the_intraday_one(
    session_factory,
) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _subscribe(session_factory, user, strategies=("chip_accumulation",), morning_list=False)
    await _backdate(session_factory, user)
    intraday = {
        **_hit("2409", "10:20"),
        "strategy": "chip_accumulation",
        "conditions": json.dumps(
            [
                {
                    "key": "price_above_range_high",
                    "kind": "trigger",
                    "observed": 33.9,
                    "required": 33.5,
                },
            ]
        ),
    }
    close = {
        **intraday,
        "slot": "close",
        "conditions": json.dumps(
            [
                {
                    "key": "close_above_range_high",
                    "kind": "trigger",
                    "observed": 34.2,
                    "required": 33.5,
                },
                {
                    "key": "close_volume_multiple",
                    "kind": "trigger",
                    "observed": 1.8,
                    "required": 1.5,
                },
            ]
        ),
    }
    reader = FakeReader()
    reader.put(STRATEGY_WATCHLIST_DATASET, _DAY, [_accumulation_watch()])
    reader.put(STRATEGY_HITS_DATASET, _DAY, [intraday])
    worker = make_worker(session_factory, reader)
    await worker.run_strategy_watch(now=_at(10, 25))
    reader.put(STRATEGY_HITS_DATASET, _DAY, [intraday, close])
    await worker.run_strategy_watch(now=_at(18, 5))

    titles = sorted(str(n.payload["title"]) for n in await _sent(session_factory, user))
    assert titles == ["2409 友達 · 籌碼集中盤整 條件全數達成", "收盤確認：2409 友達 · 籌碼集中盤整"]
    close_body = next(
        str(n.payload["body"])
        for n in await _sent(session_factory, user)
        if str(n.payload["title"]).startswith("收盤確認")
    )
    assert "收盤 34.2 高於盤整區間高點 33.5" in close_body
    assert "千張大戶 51.7%（4 週 +6.00 個百分點）" in close_body
    assert "盤整區間下緣 30.2" in close_body
    assert "停損" not in close_body
    assert check_output(close_body, has_market_data=False).reason is None


def test_morning_list_shows_the_range_and_holder_share_for_accumulation() -> None:
    listing = morning_list([_accumulation_watch()], _DAY)
    assert listing is not None
    assert "籌碼集中盤整 1 檔：2409 友達（區間 30.2 至 33.5、大戶 51.7%）" in listing[1]


def test_flag_listing_states_the_pause_and_a_margin_surge() -> None:
    watch = {
        "symbol": "3017",
        "name": "奇鋐",
        "strategy": "momentum_flag",
        "flag_low": 59.2,
        "flag_high": 60.3,
        "flag_sessions": 5.0,
        "run_gain_pct": 25.0,
        "large_holder_pct": 60.1,
        "large_holder_change_4w": 1.2,
        "margin_change_20d_pct": 33.0,
        "regime": "neutral",
        "breadth_pct": 50.0,
    }
    listing = morning_list([watch], _DAY)
    assert listing is not None
    assert "強勢整理 1 檔：3017 奇鋐（整理 59.2 至 60.3（5 日，前段 +25%））" in listing[1]
    hit = {
        **_hit("3017", "10:20"),
        "strategy": "momentum_flag",
        "conditions": json.dumps(
            [
                {
                    "key": "price_above_flag_high",
                    "kind": "trigger",
                    "observed": 60.8,
                    "required": 60.3,
                }
            ]
        ),
    }
    _, body, plain = hit_alert(hit, watch, masked=False)
    text = safe_body(body, plain, "fallback")
    assert "價格 60.8 高於整理區高點 60.3" in text
    assert "融資 20 日 +33.0%，增幅偏大（散戶融資追價）" in text
    assert "整理區下緣 59.2" in text
    assert check_output(text, has_market_data=False).reason is None


def test_morning_list_defines_each_listed_strategy_once() -> None:
    listing = morning_list(
        [_watch("2330"), _watch("2454"), _watch("2317", "ad_divergence")],
        _DAY,
    )
    assert listing is not None
    body = listing[1]
    assert body.count(f"波動率擠壓：{STRATEGY_DEFINITION['volatility_squeeze']}") == 1
    assert f"量價背離：{STRATEGY_DEFINITION['ad_divergence']}" in body
    assert "超跌反彈：" not in body


def test_morning_list_ranks_industries_on_the_list_only() -> None:
    def row(symbol: str, industry: str | None, strength: float | None) -> dict[str, object]:
        return {**_watch(symbol), "industry": industry, "sector_strength_pct": strength}

    rows = [
        row("2330", "半導體業", 92.0),
        row("2454", "半導體業", 92.0),
        row("2603", "航運業", 40.0),
        row("2412", "電信業", 55.5),
        row("1101", "水泥工業", 10.0),
        row("9999", None, None),
        row("8888", "無強度業", None),
    ]
    listing = morning_list(rows, _DAY)
    assert listing is not None
    sector = next(line for line in listing[1].splitlines() if line.startswith("清單類股"))
    assert "半導體業 92（2 檔）、電信業 55.5（1 檔）、航運業 40（1 檔）" in sector
    assert "水泥工業" not in sector
    assert "無強度業" not in sector
    assert "僅就清單內" in sector


def test_morning_list_skips_the_sector_line_without_industries() -> None:
    rows = [{**_watch("2330"), "industry": None, "sector_strength_pct": None}]
    listing = morning_list(rows, _DAY)
    assert listing is not None
    assert "清單類股" not in listing[1]


def test_hit_alert_carries_the_definition_of_its_own_strategy_only() -> None:
    _, body, plain = hit_alert(_hit("2330", "09:50"), _watch("2330"), masked=False)
    definition = f"波動率擠壓：{STRATEGY_DEFINITION['volatility_squeeze']}"
    assert definition in body
    assert definition in plain
    assert "超跌反彈：" not in body


def test_every_strategy_body_passes_the_output_scan() -> None:
    watches = [_watch(f"{2300 + i}", key) for i, key in enumerate(STRATEGY_KEYS)]
    listing = morning_list(watches, _DAY)
    assert listing is not None
    bodies = [listing[1]]
    for watch in watches:
        hit = {**_hit(str(watch["symbol"]), "09:50"), "strategy": watch["strategy"]}
        for masked in (False, True):
            _, body, plain = hit_alert(hit, watch, masked=masked)
            bodies.extend([body, plain])
    for body in bodies:
        text = safe_body(body, body, "fallback")
        assert text.endswith(NON_ADVICE_FOOTER)
        assert "fallback" not in text
        assert check_output(text, has_market_data=False).reason is None
    assert set(STRATEGY_DEFINITION) == set(STRATEGY_KEYS)
