"""`sec_filings` capability domain — wraps `Ticker.get_sec_filings()`, US-only (no equivalent feed
exists for any other market this codebase serves; see `Capability.SEC_FILINGS`'s own enum comment
and `SecFiling`'s own docstring in `schemas/capabilities.py`).
"""

from __future__ import annotations

from typing import Any, cast

from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult

from ._shared import _as_date, _as_str, _get, _result

_MAX_FILINGS = 20  # bounded per-symbol list — most-recent-first, never a full filing-history dump


def _sec_filings(sym: Symbol, frames: dict[str, Any]) -> SourceResult:
    raw_filings = frames.get("sec_filings")
    if not isinstance(raw_filings, list) or not raw_filings:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="no SEC filings for this symbol"),
        )
    rows: list[dict[str, object]] = []
    for raw in cast("list[object]", raw_filings)[:_MAX_FILINGS]:
        filing_date = _as_date(_get(raw, "date"))
        title = _as_str(_get(raw, "title"))
        filing_type = _as_str(_get(raw, "type"))
        if filing_date is None or title is None or filing_type is None:
            continue  # a filing row missing its own identity is skipped, never fabricated
        exhibits = _get(raw, "exhibits")
        edgar_url = _first_exhibit_url(exhibits)
        rows.append(
            {
                "symbol": sym.id,
                "market": sym.market.value,
                "filing_date": filing_date.isoformat(),
                "filing_type": filing_type,
                "title": title,
                "edgar_url": edgar_url,
            }
        )
    if not rows:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="no usable SEC filing rows"),
        )
    return _result(
        payload={"filings": rows}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


def _first_exhibit_url(exhibits: object) -> str | None:
    """`get_sec_filings()` rows carry an `exhibits` list of `{type, url}` dicts — take the first
    URL present as the one representative EDGAR link, rather than surfacing the whole list."""
    if not isinstance(exhibits, list) or not exhibits:
        return None
    first = cast("list[object]", exhibits)[0]
    url = _get(first, "url")
    return _as_str(url)


__all__ = ["_sec_filings"]
