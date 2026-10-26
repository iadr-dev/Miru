"""Analyst-consensus / ESG / sector-classification capability domain.

Covers the original narrow TW gap-filling trio (`analyst_consensus`, `esg`, `sector_class`)
plus the richer analyst fields (recommendations tally, latest rating action, forward EPS/
revenue estimate, long-term growth) layered on top of the same `analyst_consensus` capability
for every market, TW included — confirmed live against `2330.TW` that yfinance's
`recommendations_summary`/`earnings_estimate`/`revenue_estimate`/`growth_estimates` frames are
populated for TW tickers exactly like any other market (an earlier "non-TW-only" gate here was
wrong: it discarded real data that fetch time already had in hand, not a genuine upstream gap).
Each field degrades to `None` independently when its own sub-frame is empty/missing (e.g. no
upgrades/downgrades history that day), never withheld market-wide.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, cast

import pandas as pd

from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult

from ._shared import _as_float, _as_int, _as_str, _get, _result

# The one cast() target type every already-known-to-be-a-row pandas value below uses.
type _Series = pd.Series


def _analyst_consensus(sym: Symbol, info: dict[str, Any], frames: dict[str, Any]) -> SourceResult:
    n_analysts = _as_int(_get(info, "numberOfAnalystOpinions")) or 0
    if n_analysts <= 0:
        # Coverage-gating rule: never render empty scaffolding as data.
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE, detail="no analyst coverage for this symbol"
            ),
        )
    consensus: dict[str, object] = {
        "symbol": sym.id,
        "market": sym.market.value,
        "as_of": date.today().isoformat(),
        "mean_target": _get(info, "targetMeanPrice"),
        "median_target": _get(info, "targetMedianPrice"),
        "high_target": _get(info, "targetHighPrice"),
        "low_target": _get(info, "targetLowPrice"),
        "number_of_analysts": n_analysts,
        "recommendation_key": _get(info, "recommendationKey"),
    }
    consensus.update(_analyst_consensus_extended(frames))
    return _result(
        payload={"consensus": consensus},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


type _Convert = Callable[[object], object]

# (frame, row label, ((output key, column, converter), ...)) for the frames whose wanted figures
# sit on one labelled row; the tuple order is the output key order.
_INDEXED_FIELDS: tuple[tuple[str, str, tuple[tuple[str, str, _Convert], ...]], ...] = (
    ("earnings_estimate", "0y", (("forward_eps_estimate", "avg", _as_float),)),
    ("revenue_estimate", "0y", (("forward_revenue_estimate", "avg", _as_float),)),
    ("growth_estimates", "LTG", (("long_term_growth_estimate", "stockTrend", _as_float),)),
    ("growth_estimates", "0y", (("current_year_growth_estimate", "stockTrend", _as_float),)),
    ("growth_estimates", "+1y", (("next_year_growth_estimate", "stockTrend", _as_float),)),
    (
        "eps_trend",
        "0y",
        (
            ("eps_trend_current", "current", _as_float),
            ("eps_trend_7_days_ago", "7daysAgo", _as_float),
            ("eps_trend_30_days_ago", "30daysAgo", _as_float),
            ("eps_trend_60_days_ago", "60daysAgo", _as_float),
            ("eps_trend_90_days_ago", "90daysAgo", _as_float),
        ),
    ),
    (
        "eps_revisions",
        "0y",
        (
            ("eps_revisions_up_last_30_days", "upLast30days", _as_int),
            ("eps_revisions_down_last_30_days", "downLast30days", _as_int),
        ),
    ),
)


def _analyst_consensus_extended(frames: dict[str, Any]) -> dict[str, object]:
    """Non-TW-only richer analyst fields — recommendations tally, latest rating action,
    forward EPS/revenue estimate, long-term growth. Any individually-missing sub-frame
    (e.g. no upgrades/downgrades history) degrades that one field to `None`, never the
    whole payload."""
    extended: dict[str, object] = {}
    rec_summary = frames.get("recommendations_summary")
    if isinstance(rec_summary, pd.DataFrame) and not rec_summary.empty:
        current = cast(_Series, rec_summary.iloc[0])
        extended["strong_buy"] = _as_int(_get(current, "strongBuy"))
        extended["buy"] = _as_int(_get(current, "buy"))
        extended["hold"] = _as_int(_get(current, "hold"))
        extended["sell"] = _as_int(_get(current, "sell"))
        extended["strong_sell"] = _as_int(_get(current, "strongSell"))

    upgrades = frames.get("upgrades_downgrades")
    if isinstance(upgrades, pd.DataFrame) and not upgrades.empty:
        latest = cast(_Series, upgrades.sort_index(ascending=False).iloc[0])
        extended["latest_upgrade_downgrade_firm"] = _as_str(_get(latest, "Firm"))
        extended["latest_upgrade_downgrade_action"] = _as_str(_get(latest, "Action"))

    for frame_key, row_label, fields in _INDEXED_FIELDS:
        frame = frames.get(frame_key)
        if isinstance(frame, pd.DataFrame) and row_label in frame.index:
            row = cast(_Series, frame.loc[row_label])
            for out_key, column, convert in fields:
                extended[out_key] = convert(_get(row, column))
    return extended


def _esg(sym: Symbol, info: dict[str, Any]) -> SourceResult:
    score = _get(info, "esgScore") or _get(info, "totalEsg")
    if score is None:
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE, detail="no ESG coverage for this symbol"
            ),
        )
    esg = {
        "symbol": sym.id,
        "market": sym.market.value,
        "as_of": date.today().isoformat(),
        "total_esg_score": score,
        "esg_risk_percentile": _get(info, "esgPercentile"),
    }
    return _result(payload={"esg": esg}, health=SourceHealth(status=HealthStatus.OK, detail="ok"))


def _sector_class(sym: Symbol, info: dict[str, Any]) -> SourceResult:
    sector = _get(info, "sector")
    industry = _get(info, "industry")
    if not sector and not industry:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="no sector/industry label"),
        )
    classification = {
        "symbol": sym.id,
        "market": sym.market.value,
        "sector": sector,
        "industry": industry,
        "sector_key": _get(info, "sectorKey"),
        "industry_key": _get(info, "industryKey"),
    }
    return _result(
        payload={"classification": classification},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _etf_aum(sym: Symbol, info: dict[str, Any]) -> SourceResult:
    """`etf_aum` — a TW-only gap-filler alongside the analyst/ESG/sector trio: Yahoo's own
    `totalAssets` figure for a listed ETF, confirmed live against 0050/00685L/00403A (plain,
    leveraged, and active ETF code shapes) to return a real NTD figure Yahoo itself computes —
    distinct from this codebase's own `etf_nav`-derived `units_outstanding x NAV` estimate,
    which `get_etf_list` prefers this field over once ingested (the issuer/exchange-adjacent
    figure over a locally-multiplied one). `region`/`category` on this same `.info` payload are
    NOT surfaced here — confirmed live they report `"US"` for every TW-listed ETF regardless of
    actual listing venue (a yfinance data-quality gap for non-US instruments, not a real value),
    so exposing them would display false data rather than an honest gap."""
    total_assets = _get(info, "totalAssets")
    if total_assets is None:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="no totalAssets for this fund"),
        )
    aum = {
        "symbol": sym.id,
        "market": sym.market.value,
        "as_of": date.today().isoformat(),
        "aum": _as_float(total_assets),
    }
    return _result(payload={"aum": aum}, health=SourceHealth(status=HealthStatus.OK, detail="ok"))


def _market_value(sym: Symbol, info: dict[str, Any]) -> SourceResult:
    """`market_value` — Yahoo's own `marketCap`, the non-TW equivalent of FinMind's
    backer/sponsor-only TW `market_value` feed. `get_company_profile` reads this back for a US/
    GB/JP/EU symbol's 市值 field instead of leaving it perpetually empty (no adapter was
    configured for these markets at all before this)."""
    market_cap = _get(info, "marketCap")
    if market_cap is None:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="no marketCap for this symbol"),
        )
    row = {
        "symbol": sym.id,
        "market": sym.market.value,
        "trade_date": date.today().isoformat(),
        "market_value": _as_int(market_cap),
    }
    return _result(
        payload={"rows": [row]}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


def _valuation_ratios(sym: Symbol, info: dict[str, Any]) -> SourceResult:
    """`valuation_ratios` — Yahoo's own trailing PER/PBR/dividend-yield, the non-TW equivalent of
    FinMind's TW-only daily valuation feed. Degrades field-by-field to `None` (`ValuationRatiosRow`
    itself requires all three, so a row is only emitted once all three resolve — matching
    `get_company_profile`'s own per-field `missing_fields` disclosure at the tool layer rather
    than fabricating a partial row here)."""
    per = _as_float(_get(info, "trailingPE"))
    pbr = _as_float(_get(info, "priceToBook"))
    dividend_yield = _as_float(_get(info, "dividendYield"))
    if per is None and pbr is None and dividend_yield is None:
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE, detail="no valuation ratios for this symbol"
            ),
        )
    row = {
        "symbol": sym.id,
        "market": sym.market.value,
        "trade_date": date.today().isoformat(),
        "dividend_yield": dividend_yield,
        "per": per,
        "pbr": pbr,
    }
    return _result(
        payload={"rows": [row]}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


def _profile(sym: Symbol, info: dict[str, Any]) -> SourceResult:
    """`profile` — yfinance's `.info` company-identity fields, the non-TW equivalent of MOPS's
    TW-only company-profile dump. Maps onto the SAME key names `get_company_profile`'s
    `_mops_fields` already reads (`chairman`/`auditor`/`headquarters`/`website`/`capital`/
    `spokesperson`/`founding_date`/`industry_classification_code`) so that tool needs no branch
    on source — `chairman` is `companyOfficers`' first `"Chairman"`/`"CEO"`-titled officer (Yahoo
    has no single dedicated chairman field); `auditor`/`spokesperson`/`founding_date` have no
    yfinance equivalent at all and stay `None` (never fabricated) — `get_company_profile`'s own
    `missing_fields` already discloses that honestly, same as it does for a MOPS-side gap."""
    officers = _get(info, "companyOfficers")
    chairman = None
    if isinstance(officers, list):
        for officer in cast("list[dict[str, Any]]", officers):
            title = str(officer.get("title") or "")
            if "chairman" in title.lower() or "ceo" in title.lower():
                chairman = officer.get("name")
                break
    address_parts = [
        _get(info, "address1"),
        _get(info, "city"),
        _get(info, "state"),
        _get(info, "country"),
    ]
    headquarters = ", ".join(str(p) for p in address_parts if p) or None
    profile = {
        "symbol": sym.id,
        "market": sym.market.value,
        "chairman": chairman,
        "auditor": None,
        "headquarters": headquarters,
        "website": _get(info, "website"),
        # `capital` (TW's 實收資本額, paid-in capital) has no yfinance equivalent —
        # `sharesOutstanding` is a share COUNT, not a capital amount, and `marketCap` is a
        # market valuation, not paid-in capital; neither is the same quantity, so this stays
        # `None` (an honest gap) rather than mislabeling either as 資本額.
        "capital": None,
        "spokesperson": None,
        "founding_date": None,
        "industry_classification_code": _get(info, "industry"),
    }
    if not any(v is not None for k, v in profile.items() if k not in ("symbol", "market")):
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE, detail="no profile identity fields for this symbol"
            ),
        )
    return _result(
        payload={"profile": profile}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


def _epoch_seconds_to_date(value: object) -> date | None:
    """yfinance's `fundInceptionDate` is raw Unix epoch SECONDS (confirmed live: `921024000` ->
    1999-03-10) — `pd.Timestamp(value)` with no `unit=` argument (this module's usual `_as_date`
    helper) misreads a bare int as epoch NANOSECONDS, silently producing a bogus 1970 date. This
    field needs its own explicit `unit="s"` conversion rather than reusing `_as_date`."""
    seconds = _as_float(value)
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC).date()


def _fund_profile(sym: Symbol, info: dict[str, Any]) -> SourceResult:
    """`fund_profile` — the ETF/mutual-fund equivalent of `_profile`'s company-identity fields,
    for the 總覽 tab's fund-info card. A fund has no chairman/EPS/capital (`_profile`/
    `_valuation_ratios` legitimately return those as `None` for a fund symbol), but yfinance's
    `.info` DOES carry real fund-specific fields nothing surfaces today: AUM, category, issuer,
    YTD return, distribution yield, inception date (confirmed live against QQQ). `totalAssets`
    is preferred over `netAssets` when both are present — same field `_etf_aum` already reads,
    kept consistent here rather than introducing a second AUM source of truth."""
    total_assets = _as_float(_get(info, "totalAssets")) or _as_float(_get(info, "netAssets"))
    category = _as_str(_get(info, "category"))
    fund_family = _as_str(_get(info, "fundFamily"))
    ytd_return = _as_float(_get(info, "ytdReturn"))
    distribution_yield = _as_float(_get(info, "yield"))
    inception_date = _epoch_seconds_to_date(_get(info, "fundInceptionDate"))
    if (
        total_assets is None
        and category is None
        and fund_family is None
        and ytd_return is None
        and distribution_yield is None
        and inception_date is None
    ):
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE, detail="symbol is not a fund/ETF (no fund profile)"
            ),
        )
    profile = {
        "symbol": sym.id,
        "market": sym.market.value,
        "as_of": date.today().isoformat(),
        "total_assets": total_assets,
        "category": category,
        "fund_family": fund_family,
        # `ytdReturn` is already percent-scaled in yfinance's own `.info` convention (confirmed
        # live: QQQ's `ytdReturn=16.99...` reads as 16.99%, not 1699%) — never multiplied by
        # 100. `yield` is the opposite: a 0-1 fraction (confirmed live: QQQ's `yield=0.0042`
        # means 0.42%), so it IS scaled by 100 below, same convention
        # `FundSectorWeightingRow.weight` uses.
        "ytd_return_pct": ytd_return,
        "distribution_yield_pct": (distribution_yield * 100) if distribution_yield else None,
        "inception_date": inception_date.isoformat() if inception_date else None,
    }
    return _result(
        payload={"profile": profile}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


__all__ = [
    "_analyst_consensus",
    "_analyst_consensus_extended",
    "_esg",
    "_etf_aum",
    "_fund_profile",
    "_market_value",
    "_profile",
    "_sector_class",
    "_valuation_ratios",
]
