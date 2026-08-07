"""The isolated synchronous-`yfinance`-SDK-call layer.

Every function here talks directly to the `yfinance` library and therefore blocks the
calling thread — each one is invoked exclusively via `anyio.to_thread.run_sync` at its call
site in `_adapter.py`/`discovery.py`/`holders.py`, never awaited or called inline on the
event loop. Grouping them together keeps that "runs inside a threadpool, touches
dynamically-shaped 3rd-party objects" contract in one place, so every other module in this
package only ever handles already-fetched, in-process pandas/dict objects.
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd

from ._shared import _init_tz_cache


def _fetch_info_blocking(yahoo_symbol: str) -> dict[str, Any]:
    # `Ticker.get_info()`/frame-returning properties are dynamically-shaped `dict[str,
    # Any]`/`DataFrame`; this is the isolated 3rd-party edge, never crossed by callers of
    # fetch().
    # Lazy import: heavy/optional dep, loaded only inside this threadpool-run function so
    # importing the adapter package itself stays cheap.
    import yfinance as yf  # noqa: PLC0415

    _init_tz_cache()
    ticker = yf.Ticker(yahoo_symbol)
    raw_info: dict[str, Any] = cast("dict[str, Any]", ticker.get_info())
    return raw_info


def _fetch_ticker_frames_blocking(yahoo_symbol: str) -> dict[str, Any]:
    """One blocking call site that gathers every `Ticker`-scoped frame/object a `fetch()` for
    this symbol might need, so each capability handler only ever touches already-fetched,
    in-process pandas objects — never triggers its own separate blocking round trip."""
    import yfinance as yf  # noqa: PLC0415

    _init_tz_cache()
    ticker = yf.Ticker(yahoo_symbol)
    return {
        "info": ticker.get_info(),
        "history": ticker.history(period="5d", auto_adjust=False),
        "financials": ticker.financials,
        "balance_sheet": ticker.balance_sheet,
        "cash_flow": ticker.cash_flow,
        "dividends": ticker.dividends,
        "splits": ticker.splits,
        "recommendations_summary": ticker.recommendations_summary,
        "upgrades_downgrades": ticker.upgrades_downgrades,
        "growth_estimates": ticker.growth_estimates,
        "earnings_estimate": ticker.earnings_estimate,
        "revenue_estimate": ticker.revenue_estimate,
        "options": ticker.options,
        "option_chain": ticker.option_chain,  # callable — invoked per-expiry by the handler
        "institutional_holders": ticker.institutional_holders,
        "mutualfund_holders": ticker.mutualfund_holders,
        "insider_transactions": ticker.insider_transactions,
        "funds_data": _safe_funds_data(ticker),
    }


def _safe_funds_data(ticker: Any) -> Any:  # dynamically-shaped 3rd-party edge
    """`Ticker.funds_data` raises for a non-fund symbol (an ordinary equity) rather than
    returning `None` — normalize that expected case to `None` here so the handler doesn't need
    its own try/except for what is a routine "this symbol isn't a fund" outcome."""
    try:
        return ticker.funds_data
    except Exception:  # a non-fund symbol raising here is expected, not an error
        return None


def _fetch_search_blocking(query: str) -> list[dict[str, Any]]:
    import yfinance as yf  # noqa: PLC0415

    _init_tz_cache()
    result = yf.Search(query)
    quotes = cast("object", result.quotes)
    return cast("list[dict[str, Any]]", quotes) if isinstance(quotes, list) else []


def _fetch_lookup_blocking(query: str) -> pd.DataFrame:
    import yfinance as yf  # noqa: PLC0415

    _init_tz_cache()
    lookup = yf.Lookup(query)
    frame: Any = lookup.all
    return cast("pd.DataFrame", frame)


def _fetch_calendars_blocking() -> dict[str, pd.DataFrame]:
    import yfinance as yf  # noqa: PLC0415

    _init_tz_cache()
    cal = yf.Calendars()
    return {
        "earnings": cal.earnings_calendar,
        "ipo": cal.ipo_info_calendar,
        "economic": cal.economic_events_calendar,
    }


def _fetch_sector_blocking(sector_key: str, region: str) -> dict[str, pd.DataFrame | None]:
    import yfinance as yf  # noqa: PLC0415

    _init_tz_cache()
    sector = yf.Sector(sector_key, region=region)
    return {
        "top_companies": sector.top_companies,
        "top_etfs": _dict_to_frame(sector.top_etfs),
        "top_mutual_funds": _dict_to_frame(sector.top_mutual_funds),
    }


def _fetch_industry_blocking(
    industry_key: str, region: str
) -> tuple[dict[str, pd.DataFrame | None], str]:
    import yfinance as yf  # noqa: PLC0415

    _init_tz_cache()
    industry = yf.Industry(industry_key, region=region)
    frames: dict[str, pd.DataFrame | None] = {
        "top_companies": industry.top_companies,
        "top_growth_companies": industry.top_growth_companies,
        "top_performing_companies": industry.top_performing_companies,
    }
    return frames, str(industry.sector_key)


def _dict_to_frame(mapping: object) -> pd.DataFrame | None:
    """`Sector.top_etfs`/`top_mutual_funds` return a plain `{symbol: name}` dict, not a
    `DataFrame` like `top_companies` — normalize to the same tabular shape the handler expects
    so one code path can walk all three sub-lists."""
    if not isinstance(mapping, dict):
        return None
    rows = [{"symbol": k, "name": v} for k, v in cast("dict[str, object]", mapping).items()]
    return pd.DataFrame(rows).set_index("symbol") if rows else None


def _fetch_market_status_blocking(region: str) -> dict[str, Any]:
    import yfinance as yf  # noqa: PLC0415

    _init_tz_cache()
    market = yf.Market(region)
    return cast("dict[str, Any]", market.status)


def _fetch_option_chain_blocking(yahoo_symbol: str, expiry: str) -> Any:
    import yfinance as yf  # noqa: PLC0415

    _init_tz_cache()
    ticker = yf.Ticker(yahoo_symbol)
    return ticker.option_chain(expiry)


__all__ = [
    "_dict_to_frame",
    "_fetch_calendars_blocking",
    "_fetch_industry_blocking",
    "_fetch_info_blocking",
    "_fetch_lookup_blocking",
    "_fetch_market_status_blocking",
    "_fetch_option_chain_blocking",
    "_fetch_search_blocking",
    "_fetch_sector_blocking",
    "_fetch_ticker_frames_blocking",
    "_safe_funds_data",
]
