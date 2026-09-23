"""`generate_stock_summary` — an LLM sub-task that turns an already-computed `ScoreResult` into a
Sponsor-tier headline + narrative + SWOT, strictly grounded in that score's own factor breakdown
(never any other input — no raw price, no news, no anything not already on `ScoreResult`).

Follows `conversation/branch_summary.py`'s exact structured-output pattern: a plain system prompt
demanding "respond with ONLY a JSON object with exactly these keys", `llm.chat(...)`, a hand-rolled
`_parse_summary_json` with a regex `\\{.*\\}` fallback, manual field coercion — no native tool-
calling/schema-forcing, matching this codebase's one LLM-structured-output convention.

The parsed dict is run through `governance.textscan.scan_structured_payload` before this module
ever constructs the final `StockSummaryResult` — belt-and-suspenders on top of the prompt's own
"never buy/sell/hold" instruction, exactly as the phase brief requires. This imports from
`governance.textscan` (a leaf package `agent.guardrails` itself re-exports the same function
from) rather than `agent.guardrails` directly, because `analytics` sits below `agent` in
`tests/arch/test_layering.py`'s tracked one-way dependency graph — importing `agent.guardrails`
here would be a forbidden upward edge; `governance/` is deliberately outside that graph, matching
`llm/base.py`'s own precedent for a shared leaf module. On any parse failure, this function
returns a plain minimal fallback grounded in the composite/band alone — it never raises up to the
caller, matching `branch_summary.py`'s own graceful-fallback discipline for a flaky LLM response.
"""

from __future__ import annotations

import json
import re
from typing import Final, cast

import structlog

from analytics.models import ScoreResult, StockSummaryResult, StockSwot
from governance.textscan import scan_structured_payload
from llm.base import LLMClient, Message, MessageRole

logger = structlog.get_logger(__name__)

_SUMMARY_SYSTEM_PROMPT: Final[str] = """\
You write a short analytical summary of a stock/ETF's already-computed multi-factor score for a \
detail page on a Traditional-Chinese (Taiwan), zh-TW-first product. Write EVERY string value in \
your response in Traditional Chinese (zh-TW) — never English, never Simplified Chinese. The \
factor breakdown below is ALREADY labeled in zh-TW (e.g. "動能", "趨勢品質") — always refer to a \
factor by that zh-TW label. Never write a raw snake_case identifier (e.g. `momentum`, \
`trend_quality`, `revenue_flow`) anywhere in your response; ticker symbols are the only bare \
identifiers allowed untranslated.

You are given the composite score's descriptive band and the full breakdown of named factors \
(each with a percentile rank and a plain-language description, or a reason it was unavailable). \
Ground EVERY sentence and EVERY SWOT bullet strictly in the factors you are given — never invent \
a fact, number, news event, or company detail not present in the supplied data. Never use buy/\
sell/hold language, price targets, ratings, or position-sizing language of any kind (e.g. 買進/\
賣出/目標價) — this is descriptive market context only, not a recommendation.

Respond with ONLY a JSON object with exactly these keys: "headline" (one short zh-TW sentence \
summarizing the overall picture), "narrative" (2-4 zh-TW sentences expanding on the factor mix), \
"strengths" (a list of short zh-TW strings, each citing one specific factor that supports it), \
"weaknesses" (same shape, factors that detract), "opportunities" (same shape, forward-looking \
reads grounded in a factor), "threats" (same shape, risk reads grounded in a factor). Any list \
may be empty if no factor supports that quadrant. No prose outside the JSON object.
"""

_FALLBACK_NARRATIVE_TEMPLATE: Final[str] = (
    "{symbol} 的多因子綜合評分為 {composite}，描述為「{band}」。"
    "本次未能生成詳細摘要，完整的因子拆解仍可另行查看。"
)

# The user-facing zh-TW label for each internal snake_case factor field name — mirrors
# `web/messages/zh-TW.json`'s `stock.ai.factor.*` keys exactly, so the LLM never sees (and can
# never echo back) a raw identifier like "trend_quality"/"revenue_flow". Covers both
# `StockScoreBreakdown` and `EtfScoreBreakdown` field names (`analytics/models.py`).
_FACTOR_LABELS: Final[dict[str, str]] = {
    "momentum": "動能",
    "trend_quality": "趨勢品質",
    "value_guard": "價值防護",
    "quality": "品質",
    "revenue_flow": "籌碼與營收",
    "premium_discount": "折溢價",
    "cost": "費用率",
    "management_activity": "主動管理程度",
    "liquidity_flow": "流動性與資金流",
}


