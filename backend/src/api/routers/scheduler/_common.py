"""Helpers shared by the scheduler route modules."""

from __future__ import annotations

from fastapi import Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import (
    get_session,
)
from api.errors import ApiError
from scheduler.store import (
    SchedulerStore,
    SqlAlchemySchedulerStore,
)

# Numeric literal, matching api/errors.py — the `status.HTTP_422_*` constant name churns across
# Starlette versions and emits a deprecation warning; the code is stable.
HTTP_422_UNPROCESSABLE = 422


def store(session: AsyncSession = Depends(get_session)) -> SchedulerStore:
    return SqlAlchemySchedulerStore(session)


def cap_reached(what: str, limit: int) -> ApiError:
    return ApiError(
        status_code=status.HTTP_409_CONFLICT,
        code="cap_reached",
        message=f"You can keep at most {limit} {what}. Delete or pause one to add another.",
        retryable=False,
    )


def not_found(code: str, message: str) -> ApiError:
    return ApiError(
        status_code=status.HTTP_404_NOT_FOUND, code=code, message=message, retryable=False
    )
