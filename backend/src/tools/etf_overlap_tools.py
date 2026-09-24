"""ETF overlap-checker + multi-market fund total-return comparison — two derived, N-symbol
fan-out reads over capabilities already wrapped elsewhere in this registry.

## `get_etf_overlap` (Reddit r/ETFs gap: existing free tools only compare 2 funds at a time)

Wraps the SAME `Capability.FUND_HOLDINGS` read `analyst_tools.GetFundHoldingsTool` already
exposes as `get_fund_holdings` — this tool only fans that one router call out over 3+
caller-named symbols and computes the cross-ETF overlap itself; it adds no new adapter/source.
`DataClass.PUBLIC` for the same reason `get_fund_holdings` itself is `PUBLIC`: this is a derived
read over that same public capability, just repeated per symbol.

**Scope: non-TW (US/GB/JP/EU) ETFs/funds only**, same restriction `NonTwSymbolInput` already
enforces for `get_fund_holdings`. TW passive ETFs (0050/00713/00878/...) are deliberately
excluded — `sources/etf_pcf.py`'s own module docstring discloses that per-constituent
holdings/weights for TW ETFs are a genuine, unfilled adapter gap (only fund-level metadata via
`get_etf_fund_info` exists for TW); building a real TW overlap-checker would first require a new
TWSE issuer-PCF/holdings adapter, out of scope here. This tool never approximates that gap —
a TW symbol in the input is simply rejected by `NonTwSymbolInput`'s own validator, the same as
every other non-TW-only tool in this registry.

**Overlap-percentage definition chosen**: for each pair of requested ETFs, sum
`min(weight_in_a, weight_in_b)` over every holding both hold — the standard "portfolio overlap"
convention (the fraction of each fund's basket that's identically exposed to the other's), also
used by most public ETF-overlap tools. This is the smaller of the two exposures per shared
holding, so the resulting percentage never exceeds either fund's own 100% weight allocation.

**3+ minimum enforced in the schema** (`Field(min_length=3)` on the symbol list), not just
documented — this is the explicit fix for the "only pairwise" complaint, so the tool can't
silently be called with 2.

**Never a full-market scan**: the input is N explicit, caller-named symbols (capped, like every
other multi-symbol tool in this registry, well below a whole-market query) — never a market-wide
query, and `dispatch_tool`'s `pre_tool` full-market-scan guard applies the same as any other tool.

**Honest degradation**: a symbol whose `FUND_HOLDINGS` fetch comes back unhealthy/empty (not a
fund, no holdings data) is named in `unavailable_symbols` with its `health_status`, never
silently dropped from the request — it simply contributes no rows to the overlap computation.

## `get_fund_comparison` (PTT gap: 0050 vs 00713 vs 00878 報酬率比較, 股利再投入)

Total-return-with-dividends-reinvested comparison across 1-10 symbols, **any market** — unlike
the overlap-checker, this is legitimately buildable for TW too, because it only needs
`Capability.PRICE_EOD` (real for TW) + `Capability.DIVIDENDS` (real for TW, wraps the same read
`market_tools.GetDividendScheduleTool` exposes as `get_dividend_schedule`) — no per-constituent
holdings/NAV involved. `lookback_days` is bounded to `_MAX_LOOKBACK_DAYS` (~55yr) rather than the
original 10yr cap, since a single-symbol call (e.g. the ETF detail page's own "年化報酬(成立以來)"
stat) needs to span all the way back to a fund's real `inception_date`, not just a recent window.

**Dividend-reinvestment convention chosen** (stated explicitly, not invented silently): on each
dividend event whose `cash_ex_dividend_date` falls within the lookback window, the cash dividend
per share is treated as reinvested at THAT SAME DATE's closing price (buying
`cash_dividend / close_on_ex_date` additional fractional shares) — a standard, defensible
approximation of dividend-reinvestment total return, not an account-level simulation of actual
reinvestment timing (which in practice happens some days after the ex-date, at whatever price
prevails then) or of any fees/taxes. This is stated in `GetFundComparisonOutput`'s own docstring
so a caller never mistakes it for an exact accounting figure.

**Non-TW basic-profile field**: sector weightings from the same `FUND_HOLDINGS` capability
`get_fund_holdings`/`get_etf_overlap` use.
**TW basic-profile field**: fund-level metadata (issuer/tracked index) from the same
`Capability.ETF_HOLDINGS` capability `get_etf_fund_info` wraps — reused, not duplicated.

Per the same TW-holdings-gap rule as `get_etf_overlap`: a TW symbol here never gets an
overlap%/premium-discount/NAV figure (those need the still-unfilled TW holdings/NAV adapter) —
`FundComparisonRow.unavailable_note` says so explicitly for any TW row rather than omitting the
field with no explanation or fabricating a placeholder.
"""