def _factor_label(field_name: str) -> str:
    return _FACTOR_LABELS.get(field_name, field_name)


def _factor_lines(score: ScoreResult) -> str:
    lines: list[str] = []
    breakdown = score.breakdown
    for field_name in type(breakdown).model_fields:
        factor = getattr(breakdown, field_name)
        label = _factor_label(field_name)
        if factor.percentile is None:
            lines.append(f"- {label}: unavailable ({factor.unavailable_reason or 'no data'})")
        else:
            lines.append(f"- {label}: percentile {factor.percentile:.1f} — {factor.description}")
    return "\n".join(lines)


def _render_score_context(score: ScoreResult) -> str:
    composite_text = "N/A" if score.composite is None else f"{score.composite:.1f}"
    parts = [
        f"Symbol: {score.symbol} ({score.market.value})",
        f"Composite score: {composite_text}",
        f"Band: {score.band.value}",
        f"Regime caveat: {score.regime_caveat}",
        "Factor breakdown:",
        _factor_lines(score),
    ]
    if score.partial_factors:
        parts.append(f"Factors excluded as unavailable: {', '.join(score.partial_factors)}")
    return "\n".join(parts)


def _parse_summary_json(raw_text: str) -> dict[str, object]:
    parsed: object
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return cast("dict[str, object]", parsed) if isinstance(parsed, dict) else {}


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in cast("list[object]", value))


def _fallback_result(score: ScoreResult) -> StockSummaryResult:
    composite_text = "N/A" if score.composite is None else f"{score.composite:.1f}"
    return StockSummaryResult(
        symbol=score.symbol,
        market=score.market,
        as_of=score.as_of,
        headline=score.band.value,
        narrative=_FALLBACK_NARRATIVE_TEMPLATE.format(
            symbol=score.symbol, composite=composite_text, band=score.band.value
        ),
        swot=StockSwot(),
        non_trading_caveat=score.non_trading_caveat,
    )


async def generate_stock_summary(score: ScoreResult, llm: LLMClient) -> StockSummaryResult:
    """Build a prompt strictly from `score.breakdown`'s factor `name`/`percentile`/`description`
    fields plus `score.band` and `score.regime_caveat` (nothing else), call `llm`, parse the
    structured response, and run the parsed dict through `scan_structured_payload` before
    constructing the final result. Never raises — any parse failure degrades to a plain minimal
    fallback citing the composite score itself."""
    context = _render_score_context(score)
    try:
        response = await llm.chat(
            [
                Message(role=MessageRole.SYSTEM, content=_SUMMARY_SYSTEM_PROMPT),
                Message(role=MessageRole.USER, content=context),
            ]
        )
    except Exception as exc:  # LLM outage/timeout — degrade, never fail the caller
        logger.warning("stock_summary_llm_call_failed", symbol=score.symbol, error=str(exc))
        return _fallback_result(score)

    parsed = _parse_summary_json(response.text or "")
    if not parsed:
        logger.warning("stock_summary_parse_failed", symbol=score.symbol)
        return _fallback_result(score)

    cleaned_obj, _redacted = scan_structured_payload(parsed)
    cleaned = cast("dict[str, object]", cleaned_obj)

    headline = str(cleaned.get("headline") or score.band.value)
    narrative = str(
        cleaned.get("narrative")
        or _FALLBACK_NARRATIVE_TEMPLATE.format(
            symbol=score.symbol,
            composite="N/A" if score.composite is None else f"{score.composite:.1f}",
            band=score.band.value,
        )
    )
    swot = StockSwot(
        strengths=_string_tuple(cleaned.get("strengths")),
        weaknesses=_string_tuple(cleaned.get("weaknesses")),
        opportunities=_string_tuple(cleaned.get("opportunities")),
        threats=_string_tuple(cleaned.get("threats")),
    )

    return StockSummaryResult(
        symbol=score.symbol,
        market=score.market,
        as_of=score.as_of,
        headline=headline,
        narrative=narrative,
        swot=swot,
        non_trading_caveat=score.non_trading_caveat,
    )


__all__ = ["generate_stock_summary"]
