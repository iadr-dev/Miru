"""`propose_watchlist_add` - the agent's way to *suggest* adding a stock/ETF to the user's 自選股
("把 2330 加到自選股").

Propose-only, exactly like `propose_alert` and `propose_memory`: this tool performs no I/O and adds
nothing. It echoes the symbol as a typed `watchlist_proposal` card; the user picks a group and taps
加入, and the *frontend* calls the ordinary `PUT /watchlists/{id}/items/{symbol}` (auth and group
ownership are enforced there; a repeat add is an idempotent no-op). The confirm gate is the card, so
the agent has no write path to the watchlist.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.enums import DataClass, Market
from tools.base import BaseTool, Tool, ToolContext, parse_args

_SYMBOL_PATTERN: Final = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,15}$")
_PROPOSAL_MARKETS: Final = frozenset({Market.TW, Market.US})


class ProposeWatchlistAddInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, max_length=16, description="Stock/ETF code, e.g. 2330.")
    market: Market = Field(description="TW or US.")

    @field_validator("symbol")
    @classmethod
    def _normalise(cls, value: str) -> str:
        # Same normalisation as the watchlist routes, so the card shows exactly what will be added.
        cleaned = value.strip().upper()
        if not _SYMBOL_PATTERN.match(cleaned):
            raise ValueError("symbol must be a plain ticker code such as 2330 or AAPL")
        return cleaned

    @field_validator("market")
    @classmethod
    def _supported(cls, value: Market) -> Market:
        if value not in _PROPOSAL_MARKETS:
            raise ValueError("watchlists support the TW and US markets only")
        return value


class ProposeWatchlistAddOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    # Always true: nothing has been added. The user must pick a group and confirm on the card.
    requires_user_confirmation: Literal[True] = True
    next_step: str = (
        "DONE: the confirmation card is now on the user's screen. Do NOT call "
        "propose_watchlist_add again this turn. Reply with one short sentence and stop."
    )


class ProposeWatchlistAddTool(BaseTool):
    """`propose_watchlist_add` - propose (never perform) adding a symbol to the user's 自選股."""

    name = "propose_watchlist_add"
    description = (
        "Propose adding one Taiwan or US stock/ETF to the user's watchlist (自選股) when they ask "
        "for it (e.g. '把 2330 加到自選股', 'add AAPL to my watchlist'). This ONLY shows a "
        "confirmation card where the user picks the group - nothing is added until they tap 加入; "
        "never say it was added. Call it once per symbol, then say briefly that the proposal is on "
        "screen. Use ask_user first if the symbol is ambiguous. Do not frame a watchlist entry as "
        "a recommendation."
    )
    input_model: type[BaseModel] = ProposeWatchlistAddInput
    output_model: type[BaseModel] = ProposeWatchlistAddOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "watchlist_proposal"

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no I/O, no identity needed - the signed-in user adds it from the card
        typed = parse_args(ProposeWatchlistAddInput, args)
        return ProposeWatchlistAddOutput(symbol=typed.symbol, market=typed.market)


def build_registration_table() -> list[tuple[Tool, bool]]:
    return [(ProposeWatchlistAddTool(), False)]


__all__ = [
    "ProposeWatchlistAddInput",
    "ProposeWatchlistAddOutput",
    "ProposeWatchlistAddTool",
    "build_registration_table",
]