from __future__ import annotations

from datetime import date as date_
from datetime import timedelta
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dataaccess.router import DataAccessRouter
from schemas.capabilities import (
    DividendEvent,
    EODBar,
    EtfFundInfo,
    FundHoldingRow,
    FundSectorWeightingRow,
)
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from schemas.source import HealthStatus
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import SymbolInput

# The untyped payload-list edge every `cast(...)` below narrows from — same PEP-695 local alias
# convention as `sources/etf_pcf.py`/`sources/news.py` use for their own `SourceResult.payload`
# unwrapping, so `cast(_UntypedList, ...)` isn't a repeated string literal at every call site.
type _UntypedList = list[object]

# Mirrors `tools/analyst_tools.py`'s own `_NON_TW_MARKETS`/`_validate_non_tw` (the yfinance
# non-TW-primary capability set `FUND_HOLDINGS` is served for) — duplicated rather than
# importing that module's private names across a tool-group boundary; both sets are pinned to
# the same `sources/yfinance/_shared.py::_NON_TW_SUPPORTED` fact, so they can't silently drift.
_NON_TW_MARKETS = frozenset({Market.US, Market.GB, Market.JP, Market.EU})


def _validate_non_tw(market: Market) -> Market:
    if market not in _NON_TW_MARKETS:
        raise ValueError(
            f"{market.value} is not served by yfinance's non-TW capability set (US/GB/JP/EU only)"
        )
    return market


_MIN_OVERLAP_SYMBOLS = 3
_MAX_OVERLAP_SYMBOLS = 10  # bounded fan-out — never a full-market scan
_MIN_COMPARISON_SYMBOLS = 1
_MAX_COMPARISON_SYMBOLS = 10
_DEFAULT_LOOKBACK_DAYS = 365
_MAX_LOOKBACK_DAYS = 20000  # ~55yr headroom — a long-listed TW ETF's own inception-to-today span

_TW_HOLDINGS_GAP_NOTE = (
    "Per-constituent holdings/weights and NAV/IOPV are a known, unfilled gap for TW ETFs — "
    "overlap% and premium/discount are never estimated for this symbol; only fund-level "
    "metadata (issuer/tracked index) is available."
)


class GetEtfOverlapInput(BaseModel):
    """3+ non-TW ETF/fund symbols, same market for every symbol — a genuine minimum, enforced
    here rather than merely documented."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbols: tuple[str, ...] = Field(
        min_length=_MIN_OVERLAP_SYMBOLS,
        max_length=_MAX_OVERLAP_SYMBOLS,
        description="3 or more ETF/fund symbols to compare (the explicit fix for 'existing "
        "tools only compare 2 funds at a time').",
    )
    market: Market

    @field_validator("market")
    @classmethod
    def _check_market(cls, value: Market) -> Market:
        return _validate_non_tw(value)

    @field_validator("symbols")
    @classmethod
    def _check_distinct(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError(
                "symbols must be distinct — a duplicate can't be meaningfully overlapped"
            )
        return value

    def to_symbols(self) -> tuple[Symbol, ...]:
        return tuple(Symbol(id=s, market=self.market) for s in self.symbols)


class OverlapHoldingRow(BaseModel):
    """One holding present in 2+ of the requested ETFs — the row shape the "which ETFs hold
    this, at what weight" view needs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    holding_symbol: str
    holding_name: str
    held_by: tuple[str, ...]  # requested ETF symbols that hold this, in request order
    weight_by_etf: tuple[float, ...]  # each ETF's own weight in this holding, aligned to held_by


