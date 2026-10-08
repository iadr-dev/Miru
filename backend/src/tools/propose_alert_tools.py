"""`propose_alert` — the agent's way to *suggest* an alert rule in chat ("外資連買 3 天就提醒我").

Propose-only, by design (governance §9a bound 1: a rule exists solely because a signed-in user
authorised it). This tool performs no I/O and creates nothing: it validates the rule the model
assembled from the user's words and echoes it back as a typed `alert_proposal` card
(`agent.loop._card_for` → the existing `rich_card` SSE path, same mechanism as `ask_user`). The
user then taps 建立 on the card, and the *frontend* calls the ordinary `POST /alerts` — which
enforces the auth, the fair-use caps, and the market/watchlist checks exactly as any other
creation path. So the confirm gate is the card itself, not a flag the model could skip, and there
is no agent-side write for a prompt-injected page or a confused model to abuse.

Validation lives in the input model on purpose: a proposal the backend would reject (a Taiwan-only
template on a US symbol, an out-of-range threshold) is returned to the model as a tool-argument
error it can correct, instead of reaching the user as a broken card they can only fail to create.
"""

from __future__ import annotations

from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.alerts import (
    ConditionModel,
    RuleCondition,
    TriggerMode,
    describe,
    supports_market,
)
from schemas.enums import DataClass, Market
from tools.base import BaseTool, Tool, ToolContext, parse_args

# The proposal's lifetime is fixed, not a model-chosen argument: a live model once sent `null`
# ("never expires") on its own and created a rule nobody asked to be permanent. The user can change
# it in Settings after confirming the card.
_DEFAULT_LIFETIME_DAYS: Final = 90
_PROPOSAL_MARKETS: Final = frozenset({Market.TW, Market.US})


class ProposeAlertInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, max_length=32, description="Stock/ETF code, e.g. 2330.")
    market: Market = Field(description="TW or US.")
    condition: RuleCondition = Field(
        description=(
            "What to watch, as `{kind, ...}`. Available for TW and US: close_price "
            "(comparator above|below, value), daily_change (direction up|down, percent), "
            "volume_surge (multiple, optional window). Taiwan only: institutional_streak "
            "(investor foreign|trust|dealer, direction buy|sell, days 2-20), attention_entry, "
            "disposition_entry, month_revenue_published, material_announcement (a 重大訊息 was "
            "filed; optional keyword the subject must contain, max 20 chars, e.g. 股利), "
            "margin_balance_change (side margin|short, direction up|down, percent) and "
            "etf_premium_discount (direction premium|discount, percent; ETF codes only)."
        )
    )

    @field_validator("symbol")
    @classmethod
    def _normalise_symbol(cls, value: str) -> str:
        # Same normalisation as `POST /alerts`, so the card shows exactly what will be created.
        return value.strip().upper()

    @model_validator(mode="after")
    def _supported(self) -> Self:
        if self.market not in _PROPOSAL_MARKETS:
            raise ValueError("alerts support the TW and US markets only")
        if not supports_market(self.condition, self.market):
            raise ValueError(f"'{self.condition.kind}' alerts exist for the TW market only")
        return self


class ProposeAlertOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    condition: RuleCondition
    trigger_mode: TriggerMode
    expires_in_days: int | None
    # Fact-phrased one-liner, for the model to quote back (the card renders its own, localised).
    summary: str
    # Always true: nothing has been created. The user must confirm on the card.
    requires_user_confirmation: Literal[True] = True
    # Read by the model as part of the tool result. Some models (seen: one Grok) re-propose in a
    # loop for minutes unless the result itself says the job is done.
    next_step: str = (
        "DONE: the confirmation card is now on the user's screen. Do NOT call propose_alert "
        "again this turn. Reply with one short sentence and stop."
    )


class ProposeAlertTool(BaseTool):
    """`propose_alert` — propose (never create) an alert rule for the user to confirm."""

    name = "propose_alert"
    description = (
        "Propose an alert rule for one Taiwan or US stock/ETF when the user asks to be notified "
        "about a data condition (e.g. 'alert me when 2330 closes above 600', "
        "'外資連買 3 天提醒我'). "
        "This ONLY shows the user a confirmation card — it creates nothing; the alert exists only "
        "after the user taps 建立 on that card. Call it once per rule, then tell the user briefly "
        "that the proposal is on screen for them to confirm; never say the alert was created or "
        "set. Use ask_user first if the symbol or threshold is ambiguous. Alerts report when a "
        "data condition the user chose is met — never frame the condition as a trade signal."
    )
    input_model: type[BaseModel] = ProposeAlertInput
    output_model: type[BaseModel] = ProposeAlertOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "alert_proposal"

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no I/O, no identity needed — the signed-in user creates the rule from the card
        typed = parse_args(ProposeAlertInput, args)
        condition: ConditionModel = typed.condition
        return ProposeAlertOutput(
            symbol=typed.symbol,
            market=typed.market,
            condition=condition,
            # Not a model-chosen argument (a model once set 'once' unprompted); the user can change
            # the frequency when editing the rule after creating it.
            trigger_mode=TriggerMode.ONCE_PER_DAY,
            expires_in_days=_DEFAULT_LIFETIME_DAYS,
            summary=f"{typed.symbol} {describe(condition)}",
        )


def build_registration_table() -> list[tuple[Tool, bool]]:
    return [(ProposeAlertTool(), False)]


__all__ = [
    "ProposeAlertInput",
    "ProposeAlertOutput",
    "ProposeAlertTool",
    "build_registration_table",
]
