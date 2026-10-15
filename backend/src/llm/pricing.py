"""List prices for the models we serve, and the arithmetic that turns recorded tokens into an
*estimated* cost.

Why an estimate: only OpenRouter returns a charge on each response. Anthropic's and OpenAI's cost
APIs need organization admin credentials and Gemini has none, so for those providers the honest
number is "tokens x published list price". It is labelled as an estimate everywhere it is shown and
never mixed with a provider-reported charge.

Units: a price of `$P / MTok` is exactly `P` micro-dollars per token, so prices here are stored as
"micro-USD per token" (= the published $/MTok figure) and the result is micro-USD, the same unit
the usage tables already use.

Sources (checked 2026-10-07; re-verify when a model or price changes):
- Anthropic: https://platform.claude.com/docs/en/about-claude/pricing
- OpenAI:    https://developers.openai.com/api/docs/pricing
- Gemini:    https://ai.google.dev/gemini-api/docs/pricing

Known simplifications (they make the estimate slightly low, never high): the >200k / >272k
long-context surcharges (and Claude Haiku 5.5's over-100k-token rate) apply per request but usage
is stored per turn, so the standard rate is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class ModelPrice:
    """Published list price, USD per million tokens."""

    input: Decimal
    cached_input: Decimal
    output: Decimal
    # Writing a prompt prefix to the cache. Only Anthropic charges extra for it (1.25x input for
    # the default 5-minute cache); everywhere else a cache write is just input.
    cache_write: Decimal


def _price(input_: str, cached: str, output: str, *, write_multiplier: str = "1") -> ModelPrice:
    return ModelPrice(
        Decimal(input_),
        Decimal(cached),
        Decimal(output),
        Decimal(input_) * Decimal(write_multiplier),
    )


@dataclass(frozen=True)
class _Schedule:
    """A price that changes on a date: `price` applies to usage on/after `from_date`."""

    from_date: date | None
    price: ModelPrice


# How each provider counts tokens on the response (this decides the arithmetic):
# - Anthropic: `input_tokens` EXCLUDES cache reads, which are reported separately.
# - OpenAI / OpenRouter-style: `input_tokens` INCLUDES the cached part.
# - Gemini: `output_tokens` (candidates) EXCLUDES thinking tokens, which are billed as output.
_CACHED_INCLUDED_IN_INPUT = {"openai": True, "gemini": True, "anthropic": False}
_THINKING_SEPARATE_FROM_OUTPUT = {"gemini"}

_FREE_LOCAL = _price("0", "0", "0")

# (provider, model-id prefix) -> dated prices. The longest matching prefix wins, so a dated id such
# as "claude-haiku-4-5-20251001" matches "claude-haiku-4-5".
_PRICES: dict[tuple[str, str], tuple[_Schedule, ...]] = {
    # ---- Anthropic (cache hit = 0.1x input) ----
    ("anthropic", "claude-haiku-4-5"): (
        _Schedule(None, _price("1", "0.10", "5", write_multiplier="1.25")),
    ),
    ("anthropic", "claude-sonnet-4-6"): (
        _Schedule(None, _price("3", "0.30", "15", write_multiplier="1.25")),
    ),
    ("anthropic", "claude-sonnet-5"): (
        _Schedule(None, _price("2", "0.20", "10", write_multiplier="1.25")),
    ),
    # Cache hits cost 0.05x input on Opus 5.5 and Sonnet 5.5 (0.1x on earlier models).
    ("anthropic", "claude-sonnet-5-5"): (
        _Schedule(None, _price("2", "0.10", "10", write_multiplier="1.25")),
    ),
    ("anthropic", "claude-opus-5-5"): (
        _Schedule(None, _price("4", "0.20", "20", write_multiplier="1.25")),
    ),
    # Haiku 5.5 is priced by prompt length; this is the rate for prompts up to 100,000 tokens. A
    # prompt over that pays $0.50 in / $0.05 cache hit / $2.50 out, which a per-turn total cannot
    # tell apart from several smaller requests, so the estimate stays at the lower rate.
    ("anthropic", "claude-haiku-5-5"): (
        _Schedule(None, _price("0.10", "0.01", "0.50", write_multiplier="1.25")),
    ),
    ("anthropic", "claude-opus-4-6"): (
        _Schedule(None, _price("5", "0.50", "25", write_multiplier="1.25")),
    ),
    ("anthropic", "claude-opus-4-8"): (
        _Schedule(None, _price("5", "0.50", "25", write_multiplier="1.25")),
    ),
    # ---- OpenAI (standard tier, short context) ----
    ("openai", "gpt-4o-mini"): (_Schedule(None, _price("0.15", "0.075", "0.60")),),
    ("openai", "gpt-5.4-mini"): (_Schedule(None, _price("0.75", "0.075", "4.50")),),
    ("openai", "gpt-5.4"): (_Schedule(None, _price("2.50", "0.25", "15")),),
    ("openai", "gpt-5.6-luna"): (_Schedule(None, _price("0.20", "0.02", "1.20")),),
    ("openai", "gpt-5.6-sol"): (_Schedule(None, _price("2", "0.20", "10")),),
    ("openai", "gpt-5.6-terra"): (_Schedule(None, _price("2", "0.20", "12")),),
    # ---- Gemini (paid tier; output includes thinking) ----
    ("gemini", "gemini-3.1-pro"): (_Schedule(None, _price("2", "0.20", "12")),),
    ("gemini", "gemini-3.5-flash-lite"): (_Schedule(None, _price("0.30", "0.03", "2.50")),),
    ("gemini", "gemini-3.5-flash"): (_Schedule(None, _price("1.50", "0.15", "9")),),
    # Introductory price through 2026-12-31, then the standard rate from 2027-01-01.
    ("gemini", "gemini-3.6-flash"): (
        _Schedule(None, _price("0.75", "0.075", "3.75")),
        _Schedule(date(2027, 1, 1), _price("1.50", "0.15", "7.50")),
    ),
    # ---- Local models cost nothing per token ----
    ("ollama", ""): (_Schedule(None, _FREE_LOCAL),),
}


def price_for(provider: str, model: str, on: date) -> ModelPrice | None:
    """The list price in force on `on` for this model, or None if we do not know it."""
    provider = provider.lower()
    best: tuple[str, tuple[_Schedule, ...]] | None = None
    for (known_provider, prefix), schedules in _PRICES.items():
        if known_provider != provider or not model.startswith(prefix):
            continue
        if best is None or len(prefix) > len(best[0]):
            best = (prefix, schedules)
    if best is None:
        return None
    applicable = [s for s in best[1] if s.from_date is None or s.from_date <= on]
    # Later `from_date`s override earlier ones; the undated entry is the base.
    return max(applicable, key=lambda s: s.from_date or date.min).price


def prompt_tokens(
    provider: str, input_tokens: int, cached_tokens: int = 0, cache_write_tokens: int = 0
) -> int:
    """Every token the model read for a request, the number a person means by "input tokens".

    Providers disagree on what their `input_tokens` field holds: OpenAI and Gemini count the cached
    part inside it, Anthropic reports the cache reads and cache writes separately and leaves them
    out (so a request that read 55,000 cached tokens shows `input_tokens` of 20). Stored values are
    kept as reported, since the price arithmetic needs them that way; anything shown to a person
    goes through here."""
    if _CACHED_INCLUDED_IN_INPUT.get(provider.lower(), True):
        return input_tokens
    return input_tokens + cached_tokens + cache_write_tokens


def estimate_cost_micros(  # noqa: PLR0913 - one argument per token class a provider reports
    *,
    provider: str,
    model: str,
    on: date,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> int | None:
    """Estimated charge in micro-USD, or None when the model has no known list price.

    Cache reads are priced at the (cheaper) hit rate and cache writes at the write rate, each taken
    out of the fresh-input count where the provider folds them into `input_tokens`."""
    price = price_for(provider, model, on)
    if price is None:
        return None
    provider = provider.lower()
    folded = _CACHED_INCLUDED_IN_INPUT.get(provider, True)
    cached = min(cached_tokens, input_tokens) if folded else cached_tokens
    writes = (
        min(cache_write_tokens, max(input_tokens - cached, 0)) if folded else cache_write_tokens
    )
    fresh_input = input_tokens - cached - writes if folded else input_tokens
    billed_output = output_tokens + (
        reasoning_tokens if provider in _THINKING_SEPARATE_FROM_OUTPUT else 0
    )
    total = (
        Decimal(fresh_input) * price.input
        + Decimal(cached) * price.cached_input
        + Decimal(writes) * price.cache_write
        + Decimal(billed_output) * price.output
    )
    return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


__all__ = ["ModelPrice", "estimate_cost_micros", "price_for", "prompt_tokens"]
