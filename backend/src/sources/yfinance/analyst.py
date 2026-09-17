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

from datetime import date
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

    earnings_est = frames.get("earnings_estimate")
    if isinstance(earnings_est, pd.DataFrame) and "0y" in earnings_est.index:
        eps_row = cast(_Series, earnings_est.loc["0y"])
        extended["forward_eps_estimate"] = _as_float(_get(eps_row, "avg"))

    revenue_est = frames.get("revenue_estimate")
    if isinstance(revenue_est, pd.DataFrame) and "0y" in revenue_est.index:
        revenue_row = cast(_Series, revenue_est.loc["0y"])
        extended["forward_revenue_estimate"] = _as_float(_get(revenue_row, "avg"))

    growth_est = frames.get("growth_estimates")
    if isinstance(growth_est, pd.DataFrame) and "LTG" in growth_est.index:
        growth_row = cast(_Series, growth_est.loc["LTG"])
        extended["long_term_growth_estimate"] = _as_float(_get(growth_row, "stockTrend"))
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


__all__ = ["_analyst_consensus", "_analyst_consensus_extended", "_esg", "_etf_aum", "_sector_class"]
