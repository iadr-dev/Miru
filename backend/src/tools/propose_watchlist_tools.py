"""`propose_watchlist_change` - the agent's way to *suggest* a change to the user's 自選股: add
stocks/ETFs (optionally into a named group, created if it does not exist yet), remove them, move
them to another group, or manage a group itself (rename, delete, put first/last).

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

from schemas.enums import DataClass, UserMarket
from tools.base import BaseTool, Tool, ToolContext, parse_args

_SYMBOL_PATTERN: Final = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,15}$")
MAX_SYMBOLS: Final = 10
MAX_GROUP_NAME: Final = 30
Action = Literal["add", "remove", "move", "rename_group", "delete_group", "reorder_group"]
_SYMBOL_ACTIONS: Final = frozenset({"add", "remove", "move"})
_GROUP_FORBIDDEN: Final = "<>[]()`*_#\\|\"'"


class ProposeWatchlistChangeInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Action = Field(
        description=(
            "add = put symbols in the watchlist; remove = take them out; move = move symbols to "
            "another existing group; rename_group / delete_group / reorder_group = manage the "
            "group named in group_name."
        )
    )
    market: UserMarket = Field(description="TW or US. All symbols and groups are in this market.")
    symbols: list[str] = Field(
        default_factory=list,
        max_length=MAX_SYMBOLS,
        description=(
            "add/remove/move only: ticker codes, e.g. ['2409', '3481']. Look a company NAME up "
            "first (get_symbol_directory_search) - never guess a code from a name."
        ),
    )
    group_name: str | None = Field(
        default=None,
        max_length=MAX_GROUP_NAME,
        description=(
            "add: put the symbols in the user's group with this name, creating it if it does not "
            "exist (empty = the user picks on the card). move: the existing TARGET group "
            "(required). rename_group / delete_group / reorder_group: the group to act on "
            "(required)."
        ),
    )
    new_name: str | None = Field(
        default=None, max_length=MAX_GROUP_NAME, description="rename_group only: the new name."
    )
    position: Literal["first", "last"] | None = Field(
        default=None, description="reorder_group only: put the group first or last."
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

    @field_validator("group_name", "new_name")
    @classmethod
    def _clean_group_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            return None
        if not cleaned.isprintable() or any(ch in cleaned for ch in _GROUP_FORBIDDEN):
            raise ValueError("a group name may not contain markup or control characters")
        return cleaned

    @model_validator(mode="after")
    def _fields_match_the_action(self) -> Self:
        needs_symbols = self.action in _SYMBOL_ACTIONS
        if needs_symbols and not self.symbols:
            raise ValueError(f"'{self.action}' needs at least one symbol")
        if not needs_symbols and self.symbols:
            raise ValueError(f"'{self.action}' acts on a group, not symbols")
        group_actions = {"move", "rename_group", "delete_group", "reorder_group"}
        if self.action in group_actions and self.group_name is None:
            raise ValueError(f"'{self.action}' needs group_name")
        if self.action == "remove" and self.group_name is not None:
            raise ValueError("group_name does not apply to 'remove'")
        if (self.action == "rename_group") != (self.new_name is not None):
            raise ValueError("new_name is required for, and only for, 'rename_group'")
        if (self.action == "reorder_group") != (self.position is not None):
            raise ValueError("position is required for, and only for, 'reorder_group'")
        return self


class ProposeWatchlistChangeOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Action
    market: UserMarket
    symbols: list[str]
    group_name: str | None
    new_name: str | None
    position: Literal["first", "last"] | None
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
        "Propose a change to the user's watchlist (自選股) when they ask: add / remove / move "
        "Taiwan or US stocks or ETFs (e.g. '把友達和群創加到自選股', '從自選股移除 2330', "
        "'把 2330 移到半導體'), or manage a group (rename_group, delete_group, reorder_group - "
        "e.g. '把「面板」改名成「顯示器」', '刪掉記憶體群組', '把半導體排到最前面'). This ONLY "
        "shows a confirmation card - nothing changes until the user taps the button; never say "
        "it was done. Resolve company names to codes with get_symbol_directory_search first "
        "(never guess a code), pass all symbols of one request in a single call, then say "
        "briefly that the proposal is on screen. Use ask_user if a name or group is ambiguous. "
        "A watchlist entry is not a recommendation."
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
            new_name=typed.new_name,
            position=typed.position,
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
