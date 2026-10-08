"""`propose_watchlist_change` - the agent's way to *suggest* a change to the user's 自選股: add
one or more stocks/ETFs (optionally into a named group, created if it does not exist yet) or remove
them.

Propose-only, exactly like `propose_alert` and `propose_memory`: this tool performs no I/O and
changes nothing. It echoes the proposal as a typed `watchlist_proposal` card; the user checks the
names, picks the group and taps the button, and the *frontend* calls the ordinary watchlist routes
(auth and group ownership are enforced there; adds are idempotent, removes are idempotent). The
confirm gate is the card, so the agent has no write path to the watchlist.
"""

from __future__ import annotations

import re
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.enums import DataClass, Market
from tools.base import BaseTool, Tool, ToolContext, parse_args

_SYMBOL_PATTERN: Final = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,15}$")
_PROPOSAL_MARKETS: Final = frozenset({Market.TW, Market.US})
MAX_SYMBOLS: Final = 10
MAX_GROUP_NAME: Final = 30
_GROUP_FORBIDDEN: Final = "<>[]()`*_#\\|\"'"


class ProposeWatchlistChangeInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["add", "remove"] = Field(
        description="add = put the symbols in the watchlist; remove = take them out."
    )
    market: Market = Field(description="TW or US. All symbols must be in this market.")
    symbols: list[str] = Field(
        min_length=1,
        max_length=MAX_SYMBOLS,
        description=(
            "Ticker codes, e.g. ['2409', '3481']. Look a company NAME up first "
            "(get_symbol_directory_search) - never guess a code from a name."
        ),
    )
    group_name: str | None = Field(
        default=None,
        max_length=MAX_GROUP_NAME,
        description=(
            "add only: put the symbols in the user's group with this name, creating it if it does "
            "not exist. Leave empty to let the user pick on the card."
        ),
    )

    @field_validator("symbols")
    @classmethod
    def _normalise_symbols(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            symbol = value.strip().upper()
            if not _SYMBOL_PATTERN.match(symbol):
                raise ValueError(f"{value!r} is not a plain ticker code such as 2330 or AAPL")
            if symbol not in cleaned:
                cleaned.append(symbol)
        return cleaned

    @field_validator("group_name")
    @classmethod
    def _clean_group_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            return None
        if not cleaned.isprintable() or any(ch in cleaned for ch in _GROUP_FORBIDDEN):
            raise ValueError("group_name may not contain markup or control characters")
        return cleaned

    @field_validator("market")
    @classmethod
    def _supported(cls, value: Market) -> Market:
        if value not in _PROPOSAL_MARKETS:
            raise ValueError("watchlists support the TW and US markets only")
        return value

    @model_validator(mode="after")
    def _group_only_for_add(self) -> Self:
        if self.action == "remove" and self.group_name is not None:
            raise ValueError("group_name applies to 'add' only")
        return self


class ProposeWatchlistChangeOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["add", "remove"]
    market: Market
    symbols: list[str]
    group_name: str | None
    # Always true: nothing has changed. The user must confirm on the card.
    requires_user_confirmation: Literal[True] = True
    next_step: str = (
        "DONE: the confirmation card is now on the user's screen. Do NOT call "
        "propose_watchlist_change again this turn. Reply with one short sentence and stop."
    )


class ProposeWatchlistChangeTool(BaseTool):
    """`propose_watchlist_change` - propose (never perform) adding/removing 自選股 entries."""

    name = "propose_watchlist_change"
    description = (
        "Propose adding or removing Taiwan/US stocks or ETFs in the user's watchlist (自選股) when "
        "they ask (e.g. '把友達和群創加到自選股', '從自選股移除 2330', 'add AAPL to my tech "
        "group'). This ONLY shows a confirmation card where the user checks the names, picks the "
        "group and taps the button - nothing changes until they do; never say it was added or "
        "removed. Resolve company names to codes with get_symbol_directory_search first (never "
        "guess a code), pass all symbols of one request in a single call, then say briefly that "
        "the proposal is on screen. Use ask_user if a name is ambiguous. A watchlist entry is not "
        "a recommendation."
    )
    input_model: type[BaseModel] = ProposeWatchlistChangeInput
    output_model: type[BaseModel] = ProposeWatchlistChangeOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "watchlist_proposal"

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no I/O, no identity needed - the signed-in user applies it from the card
        typed = parse_args(ProposeWatchlistChangeInput, args)
        return ProposeWatchlistChangeOutput(
            action=typed.action,
            market=typed.market,
            symbols=typed.symbols,
            group_name=typed.group_name,
        )


def build_registration_table() -> list[tuple[Tool, bool]]:
    return [(ProposeWatchlistChangeTool(), False)]


__all__ = [
    "MAX_GROUP_NAME",
    "MAX_SYMBOLS",
    "ProposeWatchlistChangeInput",
    "ProposeWatchlistChangeOutput",
    "ProposeWatchlistChangeTool",
    "build_registration_table",
]
