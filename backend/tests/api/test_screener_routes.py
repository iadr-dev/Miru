"""The screener REST surface (`/analytics/screener/*`) driven through the REAL FastAPI routes via
`TestClient` — proving `get_factor_catalog`/`get_screener_presets`/`run_screener` dispatch through
the real registry (`tools/screener_tools.py`) and the real `analytics/screener/engine.py` funnel,
plus the saved-screens CRUD over `memory/screener_screens.py`. Mirrors
`test_tag_screener_routes.py`'s stub-router-swap + real-Parquet-seed pattern.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from analytics.models import (
    FactorScore,
    ScoreBand,
    ScoreProfile,
    ScoreResult,
    StockScoreBreakdown,
)
from analytics.scoring_store import upsert_score
from analytics.symbol_universe import SymbolType
from config.settings import Settings
from ingest.reader import ParquetMarketDataReader
from ingest.store import ParquetStore
from schemas.enums import Market, Tier
from schemas.market import Symbol
from tests.api.conftest import build_test_client, sign_in
from tests.tools.conftest import make_router
from tools import build_default_tool_registry

_RUN_DATE = date(2024, 1, 2)


def _score_result(symbol: str, momentum: float, revenue_flow: float) -> ScoreResult:
    return ScoreResult(
        symbol=symbol,
        market=Market.TW,
        as_of=_RUN_DATE,
        profile=ScoreProfile.SWING,
        symbol_type=SymbolType.STOCK,
        composite=70.0,
        band=ScoreBand.BULLISH_LEANING,
        breakdown=StockScoreBreakdown(
            momentum=FactorScore(name="momentum", percentile=momentum, description="d"),
            trend_quality=FactorScore(name="trend_quality", percentile=60.0, description="d"),
            value_guard=FactorScore(name="value_guard", percentile=60.0, description="d"),
            quality=FactorScore(name="quality", percentile=60.0, description="d"),
            revenue_flow=FactorScore(name="revenue_flow", percentile=revenue_flow, description="d"),
        ),
        regime_caveat="regime caveat",
        non_trading_caveat="non-trading caveat",
        normalization_caveat="normalization caveat",
    )


def _price_row(symbol: str, close: float, volume: float = 500_000) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "TW",
        "trade_date": _RUN_DATE.isoformat(),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
        "turnover": None,
    }


def _etf_nav_row(symbol: str, close: float, premium_discount: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "TW",
        "as_of": _RUN_DATE.isoformat(),
        "fund_name": f"{symbol} Fund",
        "units_outstanding": None,
        "units_outstanding_change": None,
        "close_price": close,
        "estimated_nav": close,
        "premium_discount_pct": premium_discount,
        "prior_day_nav": close,
    }


def _client_with_screener(settings: Settings, agent_answer: str, tmp_path: Path) -> TestClient:
    """A real `TestClient` whose registry is built with a real `ParquetMarketDataReader` +
    `ParquetStore` rooted at `tmp_path`, and whose `run_screener` tool shares the app's OWN
    session factory (so a test can seed `stock_scores` rows into the exact DB the route reads
    from)."""
    client = build_test_client(settings, agent_answer=agent_answer)
    client.__enter__()
    state = client.app.state.app_state
    stub_router, _ = make_router(Symbol(id="2330", market=Market.TW), {})
    reader = ParquetMarketDataReader(root=tmp_path)
    store = ParquetStore(root=tmp_path)
    registry = build_default_tool_registry(
        stub_router,
        market_data_reader=reader,
        parquet_store=store,
        session_factory=state.session_factory,
    )
    object.__setattr__(state, "tool_registry", registry)
    return client


class TestFactorCatalogRoute:
    def test_tw_catalog_has_score_factors_and_signals(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        resp = client.get("/api/v1/analytics/screener/factors", params={"market": "TW"})
        assert resp.status_code == 200
        catalog = resp.json()["catalog"]
        field_keys = {f["key"] for f in catalog["fields"]}
        assert "momentum_percentile" in field_keys
        assert len(catalog["signals"]) > 0

    def test_us_catalog_has_no_signals(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        resp = client.get("/api/v1/analytics/screener/factors", params={"market": "US"})
        assert resp.status_code == 200
        catalog = resp.json()["catalog"]
        assert catalog["signals"] == []
        field_keys = {f["key"] for f in catalog["fields"]}
        assert "pe_ratio" in field_keys

    def test_etf_catalog_scoped_to_partition_fields(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        resp = client.get("/api/v1/analytics/screener/factors", params={"market": "ETF"})
        assert resp.status_code == 200
        field_keys = {f["key"] for f in resp.json()["catalog"]["fields"]}
        assert field_keys == {
            "premium_discount_pct",
            "aum",
            "distribution_yield_ttm_pct",
            "expense_ratio_pct_approx",
            "return_1w_pct",
            "return_1m_pct",
            "return_1q_pct",
            "return_6m_pct",
            "return_1y_pct",
            "symbol_type",
            "distribution_frequency",
        }


class TestScreenerPresetsRoute:
    def test_tw_presets_reference_real_factors(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        resp = client.get("/api/v1/analytics/screener/presets", params={"market": "TW"})
        assert resp.status_code == 200
        presets = resp.json()["presets"]
        assert len(presets) > 0
        catalog_resp = client.get("/api/v1/analytics/screener/factors", params={"market": "TW"})
        known_fields = {f["key"] for f in catalog_resp.json()["catalog"]["fields"]}
        known_tags = {s["key"] for s in catalog_resp.json()["catalog"]["signals"]}
        for preset in presets:
            for condition in preset["conditions"]:
                assert condition["factor"] in known_fields or condition["factor"] in known_tags

    def test_us_presets_reference_real_factors(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        resp = client.get("/api/v1/analytics/screener/presets", params={"market": "US"})
        assert resp.status_code == 200
        presets = resp.json()["presets"]
        assert len(presets) > 0
        catalog_resp = client.get("/api/v1/analytics/screener/factors", params={"market": "US"})
        known_fields = {f["key"] for f in catalog_resp.json()["catalog"]["fields"]}
        for preset in presets:
            for condition in preset["conditions"]:
                assert condition["factor"] in known_fields

    def test_etf_presets_reference_real_factors(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        resp = client.get("/api/v1/analytics/screener/presets", params={"market": "ETF"})
        assert resp.status_code == 200
        presets = resp.json()["presets"]
        assert len(presets) > 0
        catalog_resp = client.get("/api/v1/analytics/screener/factors", params={"market": "ETF"})
        known_fields = {f["key"] for f in catalog_resp.json()["catalog"]["fields"]}
        for preset in presets:
            for condition in preset["conditions"]:
                assert condition["factor"] in known_fields


class TestRunScreenerRoute:
    def test_tw_numeric_condition_filters_by_score_percentile(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        state = client.app.state.app_state
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            _RUN_DATE,
            [_price_row("2330", 100.0), _price_row("1101", 50.0)],
        )

        async def _seed() -> None:
            async with state.session_factory() as session:
                await upsert_score(session, _score_result("2330", momentum=90.0, revenue_flow=80.0))
                await upsert_score(session, _score_result("1101", momentum=10.0, revenue_flow=20.0))
                await session.commit()

        assert client.portal is not None
        client.portal.call(_seed)

        resp = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "TW",
                "conditions": [{"factor": "momentum_percentile", "op": "gte", "value": 70}],
                "match": "AND",
            },
        )
        assert resp.status_code == 200
        result = resp.json()["result"]
        symbols = {s["symbol"] for s in result["survivors"]}
        assert symbols == {"2330"}

    def test_tw_raw_valuation_condition_filters_by_pe_ratio(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        """`pe_ratio`/`pb_ratio`/`dividend_yield` read the raw `valuation_ratios` (`TaiwanStockPER`)
        partition directly — a literal per-stock value, distinct from `momentum_percentile`'s
        cross-sectional rank in `stock_scores`."""
        client = _client_with_screener(settings, agent_answer, tmp_path)
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            _RUN_DATE,
            [_price_row("2330", 100.0), _price_row("1101", 50.0)],
        )
        store.write_partition(
            "TaiwanStockPER",
            "TW",
            _RUN_DATE,
            [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "trade_date": _RUN_DATE.isoformat(),
                    "dividend_yield": 2.0,
                    "per": 18.0,
                    "pbr": 5.0,
                },
                {
                    "symbol": "1101",
                    "market": "TW",
                    "trade_date": _RUN_DATE.isoformat(),
                    "dividend_yield": 6.0,
                    "per": 35.0,
                    "pbr": 1.2,
                },
            ],
        )
        resp = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "TW",
                "conditions": [{"factor": "pe_ratio", "op": "lt", "value": 20}],
            },
        )
        assert resp.status_code == 200
        survivors = {s["symbol"] for s in resp.json()["result"]["survivors"]}
        assert survivors == {"2330"}

    def test_tw_roe_condition_filters_via_whole_market_fundamentals_batch(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        """`roe_ttm` is computed by grouping the whole-market income + balance-sheet partitions
        by symbol and reusing `analytics/fundamentals.py::compute_fundamental_metrics` — the same
        ROE formula the per-symbol score path already uses, here run once over a bulk read instead
        of a per-symbol store call. `HIGH`: 400 net income / 1000 equity = 40% ROE. `LOW`: 50 net
        income / 1000 equity = 5% ROE."""
        client = _client_with_screener(settings, agent_answer, tmp_path)
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            _RUN_DATE,
            [_price_row("HIGH", 100.0), _price_row("LOW", 50.0)],
        )
        income_rows = [
            {
                "symbol": symbol,
                "market": "TW",
                "period_end": f"2023-{quarter * 3:02d}-30",
                "statement_type": "income",
                "period_type": "quarterly",
                "item": "NetIncome",
                "origin_name": "NetIncome",
                "value": net_income,
            }
            for symbol, net_income in (("HIGH", 100.0), ("LOW", 12.5))
            for quarter in range(1, 5)
        ]
        balance_rows = [
            {
                "symbol": symbol,
                "market": "TW",
                "period_end": "2023-12-30",
                "statement_type": "balance_sheet",
                "period_type": "quarterly",
                "item": "Equity",
                "origin_name": "Equity",
                "value": 1000.0,
            }
            for symbol in ("HIGH", "LOW")
        ]
        store.write_partition(
            "TaiwanStockFinancialStatements", "TW", date(2023, 12, 30), income_rows
        )
        store.write_partition("TaiwanStockBalanceSheet", "TW", date(2023, 12, 30), balance_rows)
        resp = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "TW",
                "conditions": [{"factor": "roe_ttm", "op": "gte", "value": 10}],
            },
        )
        assert resp.status_code == 200
        survivors = {s["symbol"] for s in resp.json()["result"]["survivors"]}
        assert survivors == {"HIGH"}

    def test_tw_rsi_condition_filters_via_whole_market_price_history(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        """`rsi_14` reads the same whole-market `price_eod` window `_gated_tw_universe` already
        gates on, builds each symbol's own `PriceBar` series, and calls the real
        `analytics/indicators.py::rsi` — the identical function `batch.py`'s boolean cross-event
        tags already use, just returning the literal latest value. `9990` (a real-shaped 4-digit
        TW code — `is_scoreable_symbol` rejects longer names): 20 straight up days (RSI(14) ==
        100, no losses at all). `9991`: 20 straight DOWN days (RSI(14) == 0, no gains at all) —
        must not match a `>= 70` condition."""
        client = _client_with_screener(settings, agent_answer, tmp_path)
        store = ParquetStore(root=tmp_path)
        calendar = TradingDayCalendar()
        days = list(calendar.business_days_between(_RUN_DATE - timedelta(days=30), _RUN_DATE))[-20:]
        for i, day in enumerate(days):
            rows = [
                _price_row("9990", 100.0 + i, volume=500_000),
                _price_row("9991", 100.0 - i, volume=500_000),
            ]
            for row in rows:
                row["trade_date"] = day.isoformat()
            store.write_partition("TaiwanStockPrice", "TW", day, rows)
        resp = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "TW",
                "conditions": [{"factor": "rsi_14", "op": "gte", "value": 70}],
            },
        )
        assert resp.status_code == 200
        survivors = {s["symbol"] for s in resp.json()["result"]["survivors"]}
        assert survivors == {"9990"}

    def test_count_only_skips_row_hydration(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        state = client.app.state.app_state
        store = ParquetStore(root=tmp_path)
        store.write_partition("TaiwanStockPrice", "TW", _RUN_DATE, [_price_row("2330", 100.0)])

        async def _seed() -> None:
            async with state.session_factory() as session:
                await upsert_score(session, _score_result("2330", momentum=90.0, revenue_flow=80.0))
                await session.commit()

        assert client.portal is not None
        client.portal.call(_seed)

        resp = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "TW",
                "conditions": [{"factor": "momentum_percentile", "op": "gte", "value": 70}],
                "count_only": True,
            },
        )
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["survivors"] == []
        assert result["funnel"]["setup"] == 1

    def test_etf_premium_discount_condition(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "etf_nav",
            "TW",
            _RUN_DATE,
            [
                _etf_nav_row("DISCOUNT", 50.0, -2.0),
                _etf_nav_row("PREMIUM", 50.0, 2.0),
            ],
        )
        resp = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "ETF",
                "conditions": [{"factor": "premium_discount_pct", "op": "lt", "value": -0.5}],
            },
        )
        assert resp.status_code == 200
        survivors = {s["symbol"] for s in resp.json()["result"]["survivors"]}
        assert survivors == {"DISCOUNT"}

    def test_etf_excludes_leveraged_and_inverse_by_default(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        """`exclude_leveraged_inverse` defaults to True for ETF (`gates.py`'s `_ETF_DEFAULTS`) —
        a plain tracker (`0050`), a 2x-leveraged product (`00631L`), and an inverse product
        (`00632R`) are gated purely by code-shape (`symbol_universe.classify_symbol_type`'s
        documented `L`/`R` suffix convention), with no condition applied at all."""
        client = _client_with_screener(settings, agent_answer, tmp_path)
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "etf_nav",
            "TW",
            _RUN_DATE,
            [
                _etf_nav_row("0050", 140.0, 0.0),
                _etf_nav_row("00631L", 20.0, 0.0),
                _etf_nav_row("00632R", 9.0, 0.0),
            ],
        )
        resp = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "ETF",
                "conditions": [{"factor": "premium_discount_pct", "op": "gte", "value": -100}],
            },
        )
        assert resp.status_code == 200
        survivors = {s["symbol"] for s in resp.json()["result"]["survivors"]}
        assert survivors == {"0050"}

    def test_etf_expense_ratio_condition_filters_by_cost(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "etf_nav",
            "TW",
            _RUN_DATE,
            [_etf_nav_row("CHEAP", 50.0, 0.0), _etf_nav_row("PRICEY", 50.0, 0.0)],
        )
        store.write_partition(
            "etf_expense_ratio",
            "TW",
            _RUN_DATE,
            [
                {
                    "symbol": "CHEAP",
                    "market": "TW",
                    "as_of": _RUN_DATE.isoformat(),
                    "management_fee_pct": 0.15,
                    "custody_fee_pct": 0.03,
                    "total_expense_ratio_pct_approx": 0.18,
                    "custodian_bank": None,
                },
                {
                    "symbol": "PRICEY",
                    "market": "TW",
                    "as_of": _RUN_DATE.isoformat(),
                    "management_fee_pct": 1.0,
                    "custody_fee_pct": 0.2,
                    "total_expense_ratio_pct_approx": 1.2,
                    "custodian_bank": None,
                },
            ],
        )
        resp = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "ETF",
                "conditions": [{"factor": "expense_ratio_pct_approx", "op": "lt", "value": 0.3}],
            },
        )
        assert resp.status_code == 200
        survivors = {s["symbol"] for s in resp.json()["result"]["survivors"]}
        assert survivors == {"CHEAP"}

    def test_etf_monthly_high_yield_preset_matches_by_real_dividend_events(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        """Proves the "月月配高股息" preset (`presets.py::etf_monthly_high_yield`) actually
        resolves against real `dividends` events — 12 monthly cash distributions totaling more
        than 5% of the close price for `00900` (a real ETF-shaped code, required for
        `classify_symbol_type` to route it through the ETF branch rather than the stock-exclusion
        check), versus one annual distribution for `00901`."""
        client = _client_with_screener(settings, agent_answer, tmp_path)
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "etf_nav",
            "TW",
            _RUN_DATE,
            [_etf_nav_row("00900", 20.0, 0.0), _etf_nav_row("00901", 20.0, 0.0)],
        )
        monthly_events = [
            {
                "symbol": "00900",
                "market": "TW",
                "announced_date": date(2023, m, 1).isoformat(),
                "fiscal_year": "2023",
                "cash_dividend": 0.1,
                "stock_dividend": None,
                "cash_ex_dividend_date": date(2023, m, 15).isoformat(),
                "stock_ex_dividend_date": None,
                "cash_dividend_payment_date": None,
            }
            for m in range(1, 13)
        ]
        annual_event = {
            "symbol": "00901",
            "market": "TW",
            "announced_date": date(2023, 6, 1).isoformat(),
            "fiscal_year": "2023",
            "cash_dividend": 0.5,
            "stock_dividend": None,
            "cash_ex_dividend_date": date(2023, 6, 15).isoformat(),
            "stock_ex_dividend_date": None,
            "cash_dividend_payment_date": None,
        }
        for month in range(1, 13):
            prefix = f"2023-{month:02d}"
            month_rows = [
                row for row in monthly_events if row["cash_ex_dividend_date"].startswith(prefix)
            ]
            if month == 6:
                month_rows = [*month_rows, annual_event]
            store.write_partition("TaiwanStockDividend", "TW", date(2023, month, 15), month_rows)
        resp = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "ETF",
                "conditions": [
                    {"factor": "distribution_frequency", "op": "eq", "value": "月配"},
                    {"factor": "distribution_yield_ttm_pct", "op": "gte", "value": 5},
                ],
                "match": "AND",
            },
        )
        assert resp.status_code == 200
        survivors = {s["symbol"] for s in resp.json()["result"]["survivors"]}
        assert survivors == {"00900"}

    def test_etf_return_1y_reads_price_eod_scoped_to_etf_symbols_only(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        """Regression test: `_etf_trailing_returns_by_symbol` must pass `symbols=` to
        `read_range` so DuckDB filters at the SQL layer — reading the whole `price_eod` partition
        unfiltered for a ~250-trading-day window (as an earlier version of this code did) pulls
        every TW stock's daily bars just to keep the ~300 ETF rows, and hung a real screener
        request in manual testing. Seeds one stock (`2330`) and one ETF (`00900`) sharing the same
        price partition; `2330`'s price series is asserted to have NO influence on `00900`'s
        computed return."""
        client = _client_with_screener(settings, agent_answer, tmp_path)
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "etf_nav",
            "TW",
            _RUN_DATE,
            [_etf_nav_row("00900", 20.0, 0.0)],
        )
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            _RUN_DATE,
            [_price_row("00900", 22.0), _price_row("2330", 999.0)],
        )
        # ~300 calendar days back — inside `_etf_return_1y_by_symbol`'s ~250-trading-day window
        # (≈350 calendar days for a 5-day trading week), unlike a full calendar year back which
        # falls just outside it.
        earlier = _RUN_DATE - timedelta(days=300)
        store.write_partition(
            "TaiwanStockPrice",
            "TW",
            earlier,
            [_price_row("00900", 20.0), _price_row("2330", 500.0)],
        )
        resp = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "ETF",
                "conditions": [{"factor": "return_1y_pct", "op": "gte", "value": 5}],
            },
        )
        assert resp.status_code == 200
        survivors = {s["symbol"] for s in resp.json()["result"]["survivors"]}
        assert survivors == {"00900"}

    def test_etf_multi_window_returns_computed_from_one_shared_price_read(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        """`_ETF_RETURN_WINDOWS_TRADING_DAYS` computes 1w/1m/1q/6m/1y returns from ONE shared
        ~1-year `price_eod` read, slicing that same series per window rather than reading once
        per window. `00900`'s close DECLINES from 200 to 100 across the year, then spikes up in
        its final 5 trading days (100 -> 110, +10%) — proving `return_1w_pct` reads only the
        SHORT tail (its own window-start close is ~100, right before the spike) while
        `return_1y_pct` reads the full year's decline-then-spike (window-start close is 200, so
        the net move is still negative overall), so the two windows genuinely disagree rather
        than coincidentally matching."""
        client = _client_with_screener(settings, agent_answer, tmp_path)
        store = ParquetStore(root=tmp_path)
        store.write_partition("etf_nav", "TW", _RUN_DATE, [_etf_nav_row("00900", 110.0, 0.0)])
        calendar = TradingDayCalendar()
        days = list(calendar.business_days_between(_RUN_DATE - timedelta(days=400), _RUN_DATE))[
            -250:
        ]
        decline_days = len(days) - 5
        for i, day in enumerate(days):
            if i < decline_days:
                close = 200.0 - (i / max(decline_days - 1, 1)) * 100.0  # 200 -> 100
            else:
                close = 100.0 + (i - decline_days + 1) * 2.5  # final spike: 102.5 -> 110
            row = _price_row("00900", close)
            row["trade_date"] = day.isoformat()
            store.write_partition("TaiwanStockPrice", "TW", day, [row])
        resp = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "ETF",
                "conditions": [{"factor": "return_1w_pct", "op": "gte", "value": 5}],
            },
        )
        assert resp.status_code == 200
        survivors = {s["symbol"] for s in resp.json()["result"]["survivors"]}
        assert survivors == {"00900"}

        resp_1y = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "ETF",
                "conditions": [{"factor": "return_1y_pct", "op": "gte", "value": 5}],
            },
        )
        assert resp_1y.status_code == 200
        survivors_1y = {s["symbol"] for s in resp_1y.json()["result"]["survivors"]}
        assert survivors_1y == set()  # full-year window still nets a decline (200 -> 110)

    def test_etf_min_aum_gate_filters_by_fund_size(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        store = ParquetStore(root=tmp_path)
        store.write_partition(
            "etf_nav",
            "TW",
            _RUN_DATE,
            [_etf_nav_row("SMALL", 20.0, 0.0), _etf_nav_row("BIG", 140.0, 0.0)],
        )
        store.write_partition(
            "etf_aum",
            "TW",
            _RUN_DATE,
            [
                {"symbol": "SMALL", "market": "TW", "as_of": _RUN_DATE.isoformat(), "aum": 1e8},
                {"symbol": "BIG", "market": "TW", "as_of": _RUN_DATE.isoformat(), "aum": 5e10},
            ],
        )
        resp = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "ETF",
                "conditions": [{"factor": "aum", "op": "gte", "value": 0}],
                "gate_overrides": {"min_aum": 1e10},
            },
        )
        assert resp.status_code == 200
        survivors = {s["symbol"] for s in resp.json()["result"]["survivors"]}
        assert survivors == {"BIG"}

    def test_unknown_factor_key_degrades_gracefully(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        store = ParquetStore(root=tmp_path)
        store.write_partition("TaiwanStockPrice", "TW", _RUN_DATE, [_price_row("A", 100.0)])
        resp = client.post(
            "/api/v1/analytics/screener/run",
            json={
                "market": "TW",
                "conditions": [{"factor": "not_a_real_factor", "op": "gte", "value": 1}],
            },
        )
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["survivors"] == []
        assert any("unknown" in note for note in result["degraded"])


class TestScreenerScreensCrud:
    def test_save_list_update_delete(
        self, settings: Settings, agent_answer: str, tmp_path: Path
    ) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        headers, _ = sign_in(client, settings, tier=Tier.FREE)

        save_resp = client.post(
            "/api/v1/analytics/screener/screens",
            json={
                "name": "我的成長股",
                "market": "TW",
                "conditions": [{"factor": "momentum_percentile", "op": "gte", "value": 70}],
                "match": "AND",
            },
            headers=headers,
        )
        assert save_resp.status_code == 201
        screen_id = save_resp.json()["id"]

        list_resp = client.get(
            "/api/v1/analytics/screener/screens", params={"market": "TW"}, headers=headers
        )
        assert list_resp.status_code == 200
        assert len(list_resp.json()["screens"]) == 1

        conflict_resp = client.post(
            "/api/v1/analytics/screener/screens",
            json={"name": "我的成長股", "market": "TW", "conditions": []},
            headers=headers,
        )
        assert conflict_resp.status_code == 409

        update_resp = client.patch(
            f"/api/v1/analytics/screener/screens/{screen_id}",
            json={"name": "我的成長股2"},
            headers=headers,
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["name"] == "我的成長股2"

        delete_resp = client.delete(
            f"/api/v1/analytics/screener/screens/{screen_id}", headers=headers
        )
        assert delete_resp.status_code == 204

        empty_list_resp = client.get(
            "/api/v1/analytics/screener/screens", params={"market": "TW"}, headers=headers
        )
        assert empty_list_resp.json()["screens"] == []

    def test_requires_sign_in(self, settings: Settings, agent_answer: str, tmp_path: Path) -> None:
        client = _client_with_screener(settings, agent_answer, tmp_path)
        resp = client.get("/api/v1/analytics/screener/screens", params={"market": "TW"})
        assert resp.status_code == 401
