"""Helpers shared by the `market_rest` route modules."""

from __future__ import annotations

from datetime import date as date_

from fastapi import Depends

from accounts.models import User
from api.dependencies import (
    AppState,
    get_state,
    optional_user,
    tool_context_for,
)
from schemas.enums import Market
from tools.base import ToolContext


def context(
    user: User | None = Depends(optional_user),
    state: AppState = Depends(get_state),
) -> ToolContext:
    return tool_context_for(user, state)


def market_args(
    market: Market, start_date: date_ | None, end_date: date_ | None
) -> dict[str, object]:
    args: dict[str, object] = {"market": market.value}
    if start_date is not None:
        args["start_date"] = start_date.isoformat()
    if end_date is not None:
        args["end_date"] = end_date.isoformat()
    return args