class PairOverlapRow(BaseModel):
    """One (etf_a, etf_b) pair's overlap percentage — sum of `min(weight_in_a, weight_in_b)`
    over every holding both hold (see module docstring for why this convention was chosen)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    etf_a: str
    etf_b: str
    overlap_pct: float = Field(ge=0.0)
    shared_holding_count: int = Field(ge=0)


class GetEtfOverlapOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    requested_symbols: tuple[str, ...]
    unavailable_symbols: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Requested symbols whose FUND_HOLDINGS fetch degraded (no usable holdings "
        "data) — named honestly here, never silently dropped from the request.",
    )
    overlapping_holdings: tuple[OverlapHoldingRow, ...] = Field(default_factory=tuple)
    pairwise_overlap: tuple[PairOverlapRow, ...] = Field(default_factory=tuple)


class GetEtfOverlapTool(BaseTool):
    """`get_etf_overlap` — see module docstring for the overlap-percentage definition, the
    non-TW scope decision, and the honest-degradation contract."""

    name = "get_etf_overlap"
    description = (
        "Compare 3 or more non-TW ETFs/funds for constituent overlap: which requested ETFs "
        "hold each shared holding (with each ETF's own weight), plus a pairwise overlap-"
        "percentage summary (sum of min-weight-in-both per shared holding). Not available for "
        "TW ETFs — see sources/etf_pcf.py's documented per-constituent-holdings gap."
    )
    input_model: type[BaseModel] = GetEtfOverlapInput
    output_model: type[BaseModel] = GetEtfOverlapOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "etf_overlap"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetEtfOverlapInput, args)

        # weights[etf_symbol] = {holding_symbol: (weight, holding_name)}
        weights: dict[str, dict[str, tuple[float, str]]] = {}
        unavailable: list[str] = []
        for symbol in typed.to_symbols():
            result = await self._router.fetch(
                Capability.FUND_HOLDINGS, symbol, tls_policy=ctx.tls_policy
            )
            raw_holdings = result.payload.get("holdings")
            if result.health.status != HealthStatus.OK or not isinstance(raw_holdings, list):
                unavailable.append(symbol.id)
                continue
            rows = [FundHoldingRow.model_validate(row) for row in cast(_UntypedList, raw_holdings)]
            if not rows:
                unavailable.append(symbol.id)
                continue
            weights[symbol.id] = {
                row.holding_symbol: (row.weight, row.holding_name) for row in rows
            }

        available_symbols = [s for s in typed.symbols if s in weights]

        # Every distinct holding symbol seen across the fetched (available) ETFs.
        all_holdings: dict[str, str] = {}  # holding_symbol -> holding_name
        for per_etf in weights.values():
            for holding_symbol, (_, name) in per_etf.items():
                all_holdings.setdefault(holding_symbol, name)

        overlapping_holdings: list[OverlapHoldingRow] = []
        for holding_symbol, holding_name in all_holdings.items():
            held_by = [s for s in available_symbols if holding_symbol in weights[s]]
            if len(held_by) < 2:  # noqa: PLR2004 — the "held by 2+" threshold this tool exists to prove
                continue
            weight_by_etf = tuple(weights[s][holding_symbol][0] for s in held_by)
            overlapping_holdings.append(
                OverlapHoldingRow(
                    holding_symbol=holding_symbol,
                    holding_name=holding_name,
                    held_by=tuple(held_by),
                    weight_by_etf=weight_by_etf,
                )
            )

        pairwise: list[PairOverlapRow] = []
        for i, etf_a in enumerate(available_symbols):
            for etf_b in available_symbols[i + 1 :]:
                shared = set(weights[etf_a]) & set(weights[etf_b])
                overlap_pct = sum(min(weights[etf_a][h][0], weights[etf_b][h][0]) for h in shared)
                pairwise.append(
                    PairOverlapRow(
                        etf_a=etf_a,
                        etf_b=etf_b,
                        overlap_pct=overlap_pct,
                        shared_holding_count=len(shared),
                    )
                )

        return GetEtfOverlapOutput(
            market=typed.market.value,
            requested_symbols=typed.symbols,
            unavailable_symbols=tuple(unavailable),
            overlapping_holdings=tuple(overlapping_holdings),
            pairwise_overlap=tuple(pairwise),
        )


class GetFundComparisonInput(BaseModel):
    """1-10 symbols, any market (TW allowed) — each symbol carries its own market, since this
    tool's whole point is comparing across markets (e.g. a TW ETF vs a US ETF), unlike the
    overlap-checker which is single-market by construction (holdings-weight comparison only
    makes sense within one non-TW capability). A single symbol is a legitimate call too — the
    exact same dividend-reinvested total-return computation, just with nothing to compare it
    against (e.g. a fund's own "since inception" return, which the ETF detail page's own 總覽
    tab uses this tool for with `lookback_days` set to span the fund's real inception date)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbols: tuple[SymbolInput, ...] = Field(
        min_length=_MIN_COMPARISON_SYMBOLS, max_length=_MAX_COMPARISON_SYMBOLS
    )
    lookback_days: int = Field(
        default=_DEFAULT_LOOKBACK_DAYS,
        ge=1,
        le=_MAX_LOOKBACK_DAYS,
        description="Total-return lookback window in calendar days, ending today.",
    )

    @field_validator("symbols")
    @classmethod
    def _check_distinct(cls, value: tuple[SymbolInput, ...]) -> tuple[SymbolInput, ...]:
        keys = [(s.id, s.market) for s in value]
        if len(set(keys)) != len(keys):
            raise ValueError("symbols must be distinct (id, market) pairs")
        return value

    def window(self) -> tuple[date_, date_]:
        end = date_.today()
        return end - timedelta(days=self.lookback_days), end


