"""Per-user LLM consumption: written per (turn, provider, model), summarised per day and model."""

from __future__ import annotations

from datetime import UTC, date, datetime

from events.usage import ModelUsage, UsageAccumulator
from usage.llm_usage import summarize_llm_usage, write_model_usage

TODAY = date(2026, 10, 7)


def _usage(provider: str, model: str, tokens_in: int, tokens_out: int, cost: int = 0) -> ModelUsage:
    return ModelUsage(
        provider=provider,
        model=model,
        calls=1,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        cost_micros=cost,
    )


async def test_rows_are_idempotent_per_turn_and_model(session_factory) -> None:
    async with session_factory() as session:
        models = (_usage("openrouter", "auto", 100, 20, 1500),)
        first = await write_model_usage(session, user_id="u", turn_id="t1", models=models)
        again = await write_model_usage(session, user_id="u", turn_id="t1", models=models)
        await session.commit()
    assert (first, again) == (1, 0)


async def test_summary_is_dense_per_day_and_sorted_per_model(session_factory) -> None:
    async with session_factory() as session:
        await write_model_usage(
            session,
            user_id="u",
            turn_id="t1",
            models=(
                _usage("anthropic", "sonnet", 1000, 200),
                _usage("openrouter", "auto", 50, 5, 900),
            ),
        )
        await write_model_usage(
            session, user_id="u", turn_id="t2", models=(_usage("anthropic", "sonnet", 500, 100),)
        )
        await write_model_usage(
            session, user_id="other", turn_id="t9", models=(_usage("anthropic", "sonnet", 9, 9),)
        )
        await session.commit()
        today = datetime.now(UTC).date()
        summary = await summarize_llm_usage(session, user_id="u", days=7, today=today)

    assert len(summary.daily) == 7
    assert summary.daily[-1].date == today
    assert summary.daily[-1].input_tokens == 1550
    assert sum(d.input_tokens for d in summary.daily[:-1]) == 0  # zero-filled, oldest first
    assert [m.model for m in summary.models] == ["sonnet", "auto"]  # largest first
    assert summary.models[0].input_tokens == 1500
    assert summary.totals.cost_micros == 900  # another user's rows never leak in
    assert summary.providers == ("anthropic", "openrouter")  # biggest first
    assert summary.daily[-1].by_provider == {"anthropic": 1500 + 300, "openrouter": 55}
    assert summary.daily[0].by_provider == {}


async def test_window_excludes_older_rows(session_factory) -> None:
    async with session_factory() as session:
        await write_model_usage(
            session, user_id="u", turn_id="t1", models=(_usage("a", "m", 5, 5),)
        )
        await session.commit()
        later = date(2027, 1, 1)
        summary = await summarize_llm_usage(session, user_id="u", days=7, today=later)
    assert summary.totals.input_tokens == 0


def test_accumulator_groups_calls_by_model() -> None:
    acc = UsageAccumulator("t")
    acc.add_llm_call(input_tokens=10, output_tokens=2, provider="p", model="m", cached_tokens=4)
    acc.add_llm_call(input_tokens=5, output_tokens=1, provider="p", model="m", cost_micros=7)
    acc.add_llm_call(input_tokens=1, output_tokens=1)  # unattributed: totals only
    snapshot = acc.snapshot()
    assert snapshot.input_tokens == 16
    assert len(snapshot.models) == 1
    model = snapshot.models[0]
    assert (model.calls, model.input_tokens, model.cached_tokens, model.cost_micros) == (
        2,
        15,
        4,
        7,
    )
