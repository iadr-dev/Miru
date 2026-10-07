"""Per-user LLM consumption: written from each finished turn, read back for the usage settings.

The numbers are the providers' own per-response usage metadata (tokens for every provider, plus
the charge the response carries on OpenRouter). Provider usage-history APIs are deliberately not
used: Anthropic's and OpenAI's need organization admin credentials that an inference key does not
have, and Gemini has none - so this table is the one source that works for every provider.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import CursorResult, Insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import LlmUsageRow
from events.usage import ModelUsage

MAX_DAYS = 90


async def write_model_usage(
    session: AsyncSession,
    *,
    user_id: str,
    turn_id: str,
    models: tuple[ModelUsage, ...],
    byok: bool = False,
) -> int:
    """Insert one row per (provider, model) the turn used, on the caller's session (no commit),
    idempotent on `(user_id, turn_id, provider, model)`. Returns how many rows landed."""
    landed = 0
    now = datetime.now(UTC)
    for usage in models:
        values = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "turn_id": turn_id,
            "provider": usage.provider,
            "model": usage.model,
            "calls": usage.calls,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_tokens": usage.cached_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "cost_micros": usage.cost_micros,
            "byok": byok,
            "created_at": now,
        }
        conflict = ["user_id", "turn_id", "provider", "model"]
        if session.get_bind().dialect.name == "postgresql":
            stmt: Insert = (
                pg_insert(LlmUsageRow)
                .values(**values)
                .on_conflict_do_nothing(index_elements=conflict)
            )
        else:
            stmt = (
                sqlite_insert(LlmUsageRow)
                .values(**values)
                .on_conflict_do_nothing(index_elements=conflict)
            )
        result = cast("CursorResult[object]", await session.execute(stmt))
        landed += int(bool(result.rowcount))
    return landed


class UsageTotals(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cost_micros: int = 0


class DailyUsage(UsageTotals):
    date: date
    # input + output tokens that day, per provider - what the stacked chart is drawn from.
    by_provider: dict[str, int] = {}


class ModelTotals(UsageTotals):
    provider: str
    model: str


class LlmUsageSummary(BaseModel):
    """What the usage settings page draws: a dense daily series (zero-filled, oldest first),
    per-model totals (largest first) and the grand total for the window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    days: int
    providers: tuple[str, ...]  # every provider seen in the window, biggest first
    daily: tuple[DailyUsage, ...]
    models: tuple[ModelTotals, ...]
    totals: UsageTotals
    byok_calls: int


def _add(total: UsageTotals, row: LlmUsageRow) -> UsageTotals:
    return UsageTotals(
        calls=total.calls + row.calls,
        input_tokens=total.input_tokens + row.input_tokens,
        output_tokens=total.output_tokens + row.output_tokens,
        cached_tokens=total.cached_tokens + row.cached_tokens,
        reasoning_tokens=total.reasoning_tokens + row.reasoning_tokens,
        cost_micros=total.cost_micros + row.cost_micros,
    )


async def summarize_llm_usage(
    session: AsyncSession, *, user_id: str, days: int, today: date | None = None
) -> LlmUsageSummary:
    days = max(1, min(days, MAX_DAYS))
    last = today or datetime.now(UTC).date()
    first = last - timedelta(days=days - 1)
    since = datetime(first.year, first.month, first.day, tzinfo=UTC)
    rows = (
        await session.execute(
            select(LlmUsageRow).where(
                LlmUsageRow.user_id == user_id, LlmUsageRow.created_at >= since
            )
        )
    ).scalars()

    per_day: dict[date, UsageTotals] = {}
    per_model: dict[tuple[str, str], UsageTotals] = {}
    day_provider: dict[date, dict[str, int]] = {}
    provider_tokens: dict[str, int] = {}
    total = UsageTotals()
    byok_calls = 0
    for row in rows:
        created = row.created_at
        # SQLite hands back naive datetimes (stored as UTC); Postgres hands back aware ones.
        day = (
            (created.replace(tzinfo=UTC) if created.tzinfo is None else created)
            .astimezone(UTC)
            .date()
        )
        per_day[day] = _add(per_day.get(day, UsageTotals()), row)
        tokens = row.input_tokens + row.output_tokens
        by_provider = day_provider.setdefault(day, {})
        by_provider[row.provider] = by_provider.get(row.provider, 0) + tokens
        provider_tokens[row.provider] = provider_tokens.get(row.provider, 0) + tokens
        key = (row.provider, row.model)
        per_model[key] = _add(per_model.get(key, UsageTotals()), row)
        total = _add(total, row)
        byok_calls += row.calls if row.byok else 0

    daily = tuple(
        DailyUsage(
            date=day,
            by_provider=day_provider.get(day, {}),
            **(per_day.get(day) or UsageTotals()).model_dump(),
        )
        for day in (first + timedelta(days=i) for i in range(days))
    )
    models = tuple(
        sorted(
            (
                ModelTotals(provider=provider, model=model, **totals.model_dump())
                for (provider, model), totals in per_model.items()
            ),
            key=lambda m: m.input_tokens + m.output_tokens,
            reverse=True,
        )
    )
    providers = tuple(sorted(provider_tokens, key=lambda p: provider_tokens[p], reverse=True))
    return LlmUsageSummary(
        days=days,
        providers=providers,
        daily=daily,
        models=models,
        totals=total,
        byok_calls=byok_calls,
    )


__all__ = [
    "MAX_DAYS",
    "DailyUsage",
    "LlmUsageSummary",
    "ModelTotals",
    "UsageTotals",
    "summarize_llm_usage",
    "write_model_usage",
]