class FundComparisonRow(BaseModel):
    """One requested symbol's total-return + basic-profile slice.

    `total_return_pct` is computed with dividends reinvested at the ex-dividend date's close
    (see module docstring for the exact convention/approximation) — `None` only when price
    history itself is unusable (never fabricated).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    window_start: date_ | None = None
    window_end: date_ | None = None
    total_return_pct: float | None = None
    price_unavailable_reason: str | None = None
    sector_weightings: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Non-TW only: 'sector (weight%)' strings from FUND_HOLDINGS.",
    )
    fund_info: EtfFundInfo | None = Field(
        default=None, description="TW only: issuer/tracked-index fund-level metadata."
    )
    unavailable_note: str | None = Field(
        default=None,
        description="Honest reason a field (overlap%/premium-discount/NAV) is absent for this "
        "row, e.g. the TW-holdings-gap note — never a silently-missing field.",
    )


class GetFundComparisonOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: tuple[FundComparisonRow, ...] = Field(default_factory=tuple)
    methodology_note: str = Field(
        default=(
            "Total return assumes each cash dividend is reinvested at that ex-dividend date's "
            "closing price — a standard approximation, not an account-level simulation of "
            "actual reinvestment timing or fees."
        )
    )


def _total_return_with_dividends_reinvested(
    bars: list[EODBar], events: list[DividendEvent], window_start: date_, window_end: date_
) -> float | None:
    """Shares-held simulation: start with 1 share at the first bar on/after `window_start`;
    on each ex-dividend date within the window that has both a cash amount and a matching
    closing price, buy `cash_dividend / close` more (fractional) shares; the total return is
    the ending share count times the last close, vs the starting single share's cost."""
    in_window = sorted(
        (b for b in bars if window_start <= b.trade_date <= window_end), key=lambda b: b.trade_date
    )
    if len(in_window) < 2:  # noqa: PLR2004 — need at least a start and end bar
        return None
    close_by_date = {b.trade_date: b.close for b in in_window}
    start_close = in_window[0].close
    if start_close <= 0:
        return None
    shares = 1.0
    for event in sorted(events, key=lambda e: e.cash_ex_dividend_date or e.announced_date):
        ex_date = event.cash_ex_dividend_date
        if ex_date is None or not (window_start <= ex_date <= window_end):
            continue
        close_on_ex_date = close_by_date.get(ex_date)
        if event.cash_dividend is None or not close_on_ex_date or close_on_ex_date <= 0:
            continue
        shares += (event.cash_dividend * shares) / close_on_ex_date
    end_close = in_window[-1].close
    ending_value = shares * end_close
    return ((ending_value - start_close) / start_close) * 100.0


def _sector_weighting_strings(payload: dict[str, object]) -> tuple[str, ...]:
    raw = payload.get("sector_weightings")
    if not isinstance(raw, list):
        return ()
    rows = [FundSectorWeightingRow.model_validate(r) for r in cast(_UntypedList, raw)]
    return tuple(f"{row.sector} ({row.weight * 100:.1f}%)" for row in rows)


