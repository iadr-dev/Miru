"""Schedule CRUD (`/schedules`)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from accounts.models import User
from api.dependencies import (
    AppState,
    get_state,
    require_user,
)
from api.errors import ApiError
from api.routers.scheduler._common import HTTP_422_UNPROCESSABLE as _HTTP_422_UNPROCESSABLE
from api.routers.scheduler._common import cap_reached as _cap_reached
from api.routers.scheduler._common import not_found as _not_found
from api.routers.scheduler._common import store as _store
from scheduler.caps import MAX_SCHEDULES
from scheduler.models import (
    DeliveryChannelKind,
    QuietHours,
    ScheduleAction,
    ScheduleJob,
    SchedulePatch,
    ScheduleTrigger,
)
from scheduler.store import (
    ScheduleJobNotFoundError,
    SchedulerStore,
    StaleVersionError,
)
from scheduler.triggers import UnresolvableTriggerError, next_run_after
from scheduler.validation import (
    InvalidTriggerError,
    UnknownSkillReferenceError,
    validate_action,
    validate_trigger,
)
from schemas.schedule_templates import WATCHLIST_INPUT, WATCHLIST_SKILL

router = APIRouter()


def _stale(what_id: str) -> ApiError:
    del what_id
    return ApiError(
        status_code=status.HTTP_409_CONFLICT,
        code="stale_version",
        message="This schedule was changed elsewhere. Reload it and try again.",
        retryable=False,
    )


class CreateScheduleRequest(BaseModel):
    trigger: ScheduleTrigger
    action: ScheduleAction
    delivery_channels: tuple[DeliveryChannelKind, ...] = Field(default_factory=tuple)
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = Field(default=None, ge=1)


class UpdateScheduleRequest(BaseModel):
    """A partial edit. Only sent fields change; `null` clears `quiet_hours`/`daily_limit`.
    `expected_version` makes the edit refuse (409 `stale_version`) if another device saved first."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    trigger: ScheduleTrigger | None = None
    action: ScheduleAction | None = None
    delivery_channels: tuple[DeliveryChannelKind, ...] | None = None
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = Field(default=None, ge=1)
    expected_version: int | None = Field(default=None, ge=0)

    def to_patch(self) -> SchedulePatch:
        sent = self.model_fields_set - {"expected_version"}
        return SchedulePatch.model_validate({name: getattr(self, name) for name in sent})


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    body: CreateScheduleRequest,
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
) -> ScheduleJob:
    if await store.count_jobs(user_id=user.id) >= MAX_SCHEDULES:
        raise _cap_reached("schedules", MAX_SCHEDULES)
    await _check_watchlist_scope(body.action, user, store)
    try:
        validate_trigger(body.trigger)
        validate_action(body.action, state.skill_registry)
        next_run = next_run_after(body.trigger, datetime.now(UTC))
    except (InvalidTriggerError, UnresolvableTriggerError) as exc:
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="invalid_trigger",
            message=str(exc),
            retryable=False,
        ) from exc
    except UnknownSkillReferenceError as exc:
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="unknown_skill",
            message=str(exc),
            retryable=False,
        ) from exc
    return await store.create_job(
        user_id=user.id,
        trigger=body.trigger,
        action=body.action,
        delivery_channels=body.delivery_channels,
        quiet_hours=body.quiet_hours,
        daily_limit=body.daily_limit,
        next_run=next_run,
    )


class SchedulesResponse(BaseModel):
    schedules: list[ScheduleJob]


@router.get("/schedules")
async def list_schedules(
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> SchedulesResponse:
    return SchedulesResponse(schedules=await store.list_jobs(user_id=user.id))


@router.patch("/schedules/{job_id}")
async def update_schedule(
    job_id: str,
    body: UpdateScheduleRequest,
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
) -> ScheduleJob:
    patch = body.to_patch()
    if "action" in patch.model_fields_set and patch.action is not None:
        await _check_watchlist_scope(patch.action, user, store)
    try:
        current = await store.get_job(user_id=user.id, job_id=job_id)
        next_run = _resolved_next_run(patch, current, state)
        return await store.update_job(
            user_id=user.id,
            job_id=job_id,
            patch=patch,
            next_run=next_run,
            expected_version=body.expected_version,
        )
    except ScheduleJobNotFoundError as exc:
        raise _not_found("schedule_not_found", f"Schedule {job_id} not found.") from exc
    except StaleVersionError as exc:
        raise _stale(job_id) from exc
    except (InvalidTriggerError, UnresolvableTriggerError) as exc:
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="invalid_trigger",
            message=str(exc),
            retryable=False,
        ) from exc
    except UnknownSkillReferenceError as exc:
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="unknown_skill",
            message=str(exc),
            retryable=False,
        ) from exc


async def _check_watchlist_scope(action: ScheduleAction, user: User, store: SchedulerStore) -> None:
    """A watchlist-scoped schedule must point at one of the caller's own lists, and only the
    multi-stock briefing can expand a list."""
    watchlist_id = action.inputs.get(WATCHLIST_INPUT)
    if watchlist_id is None:
        return
    if action.skill_ref != WATCHLIST_SKILL:
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="watchlist_scope_unsupported",
            message="Only the pre-open briefing can follow a watchlist.",
            retryable=False,
        )
    if not isinstance(watchlist_id, str) or not await store.watchlist_owned_by(
        user_id=user.id, watchlist_id=watchlist_id
    ):
        raise _not_found("watchlist_not_found", f"Watchlist {watchlist_id} not found.")


def _resolved_next_run(
    patch: SchedulePatch, current: ScheduleJob, state: AppState
) -> datetime | None:
    """Validate an edit's trigger/action and resolve the job's new `next_run`. A new trigger, or
    re-enabling a job, recomputes it from now; any other edit leaves the schedule's timing alone."""
    given = patch.model_fields_set
    if "action" in given and patch.action is not None:
        validate_action(patch.action, state.skill_registry)
    trigger = patch.trigger if "trigger" in given and patch.trigger is not None else None
    if trigger is not None:
        validate_trigger(trigger)
    if trigger is None and not (patch.enabled is True and not current.enabled):
        return None
    return next_run_after(trigger or current.trigger, datetime.now(UTC))


@router.delete("/schedules/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    job_id: str,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> None:
    try:
        await store.delete_job(user_id=user.id, job_id=job_id)
    except ScheduleJobNotFoundError as exc:
        raise _not_found("schedule_not_found", f"Schedule {job_id} not found.") from exc


__all__ = ["router"]
