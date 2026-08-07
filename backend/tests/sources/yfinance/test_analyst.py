"""Tests for the analyst-consensus/ESG/sector-class capability domain — the TW narrow path
plus the non-TW extended analyst fields. Recorded fixtures, no live network."""

from __future__ import annotations

import pandas as pd
import pytest

from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.yfinance import YFinanceAdapter
from tests.sources.conftest import load_fixture

from ._helpers import empty_frames, patch_info, patch_run_sync


async def test_fetch_analyst_consensus(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("yfinance", "info_2330_tw.json")
    patch_info(monkeypatch, body)
    adapter = YFinanceAdapter()
    sym = Symbol(id="2330", market=Market.TW, yahoo="2330.TW")

    result = await adapter.fetch(Capability.ANALYST_CONSENSUS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "YFINANCE"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    consensus = result.payload["consensus"]
    assert consensus["number_of_analysts"] == 28  # type: ignore[index]
    # TW payloads never carry the non-TW-only extended fields.
    assert "strong_buy" not in consensus  # type: ignore[operator]


async def test_fetch_analyst_consensus_gates_on_zero_analysts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("yfinance", "info_no_coverage.json")
    patch_info(monkeypatch, body)
    adapter = YFinanceAdapter()
    sym = Symbol(id="1234", market=Market.TW)

    result = await adapter.fetch(Capability.ANALYST_CONSENSUS, sym, tls_policy=TLSPolicy.STRICT)

    # Coverage-gating: never render empty scaffolding as data.
    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_esg(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("yfinance", "info_2330_tw.json")
    patch_info(monkeypatch, body)
    adapter = YFinanceAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.ESG, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    assert result.payload["esg"]["total_esg_score"] == 18.4  # type: ignore[index]


async def test_fetch_sector_class(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("yfinance", "info_2330_tw.json")
    patch_info(monkeypatch, body)
    adapter = YFinanceAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.SECTOR_CLASS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.payload["classification"]["sector"] == "Technology"  # type: ignore[index]


async def test_fetch_analyst_consensus_extended_us(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = empty_frames()
    frames["info"] = {"numberOfAnalystOpinions": 41, "targetMeanPrice": 323.28}
    frames["recommendations_summary"] = pd.DataFrame(
        [{"period": "0m", "strongBuy": 6, "buy": 22, "hold": 14, "sell": 2, "strongSell": 2}]
    )
    frames["upgrades_downgrades"] = pd.DataFrame(
        {"Firm": ["JP Morgan"], "Action": ["main"]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-07-31")]),
    )
    frames["earnings_estimate"] = pd.DataFrame({"avg": [8.81]}, index=["0y"])
    frames["revenue_estimate"] = pd.DataFrame({"avg": [477_391_182_150.0]}, index=["0y"])
    frames["growth_estimates"] = pd.DataFrame({"stockTrend": [0.122]}, index=["LTG"])
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=frames)
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.ANALYST_CONSENSUS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    consensus = result.payload["consensus"]
    assert consensus["strong_buy"] == 6  # type: ignore[index]
    assert consensus["latest_upgrade_downgrade_firm"] == "JP Morgan"  # type: ignore[index]
    assert consensus["forward_eps_estimate"] == pytest.approx(8.81)  # type: ignore[index]
    assert consensus["long_term_growth_estimate"] == pytest.approx(0.122)  # type: ignore[index]