async def _fund_comparison_return_fields(
    router: DataAccessRouter,
    symbol: Symbol,
    *,
    ctx: ToolContext,
    window_start: date_,
    window_end: date_,
) -> tuple[float | None, str | None]:
    """`(total_return_pct, price_unavailable_reason)` — pulled out of
    `GetFundComparisonTool.run` purely to keep that method's own branching shallow."""
    price_result = await router.fetch(
        Capability.PRICE_EOD,
        symbol,
        tls_policy=ctx.tls_policy,
        start_date=window_start.isoformat(),
        end_date=window_end.isoformat(),
    )
    raw_bars = price_result.payload.get("bars")
    bars = (
        [EODBar.model_validate(b) for b in cast(_UntypedList, raw_bars)]
        if isinstance(raw_bars, list)
        else []
    )
    if not bars:
        return None, (
            f"No usable price history: {price_result.health.status.value} "
            f"({price_result.currentness_caveat})"
        )

    dividend_result = await router.fetch(
        Capability.DIVIDENDS,
        symbol,
        tls_policy=ctx.tls_policy,
        start_date=window_start.isoformat(),
        end_date=window_end.isoformat(),
    )
    raw_events = dividend_result.payload.get("events")
    events = (
        [DividendEvent.model_validate(e) for e in cast(_UntypedList, raw_events)]
        if isinstance(raw_events, list)
        else []
    )
    total_return = _total_return_with_dividends_reinvested(bars, events, window_start, window_end)
    if total_return is None:
        return None, (
            "Fewer than two price bars fell inside the requested window — total return cannot "
            "be computed."
        )
    return total_return, None


async def _fund_comparison_profile_fields(
    router: DataAccessRouter, symbol: Symbol, *, ctx: ToolContext
) -> tuple[tuple[str, ...], EtfFundInfo | None, str | None]:
    """`(sector_weightings, fund_info, unavailable_note)` — pulled out of
    `GetFundComparisonTool.run` for the same shallow-branching reason as the return fields."""
    if symbol.market is Market.TW:
        fund_result = await router.fetch(Capability.ETF_HOLDINGS, symbol, tls_policy=ctx.tls_policy)
        raw_info = fund_result.payload.get("info")
        fund_info = EtfFundInfo.model_validate(raw_info) if isinstance(raw_info, dict) else None
        return (), fund_info, _TW_HOLDINGS_GAP_NOTE
    holdings_result = await router.fetch(
        Capability.FUND_HOLDINGS, symbol, tls_policy=ctx.tls_policy
    )
    return _sector_weighting_strings(holdings_result.payload), None, None


async def _fund_comparison_row(
    router: DataAccessRouter,
    symbol_input: SymbolInput,
    *,
    ctx: ToolContext,
    window_start: date_,
    window_end: date_,
) -> FundComparisonRow:
    symbol = symbol_input.to_symbol()
    total_return, price_unavailable_reason = await _fund_comparison_return_fields(
        router, symbol, ctx=ctx, window_start=window_start, window_end=window_end
    )
    sector_weightings, fund_info, unavailable_note = await _fund_comparison_profile_fields(
        router, symbol, ctx=ctx
    )
    return FundComparisonRow(
        symbol=symbol.id,
        market=symbol.market.value,
        window_start=window_start,
        window_end=window_end,
        total_return_pct=total_return,
        price_unavailable_reason=price_unavailable_reason,
        sector_weightings=sector_weightings,
        fund_info=fund_info,
        unavailable_note=unavailable_note,
    )


class GetFundComparisonTool(BaseTool):
    """`get_fund_comparison` — see module docstring for the dividend-reinvestment convention and
    the TW-scope handling."""

    name = "get_fund_comparison"
    description = (
        "Get one or more stock/ETF symbols' (any market, incl. TW) total return over a lookback "
        "window with dividends reinvested at the ex-dividend close, plus basic profile fields "
        "(non-TW: sector weightings; TW: issuer/tracked-index). A single symbol over a "
        "multi-decade window is a legitimate call (e.g. a fund's own since-inception return). "
        "Never computes overlap%/premium-discount/NAV for TW symbols (documented adapter gap) — "
        "omitted with an honest note, not fabricated."
    )
    input_model: type[BaseModel] = GetFundComparisonInput
    output_model: type[BaseModel] = GetFundComparisonOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "fund_comparison"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetFundComparisonInput, args)
        window_start, window_end = typed.window()
        rows = [
            await _fund_comparison_row(
                self._router,
                symbol_input,
                ctx=ctx,
                window_start=window_start,
                window_end=window_end,
            )
            for symbol_input in typed.symbols
        ]
        return GetFundComparisonOutput(rows=tuple(rows))


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [
        (GetEtfOverlapTool(router), False),
        (GetFundComparisonTool(router), False),
    ]


__all__ = [
    "FundComparisonRow",
    "GetEtfOverlapInput",
    "GetEtfOverlapOutput",
    "GetEtfOverlapTool",
    "GetFundComparisonInput",
    "GetFundComparisonOutput",
    "GetFundComparisonTool",
    "OverlapHoldingRow",
    "PairOverlapRow",
    "build_registration_table",
]
