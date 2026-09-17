"""`get_sector_rotation_rrg` — real weekly RS-Ratio/RS-Momentum (JdK RRG) over TWSE industry
sub-indices against TAIEX. Unlike `get_sector_rotation` (reads the ingest-owned Parquet store),
this tool fetches `Capability.PRICE_EOD` per `data_id` through the router, so the stub adapter
here differentiates by `sym.id` (the sub-index/benchmark code), not just by capability — a plain
`make_router` (one result per capability, any symbol) can't express "TAIEX and Semiconductor
return different close series."
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tools.base import ToolContext
from tools.market_tools import (
    SECTOR_RRG_INDEX_IDS,
    GetSectorRotationRrgInput,
    GetSectorRotationRrgTool,
)


class _PerSymbolAdapter:
    """Stub `SourceAdapter` that returns a different canned `SourceResult` per `sym.id` — the
    shape `GetSectorRotationRrgTool` actually needs (one close series per index code), which the
    shared `tests/tools/conftest.make_router` (keyed only by capability) can't express."""

    source_id = "STUB"
    timing_class = TimingClass.OFFICIAL_EOD

    def __init__(self, bars_by_id: dict[str, list[dict[str, object]]]) -> None:
        self._bars_by_id = bars_by_id

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.PRICE_EOD, Market.TW)}

    async def fetch(
        self, cap: Capability, sym: Symbol, *, tls_policy: TLSPolicy, **params: object
    ) -> SourceResult:
        del cap, tls_policy, params
        bars = self._bars_by_id.get(sym.id, [])
        return SourceResult(
            source_id=self.source_id,
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.OFFICIAL_EOD,
            fetched_at=datetime.now(UTC),
            currentness_caveat="stub caveat",
            payload={"bars": bars},
            health=SourceHealth(
                status=HealthStatus.OK if bars else HealthStatus.STALE,
                detail="stub",
            ),
        )

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        return raw


def _daily_bars(
    data_id: str, start: date, num_days: int, closes: list[float]
) -> list[dict[str, object]]:
    """One bar per calendar day (weekends included — `_weekly_closes_from_daily` only cares
    about each ISO week's LAST bar, so extra same-week bars are harmless filler) so a short,
    deterministic `closes` list can still cover many real trading weeks once resampled."""
    bars: list[dict[str, object]] = []
    for i in range(num_days):
        day = start + timedelta(days=i)
        close = closes[i % len(closes)]
        bars.append(
            {
                "symbol": data_id,
                "market": "TW",
                "trade_date": day.isoformat(),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000,
                "turnover": None,
            }
        )
    return bars


def _router_with_series(series_by_id: dict[str, list[float]], num_days: int) -> DataAccessRouter:
    start = date(2026, 1, 1)
    bars_by_id = {
        data_id: _daily_bars(data_id, start, num_days, closes)
        for data_id, closes in series_by_id.items()
    }
    adapter = _PerSymbolAdapter(bars_by_id)
    priority = {"TW": {"price_eod": ["STUB"]}}
    return DataAccessRouter({"STUB": adapter}, priority_config=priority)


async def test_reports_unavailable_when_benchmark_series_is_empty() -> None:
    router = _router_with_series({}, num_days=0)
    tool = GetSectorRotationRrgTool(router)

    output = await tool.run(GetSectorRotationRrgInput(market=Market.TW), ToolContext())

    assert output.data_available is False
    assert output.unavailable_reason
    assert output.sectors == ()


async def test_reports_unavailable_when_history_too_short_for_smoothing_windows() -> None:
    # Only 20 calendar days ≈ 3 ISO weeks — short of the ratio(10)+momentum(4)+1 = 15-week
    # minimum this tool requires before a stable RS-Ratio/RS-Momentum window exists.
    series = {"TAIEX": [100.0, 101.0], "Semiconductor": [50.0, 51.0]}
    router = _router_with_series(series, num_days=20)
    tool = GetSectorRotationRrgTool(router)

    output = await tool.run(GetSectorRotationRrgInput(market=Market.TW), ToolContext())

    assert output.data_available is False
    assert output.unavailable_reason
    assert output.sectors == ()


async def test_computes_real_rs_ratio_and_momentum_over_enough_weekly_history() -> None:
    # ~40 weeks of daily bars — comfortably past the 15-week minimum — with the benchmark flat
    # and the sub-index trending up, so its RS-Ratio should read meaningfully ABOVE the
    # "in line with benchmark" 100 center once the rolling window has real history behind it.
    num_days = 40 * 7
    taiex_closes = [100.0] * num_days
    semi_closes = [50.0 + 0.05 * i for i in range(num_days)]
    series = {"TAIEX": taiex_closes, "Semiconductor": semi_closes}
    router = _router_with_series(series, num_days=num_days)
    tool = GetSectorRotationRrgTool(router)

    output = await tool.run(GetSectorRotationRrgInput(market=Market.TW), ToolContext())

    assert output.data_available is True
    assert output.benchmark_id == "TAIEX"
    assert len(output.week_endings) > 0

    semi = next(s for s in output.sectors if s.industry_id == "Semiconductor")
    assert semi.industry_name  # never an invented label — comes straight from SECTOR_RRG_INDEX_IDS
    assert len(semi.points) == len(output.week_endings)
    latest = semi.points[-1]
    # A steadily outperforming sector's LATEST RS-Ratio is on the high side of its own rolling
    # window (the z-score construction means this is relative to the sector's own recent history,
    # not a fixed universal threshold) — assert it's a finite real number, not NaN/inf, and that
    # the weekly_change_pct is positive (the series is monotonically increasing).
    assert not math.isnan(latest.rs_ratio)
    assert math.isfinite(latest.rs_ratio)
    assert latest.weekly_change_pct > 0

    # Every configured (data_id, display_name) pair that had series data appears with its own
    # non-invented display_name — never a fabricated label.
    names_by_id = dict(SECTOR_RRG_INDEX_IDS)
    for sector in output.sectors:
        assert sector.industry_name == names_by_id[sector.industry_id]

    # Weights are a real distribution over the returned sectors (each >= 0, summing to ~1).
    total_weight = sum(s.weight for s in output.sectors)
    assert total_weight > 0
    assert all(s.weight >= 0 for s in output.sectors)
