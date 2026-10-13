"""Alert rule CRUD, batch create and the symbol check (`/alerts`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from accounts.models import User
from api.dependencies import (
    AppState,
    dispatch_validated_tool,
    get_state,
    require_user,
    tool_context_for,
)
from api.errors import ApiError
from api.routers.scheduler._common import HTTP_422_UNPROCESSABLE as _HTTP_422_UNPROCESSABLE
from api.routers.scheduler._common import cap_reached as _cap_reached
from api.routers.scheduler._common import not_found as _not_found
from api.routers.scheduler._common import store as _store
from scheduler.caps import MAX_ALERT_RULES
from scheduler.models import (
    AlertPatch,
    AlertRule,
    DeliveryChannelKind,
    QuietHours,
    TriggerMode,
)
from scheduler.store import (
    AlertRuleNotFoundError,
    SchedulerStore,
    StaleVersionError,
)
from schemas.alerts import PER_STOCK_KINDS, RuleCondition, supports_market
from schemas.enums import Market, UserMarket

router = APIRouter()


_DEFAULT_ALERT_LIFETIME_DAYS = 90


class CreateAlertRequest(BaseModel):
    # Scope: one symbol (+market) XOR one watchlist group.
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    market: UserMarket | None = None
    watchlist_id: str | None = None
    condition: RuleCondition
    trigger_mode: TriggerMode = TriggerMode.ONCE_PER_DAY
    cooldown_seconds: int | None = Field(default=None, ge=0)
    # Days until the rule auto-expires; null = never. Defaults to 90 so forgotten rules lapse.
    expires_in_days: int | None = Field(default=_DEFAULT_ALERT_LIFETIME_DAYS, ge=1, le=3650)
    explain: bool = False
    # Empty channels / null quiet_hours / null daily_limit inherit the user's alert preferences.
    channels: tuple[DeliveryChannelKind, ...] = Field(default_factory=tuple)
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = Field(default=None, ge=1)

    @field_validator("symbol")
    @classmethod
    def _normalise_symbol(cls, value: str | None) -> str | None:
        """Tickers are stored upper-case and trimmed, because the ingested partitions key on the
        exchange ticker ('AAPL', '00631L'): an alert saved as 'aapl' would never match a row."""
        return value.strip().upper() if value is not None else None

    @model_validator(mode="after")
    def _one_scope(self) -> CreateAlertRequest:
        has_symbol = self.symbol is not None and self.market is not None
        half = (self.symbol is None) != (self.market is None)
        if half or has_symbol == (self.watchlist_id is not None):
            raise ValueError("provide symbol+market or watchlist_id, not both and not neither")
        return self


class UpdateAlertRequest(BaseModel):
    """A partial edit. Send only what changed; `null` clears a nullable field (`expires_in_days`,
    `cooldown_seconds`, `quiet_hours`, `daily_limit`). `expected_version` is the rule's `version`
    as the client last saw it — a mismatch is a 409 so edits from two devices never silently
    overwrite each other. The rule's scope (symbol / market / watchlist) cannot be edited, and
    unknown fields are rejected (422) rather than silently ignored."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    condition: RuleCondition | None = None
    trigger_mode: TriggerMode | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    explain: bool | None = None
    channels: tuple[DeliveryChannelKind, ...] | None = None
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = Field(default=None, ge=1)

    def to_patch(self) -> AlertPatch:
        """Translate to the store's patch, preserving present-vs-omitted (so an explicit `null`
        clears). `expires_in_days` becomes an absolute `expires_at` measured from now."""
        given = self.model_fields_set - {"expected_version", "expires_in_days"}
        values: dict[str, object] = {name: getattr(self, name) for name in given}
        if "expires_in_days" in self.model_fields_set:
            values["expires_at"] = (
                datetime.now(UTC) + timedelta(days=self.expires_in_days)
                if self.expires_in_days is not None
                else None
            )
        return AlertPatch.model_validate(values)


@router.post("/alerts", status_code=status.HTTP_201_CREATED)
async def create_alert(
    body: CreateAlertRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> AlertRule:
    if await store.count_alerts(user_id=user.id) >= MAX_ALERT_RULES:
        raise _cap_reached("alert rules", MAX_ALERT_RULES)
    if body.market is not None and not supports_market(body.condition, body.market):
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="template_market_mismatch",
            message=f"'{body.condition.kind}' alerts exist for the TW market only.",
            retryable=False,
        )
    if body.watchlist_id is not None and body.condition.kind in PER_STOCK_KINDS:
        # One price level cannot mean the same thing for every stock in a group (2,475 vs 185), so
        # a price alert must be set per stock — see `POST /alerts/batch`.
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="template_scope_mismatch",
            message="Price-level alerts are set per stock, not for a whole watchlist group.",
            retryable=False,
        )
    if body.watchlist_id is not None and not await store.watchlist_owned_by(
        user_id=user.id, watchlist_id=body.watchlist_id
    ):
        raise _not_found("watchlist_not_found", f"Watchlist {body.watchlist_id} not found.")
    expires_at = (
        datetime.now(UTC) + timedelta(days=body.expires_in_days)
        if body.expires_in_days is not None
        else None
    )
    return await store.create_alert(
        user_id=user.id,
        symbol=body.symbol,
        market=body.market,
        watchlist_id=body.watchlist_id,
        condition=body.condition,
        trigger_mode=body.trigger_mode,
        cooldown_seconds=body.cooldown_seconds,
        expires_at=expires_at,
        explain=body.explain,
        channels=body.channels,
        quiet_hours=body.quiet_hours,
        daily_limit=body.daily_limit,
    )


