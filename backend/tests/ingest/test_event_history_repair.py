"""`--event-history --repair-half-snapshots` - stored daily snapshots that hold one exchange only.

A forward day written while the listed market's live attention feed was empty (every day from the
first forward run) or while the OTC request failed (2026-09-07, -08, -11) holds rows of a single
exchange. The repair rebuilds exactly those days from the history reports and never touches a
complete snapshot.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import ingest.backfill_cli as backfill_cli_module
import ingest.event_history as history_module
from ingest.backfill_cli import parse_args, run_event_history_backfill
from ingest.event_history import find_half_snapshots, repair_half_snapshots
from ingest.jobs import ATTENTION_STOCKS_DATASET, DISPOSITION_EVENTS_DATASET
from ingest.journal import RunStatus
from ingest.leader import NeverLeader
from tests.ingest.test_event_history import (
    MARKET,
    StubSource,
    _ctx,
    _disposition,
    _flag,
    _read,
    _settings,
)

TODAY = date(2026, 10, 27)
MON, TUE, WED = date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)
FRI = date(2026, 9, 4)


def _history() -> list[dict[str, object]]:
    return [
        _flag("2330", "2026-09-04", "twse"),
        _flag("6173", "2026-09-04", "tpex"),
        _flag("2317", "2026-09-07", "twse"),
        _flag("3441", "2026-09-07", "tpex"),
        _flag("2454", "2026-09-08", "twse"),
        _flag("3374", "2026-09-08", "tpex"),
        _flag("2303", "2026-09-09", "twse"),
        _flag("6187", "2026-09-09", "tpex"),
    ]


def _seed_forward_days(ctx: object) -> None:
    """Monday: the OTC request failed (listed only). Tuesday: the listed dump was empty (OTC
    only). Wednesday: a complete snapshot, with a row the reports do not carry."""
    write = ctx.store.write_partition  # type: ignore[attr-defined]
    rows = {f["symbol"]: f for f in _history()}
    write(ATTENTION_STOCKS_DATASET, MARKET, MON, [rows["2317"]])
    write(ATTENTION_STOCKS_DATASET, MARKET, TUE, [rows["3374"]])
    kept = _flag("9999", "2026-09-09", "twse")
    write(ATTENTION_STOCKS_DATASET, MARKET, WED, [kept, rows["6187"]])


def test_a_stored_snapshot_with_one_exchange_is_listed_with_what_it_lacks(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_forward_days(ctx)

    half = find_half_snapshots(ctx, start=FRI, end=WED)

    assert [(h.dataset, h.day, set(h.missing)) for h in half] == [
        (ATTENTION_STOCKS_DATASET, MON, {"tpex"}),
        (ATTENTION_STOCKS_DATASET, TUE, {"twse"}),
    ]


async def test_only_the_half_days_are_rebuilt_and_the_complete_one_is_never_touched(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    _seed_forward_days(ctx)
    complete_before = _read(ctx, ATTENTION_STOCKS_DATASET, WED)
    source = StubSource(flags=_history())

    runs = await repair_half_snapshots(
        ctx, start=FRI, end=WED, adapter=source.as_adapter(), today=TODAY
    )

    assert [(r.run_date, r.status) for r in runs] == [
        (MON, RunStatus.SUCCESS),
        (TUE, RunStatus.SUCCESS),
    ]
    for day in (MON, TUE):
        rows = _read(ctx, ATTENTION_STOCKS_DATASET, day)
        assert {r["security_type"] for r in rows} == {"twse", "tpex"}
    assert _read(ctx, ATTENTION_STOCKS_DATASET, WED) == complete_before
    assert [call[0] for call in source.calls] == ["attention"]


async def test_a_second_repair_changes_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_forward_days(ctx)
    source = StubSource(flags=_history())
    await repair_half_snapshots(ctx, start=FRI, end=WED, adapter=source.as_adapter(), today=TODAY)
    after_first = {d: _read(ctx, ATTENTION_STOCKS_DATASET, d) for d in (MON, TUE, WED)}
    source.calls.clear()

    runs = await repair_half_snapshots(
        ctx, start=FRI, end=WED, adapter=source.as_adapter(), today=TODAY
    )

    assert runs == ()
    assert source.calls == []
    assert {d: _read(ctx, ATTENTION_STOCKS_DATASET, d) for d in (MON, TUE, WED)} == after_first


async def test_a_half_day_the_reports_cannot_complete_is_kept_as_it_is(tmp_path: Path) -> None:
    """The OTC market really listed nothing that day: the stored snapshot is already right."""
    ctx = _ctx(tmp_path)
    _seed_forward_days(ctx)
    only_listed = [
        f
        for f in _history()
        if not (f["security_type"] == "tpex" and f["flagged_date"] <= "2026-09-07")
    ]
    before = _read(ctx, ATTENTION_STOCKS_DATASET, MON)

    runs = await repair_half_snapshots(
        ctx, start=MON, end=MON, adapter=StubSource(flags=only_listed).as_adapter(), today=TODAY
    )

    assert [r.status for r in runs] == [RunStatus.SKIPPED]
    assert _read(ctx, ATTENTION_STOCKS_DATASET, MON) == before


async def test_a_month_that_cannot_be_read_keeps_the_stored_snapshots(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_forward_days(ctx)
    source = StubSource(flags=_history())
    source.failing_months = {("attention", 9)}
    before = _read(ctx, ATTENTION_STOCKS_DATASET, MON)

    runs = await repair_half_snapshots(
        ctx, start=FRI, end=WED, adapter=source.as_adapter(), today=TODAY
    )

    assert [r.status for r in runs] == [RunStatus.FAILED]
    assert _read(ctx, ATTENTION_STOCKS_DATASET, MON) == before


async def test_a_day_the_reports_have_not_reached_yet_is_left_for_a_later_run(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    _seed_forward_days(ctx)
    behind = [f for f in _history() if f["flagged_date"] <= "2026-09-07"]
    before = _read(ctx, ATTENTION_STOCKS_DATASET, TUE)

    runs = await repair_half_snapshots(
        ctx,
        start=MON,
        end=TUE,
        adapter=StubSource(flags=behind).as_adapter(),
        today=date(2026, 9, 9),
    )

    assert [(r.run_date, r.status) for r in runs] == [
        (TUE, RunStatus.SKIPPED),
        (MON, RunStatus.SUCCESS),
    ]
    assert _read(ctx, ATTENTION_STOCKS_DATASET, TUE) == before


async def test_disposition_snapshots_are_repaired_the_same_way(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    twse = _disposition("2330", "2026-09-04", "2026-09-07", "2026-09-18", "twse")
    tpex = _disposition("6187", "2026-09-04", "2026-09-07", "2026-09-18", "tpex")
    ctx.store.write_partition(DISPOSITION_EVENTS_DATASET, MARKET, MON, [twse])

    runs = await repair_half_snapshots(
        ctx,
        start=MON,
        end=MON,
        adapter=StubSource(dispositions=[twse, tpex]).as_adapter(),
        today=TODAY,
    )

    assert [(r.dataset, r.status) for r in runs] == [
        (DISPOSITION_EVENTS_DATASET, RunStatus.SUCCESS)
    ]
    rows = _read(ctx, DISPOSITION_EVENTS_DATASET, MON)
    assert {r["security_type"] for r in rows} == {"twse", "tpex"}


async def test_a_process_without_the_lease_repairs_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, leader=NeverLeader())
    _seed_forward_days(ctx)
    source = StubSource(flags=_history())

    runs = await repair_half_snapshots(
        ctx, start=FRI, end=WED, adapter=source.as_adapter(), today=TODAY
    )

    assert [r.status for r in runs] == [RunStatus.SKIPPED]
    assert source.calls == []


def test_the_repair_is_a_mode_of_the_event_history_run() -> None:
    assert parse_args(["--event-history", "--repair-half-snapshots"]).repair_half_snapshots
    assert not parse_args(["--event-history"]).repair_half_snapshots
    with pytest.raises(SystemExit):
        parse_args(["--repair-half-snapshots"])


async def test_the_cli_lists_the_half_days_then_repairs_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _seed_forward_days(ctx)
    source = StubSource(flags=_history())
    monkeypatch.setattr(history_module, "ExchangeEventHistoryAdapter", lambda: source)
    monkeypatch.setattr(backfill_cli_module, "last_available_trading_day", lambda *_a, **_k: WED)
    runs = await run_event_history_backfill(_settings(), ctx, override=True, years=1, repair=True)

    out = capsys.readouterr().out
    assert f"half snapshot {ATTENTION_STOCKS_DATASET} 2026-09-07: no tpex" in out
    assert f"half snapshot {ATTENTION_STOCKS_DATASET} 2026-09-08: no twse" in out
    assert "2 found, 2 repaired, 0 failed" in out
    assert len(runs) == 2
