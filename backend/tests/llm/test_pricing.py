"""List-price estimates: per-provider token arithmetic, dated prices, prefix matching."""

from __future__ import annotations

from datetime import date

import pytest

from llm.pricing import estimate_cost_micros, price_for

TODAY = date(2026, 10, 7)


def _est(provider: str, model: str, **tokens: int) -> int | None:
    return estimate_cost_micros(provider=provider, model=model, on=TODAY, **tokens)  # type: ignore[arg-type]


def test_plain_input_and_output_use_the_published_rates() -> None:
    # Claude Haiku 4.5: $1 in / $5 out per MTok -> 1 micro-USD and 5 per token.
    assert _est("anthropic", "claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=200) == (
        1000 * 1 + 200 * 5
    )


def test_anthropic_cache_reads_and_writes_are_on_top_of_input() -> None:
    # Anthropic reports cache reads/writes separately from input_tokens.
    cost = _est(
        "anthropic",
        "claude-sonnet-5",
        input_tokens=1000,
        output_tokens=100,
        cached_tokens=4000,
        cache_write_tokens=2000,
    )
    # $2 in, $0.20 read, 1.25x write = $2.50, $10 out
    assert cost == 1000 * 2 + 4000 * 0.2 + 2000 * 2.5 + 100 * 10


def test_openai_cached_tokens_are_part_of_input() -> None:
    # OpenAI's input_tokens already includes the cached part: only the rest is full price.
    cost = _est("openai", "gpt-5.4-mini", input_tokens=3100, output_tokens=46, cached_tokens=1000)
    assert cost == round(2100 * 0.75 + 1000 * 0.075 + 46 * 4.5)


def test_gemini_thinking_tokens_are_billed_as_output() -> None:
    cost = _est(
        "gemini", "gemini-3.5-flash", input_tokens=1000, output_tokens=100, reasoning_tokens=400
    )
    assert cost == 1000 * 1.5 + 500 * 9


def test_gemini_3_6_flash_price_steps_up_on_the_promo_end_date() -> None:
    before = estimate_cost_micros(
        provider="gemini",
        model="gemini-3.6-flash",
        on=date(2026, 12, 31),
        input_tokens=1000,
        output_tokens=1000,
    )
    after = estimate_cost_micros(
        provider="gemini",
        model="gemini-3.6-flash",
        on=date(2027, 1, 1),
        input_tokens=1000,
        output_tokens=1000,
    )
    assert before == 1000 * 0.75 + 1000 * 3.75
    assert after == 1000 * 1.5 + 1000 * 7.5


def test_the_longest_matching_prefix_wins() -> None:
    lite = price_for("gemini", "gemini-3.5-flash-lite", TODAY)
    flash = price_for("gemini", "gemini-3.5-flash", TODAY)
    assert lite is not None and flash is not None
    assert lite.output < flash.output
    mini = price_for("openai", "gpt-5.4-mini", TODAY)
    full = price_for("openai", "gpt-5.4", TODAY)
    assert mini is not None and full is not None
    assert mini.output < full.output


def test_local_models_are_free_and_unknown_models_have_no_estimate() -> None:
    assert _est("ollama", "qwen3.6:latest", input_tokens=5000, output_tokens=500) == 0
    assert _est("openai", "some-model-we-never-priced", input_tokens=10, output_tokens=10) is None
    assert _est("openrouter", "openrouter/auto", input_tokens=10, output_tokens=10) is None


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("anthropic", "claude-haiku-4-5-20251001"),
        ("anthropic", "claude-sonnet-4-6"),
        ("anthropic", "claude-sonnet-5"),
        ("anthropic", "claude-opus-4-6"),
        ("anthropic", "claude-opus-4-8"),
        ("openai", "gpt-4o-mini"),
        ("openai", "gpt-5.4"),
        ("openai", "gpt-5.4-mini"),
        ("openai", "gpt-5.6-luna"),
        ("gemini", "gemini-3.1-pro-preview"),
        ("gemini", "gemini-3.5-flash-lite"),
        ("gemini", "gemini-3.5-flash"),
        ("gemini", "gemini-3.6-flash"),
    ],
)
def test_every_model_in_the_picker_that_lacks_an_inline_charge_is_priced(
    provider: str, model: str
) -> None:
    assert price_for(provider, model, TODAY) is not None