_MAX_BATCH = 30


class BatchAlertItem(BaseModel):
    """One stock's own rule in a per-stock batch."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)
    market: UserMarket
    condition: RuleCondition

    @field_validator("symbol")
    @classmethod
    def _normalise_symbol(cls, value: str) -> str:
        return value.strip().upper()


class BatchCreateAlertsRequest(BaseModel):
    """Several per-stock rules created together (「逐檔設定」): each item has its own stock and
    its own level; frequency, lifetime and channels are shared."""

    model_config = ConfigDict(extra="forbid")

    items: tuple[BatchAlertItem, ...] = Field(min_length=1, max_length=_MAX_BATCH)
    origin_watchlist_id: str | None = None
    trigger_mode: TriggerMode = TriggerMode.ONCE_PER_DAY
    cooldown_seconds: int | None = Field(default=None, ge=0)
    expires_in_days: int | None = Field(default=_DEFAULT_ALERT_LIFETIME_DAYS, ge=1, le=3650)
    channels: tuple[DeliveryChannelKind, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _no_duplicate_stock(self) -> BatchCreateAlertsRequest:
        seen = {(i.symbol, i.market) for i in self.items}
        if len(seen) != len(self.items):
            raise ValueError("each stock may appear once in a batch")
        return self


class AlertsResponse(BaseModel):
    alerts: list[AlertRule]


@router.post("/alerts/batch", status_code=status.HTTP_201_CREATED)
async def create_alerts_batch(
    body: BatchCreateAlertsRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> AlertsResponse:
    """All-or-nothing: every item is validated and the fair-use cap is checked for the whole batch
    before anything is written, and the request runs in one transaction."""
    if await store.count_alerts(user_id=user.id) + len(body.items) > MAX_ALERT_RULES:
        raise _cap_reached("alert rules", MAX_ALERT_RULES)
    for item in body.items:
        if not supports_market(item.condition, item.market):
            raise ApiError(
                status_code=_HTTP_422_UNPROCESSABLE,
                code="template_market_mismatch",
                message=f"'{item.condition.kind}' alerts exist for the TW market only.",
                retryable=False,
            )
    if body.origin_watchlist_id is not None and not await store.watchlist_owned_by(
        user_id=user.id, watchlist_id=body.origin_watchlist_id
    ):
        raise _not_found("watchlist_not_found", f"Watchlist {body.origin_watchlist_id} not found.")
    expires_at = (
        datetime.now(UTC) + timedelta(days=body.expires_in_days)
        if body.expires_in_days is not None
        else None
    )
    created = [
        await store.create_alert(
            user_id=user.id,
            symbol=item.symbol,
            market=item.market,
            origin_watchlist_id=body.origin_watchlist_id,
            condition=item.condition,
            trigger_mode=body.trigger_mode,
            cooldown_seconds=body.cooldown_seconds,
            expires_at=expires_at,
            channels=body.channels,
        )
        for item in body.items
    ]
    return AlertsResponse(alerts=created)


@router.get("/alerts/symbol-check")
async def check_alert_symbol(
    symbol: str = Query(min_length=1, max_length=20),
    market: Market = Query(),
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
) -> dict[str, bool]:
    """Does this ticker have any price data? A pre-flight the alert form calls so a typo
    (`APPLE` for `AAPL`) is flagged *before* a rule is saved that could never fire. It only
    reports — creation itself is never blocked, since a brand-new listing may genuinely have no
    bar yet. `checked=False` means the lookup itself failed, which says nothing about the ticker."""
    try:
        result = await dispatch_validated_tool(
            state,
            "get_price_quote",
            {"id": symbol.strip().upper(), "market": market.value},
            tool_context_for(user, state),
        )
    except Exception:
        return {"checked": False, "exists": False}
    return {"checked": True, "exists": result.get("bar") is not None}


@router.get("/alerts")
async def list_alerts(
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> AlertsResponse:
    return AlertsResponse(alerts=await store.list_alerts(user_id=user.id))


@router.patch("/alerts/{alert_id}")
async def update_alert(
    alert_id: str,
    body: UpdateAlertRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> AlertRule:
    try:
        return await store.update_alert(
            user_id=user.id,
            alert_id=alert_id,
            patch=body.to_patch(),
            expected_version=body.expected_version,
        )
    except StaleVersionError as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="stale_version",
            message="This alert was changed elsewhere. Reload it and try again.",
            retryable=False,
        ) from exc
    except AlertRuleNotFoundError as exc:
        raise _not_found("alert_not_found", f"Alert {alert_id} not found.") from exc


@router.delete("/alerts/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert(
    alert_id: str,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> None:
    try:
        await store.delete_alert(user_id=user.id, alert_id=alert_id)
    except AlertRuleNotFoundError as exc:
        raise _not_found("alert_not_found", f"Alert {alert_id} not found.") from exc


__all__ = ["router"]
