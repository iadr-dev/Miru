"""The chat turn chip's estimate and the settings summary share one function, so they agree."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.turn_driver import _with_estimated_cost
from events.usage import ModelUsage, TurnUsage
from orchestrator.models import OrchestratorResult, Routing
from usage.llm_usage import estimate_turn_cost_micros, summarize_llm_usage, write_model_usage


def _m(provider: str, model: str, cost: int = 0) -> ModelUsage:
    return ModelUsage(
        provider=provider,
        model=model,
        calls=1,
        input_tokens=1000,
        output_tokens=100,
        cost_micros=cost,
    )


def _result(*models: ModelUsage) -> OrchestratorResult:
    return OrchestratorResult(
        final_text="x",
        routing=Routing(framework="none", reason="t"),
        usage=TurnUsage(turn_id="t1", models=tuple(models)),
    )


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("anthropic", "claude-sonnet-5-5"),
        ("openai", "gpt-5.4-mini"),
        ("gemini", "gemini-3.5-flash"),
    ],
)
def test_priced_providers_get_an_estimate(provider: str, model: str) -> None:
    assert estimate_turn_cost_micros((_m(provider, model),)) > 0


def test_reported_charge_and_unpriced_models_contribute_nothing() -> None:
    assert estimate_turn_cost_micros((_m("openrouter", "auto", 900),)) == 0
    assert estimate_turn_cost_micros((_m("acme", "mystery"),)) == 0
    mixed = (_m("openrouter", "auto", 900), _m("openai", "gpt-5.4-mini"))
    assert estimate_turn_cost_micros(mixed) == estimate_turn_cost_micros((mixed[1],))


def test_only_byok_turns_carry_the_estimate() -> None:
    result = _result(_m("openai", "gpt-5.4-mini"))
    assert _with_estimated_cost(result, turn_id="t1", byok=False).usage.estimated_cost_micros == 0  # type: ignore[union-attr]
    got = _with_estimated_cost(result, turn_id="t1", byok=True).usage
    assert got is not None
    assert got.estimated_cost_micros == round(1000 * 0.75 + 100 * 4.5)
    assert got.cost_micros == 0  # kept apart from the provider-reported charge


def test_an_old_stored_usage_event_still_parses() -> None:
    old = {"turn_id": "t", "input_tokens": 5, "output_tokens": 2, "cost_micros": 0}
    assert TurnUsage.model_validate(old).estimated_cost_micros == 0


async def test_chat_estimate_and_settings_summary_agree(session_factory) -> None:
    models = (_m("anthropic", "claude-sonnet-5-5"), _m("openai", "gpt-5.4-mini"))
    async with session_factory() as session:
        await write_model_usage(session, user_id="u", turn_id="t1", models=models, byok=True)
        await session.commit()
        summary = await summarize_llm_usage(
            session, user_id="u", days=7, today=datetime.now(UTC).date()
        )
    chat = _with_estimated_cost(_result(*models), turn_id="t1", byok=True).usage
    assert chat is not None
    assert chat.estimated_cost_micros == summary.totals.estimated_cost_micros > 0
