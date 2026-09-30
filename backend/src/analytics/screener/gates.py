"""Screener hard gates (stage ⓐ of the funnel, README.md §1/§5.1) — binary elimination applied
BEFORE any setup condition or rank. `SCREENER_GATES` are the defaults; `GateOverrides` lets a
caller relax/tighten them per request. **Liquidity is a gate, never a scored factor** — this
module's output can only ever exclude a symbol, never rank or weight it (governance non-negotiable,
`docs/web/screener/README.md` header).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class GateDefaults(BaseModel):
    """The default gate thresholds for one market. `min_price`/`min_avg_volume`/`min_market_cap`
    are the liquidity/price/market-cap floors; the three exclude flags default to True (safest
    default — a fresh screen starts with disposition/attention/full-cash-delivery stocks and
    newly-listed 蜜月期 symbols already filtered out, matching what a trader expects "screening"
    to mean without having to discover and tick three checkboxes first)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_price: float = 0.0
    min_avg_volume: float = 0.0
    min_market_cap: float = 0.0
    exclude_disposition: bool = True
    exclude_attention: bool = True
    exclude_full_cash_delivery: bool = True
    honeymoon_exclude_days: int = 0  # 0 = no 蜜月期 (newly-listed) exclusion window
    # ETF-only (TW): a leveraged (00631L-style, code ends `L`) or inverse (00632R-style, ends `R`)
    # ETF's daily-rebalancing decay makes it a fundamentally different instrument from a plain
    # tracker — `symbol_universe.classify_symbol_type`'s own documented exchange convention.
    # Meaningless for TW/US stocks (no such classification exists for them), left `False` there.
    exclude_leveraged_inverse: bool = False
    # ETF-only: a size floor on `aum` (億元 NT$) — the ETF equivalent of a stock's `min_market_cap`
    # (an ETF has no market cap of its own; AUM is the analogous "how big is this fund" gate).
    min_aum: float = 0.0


class GateOverrides(BaseModel):
    """A caller's per-request relaxation/tightening of `SCREENER_GATES`. Every field is optional
    — an unset field keeps the market default (`run_screener`'s `gate_overrides` is `None` by
    default, meaning "use the defaults as-is")."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_price: float | None = None
    min_avg_volume: float | None = None
    min_market_cap: float | None = None
    exclude_disposition: bool | None = None
    exclude_attention: bool | None = None
    exclude_full_cash_delivery: bool | None = None
    honeymoon_exclude_days: int | None = None
    exclude_leveraged_inverse: bool | None = None
    min_aum: float | None = None


# TW defaults follow the same liquidity-floor spirit as `disposition_board_tools.py`'s own risk
# thresholds — bounded, documented, correctable, not a verbatim regulator-published figure.
_TW_DEFAULTS = GateDefaults(
    min_price=3.0,
    min_avg_volume=100_000,  # shares/day, ~100張 — a conventional liquidity floor for a
    # screenable TW stock, low enough not to exclude legitimate small-caps
    min_market_cap=1_000_000_000,  # NT$10億
)

_US_DEFAULTS = GateDefaults(
    min_price=1.0,
    min_avg_volume=100_000,
    min_market_cap=100_000_000,  # US$100M — conventional micro-cap floor
)

_ETF_DEFAULTS = GateDefaults(
    min_price=0.0,
    min_avg_volume=10_000,
    min_market_cap=0.0,
    exclude_disposition=False,  # ETFs aren't 處置/注意 candidates in this codebase's data model
    exclude_attention=False,
    exclude_full_cash_delivery=False,
    exclude_leveraged_inverse=True,  # safest default, same "screening means this by default"
    # reasoning the other three exclude flags already document — a fresh ETF screen shouldn't
    # surface a 2x-leveraged/inverse product unless the caller explicitly opts in.
    min_aum=0.0,
)

SCREENER_GATES: dict[str, GateDefaults] = {
    "TW": _TW_DEFAULTS,
    "US": _US_DEFAULTS,
    "ETF": _ETF_DEFAULTS,
}


def resolve_gates(market: str, overrides: GateOverrides | None) -> GateDefaults:
    """The effective gate thresholds for `market`, with any caller `overrides` applied on top of
    the market default. An unrecognized market falls back to the TW defaults (the most
    conservative floor set) rather than raising."""
    base = SCREENER_GATES.get(market, _TW_DEFAULTS)
    if overrides is None:
        return base
    updates = {field: value for field, value in overrides.model_dump().items() if value is not None}
    return base.model_copy(update=updates)


__all__ = [
    "SCREENER_GATES",
    "GateDefaults",
    "GateOverrides",
    "resolve_gates",
]
