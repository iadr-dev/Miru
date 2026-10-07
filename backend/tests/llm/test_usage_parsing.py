"""Per-response usage detail a provider reports (cached/reasoning tokens, OpenRouter cost)."""

from __future__ import annotations

from llm.openai_compat import _usage_from_raw


def test_openrouter_usage_accounting_fields_are_read() -> None:
    usage = _usage_from_raw(
        {
            "prompt_tokens": 194,
            "completion_tokens": 2,
            "total_tokens": 196,
            "prompt_tokens_details": {"cached_tokens": 50, "cache_write_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 7},
            "cost": 0.00014,
            "cost_details": {"upstream_inference_cost": 0.0},
        }
    )
    assert (usage.input_tokens, usage.output_tokens) == (194, 2)
    assert (usage.cached_tokens, usage.reasoning_tokens) == (50, 7)
    assert usage.cost_micros == 140


def test_plain_openai_compatible_usage_has_no_detail() -> None:
    usage = _usage_from_raw({"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7})
    assert (usage.cached_tokens, usage.reasoning_tokens, usage.cost_micros) == (0, 0, 0)


def test_openrouter_cache_write_tokens_are_read() -> None:
    usage = _usage_from_raw(
        {
            "prompt_tokens": 500,
            "completion_tokens": 10,
            "total_tokens": 510,
            "prompt_tokens_details": {"cached_tokens": 100, "cache_write_tokens": 200},
        }
    )
    assert (usage.cached_tokens, usage.cache_write_tokens) == (100, 200)
