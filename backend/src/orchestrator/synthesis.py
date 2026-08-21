"""Synthesis: merge sub-agent/team lane outputs into one caveated, non-trading answer using the
best-quality SYNTHESIS model role.

Governance is enforced at two layers: each lane's `text` already passed
`guardrails.check_output` inside its Step loop (`AgentLoop._finalize_output`) before it reached
here, and the merged answer is run through `guardrails.check_output` again — trading language
can't be laundered into synthesis input and paraphrased past the final check.

Handling:
- **Partial results:** synthesis proceeds with >= 1 successful lane and declares which
  dimensions are missing and why. The turn fails (raises) only when zero lanes succeed.
- **Conflicting lanes:** the synthesis prompt instructs the model to surface both conclusions
  with provenance and caveat the divergence — never silently pick a winner.
- **Synthesis-call failure:** if the SYNTHESIS call itself fails, fall back to a structured
  concatenation of the (already guardrail-checked) per-lane findings under a "synthesis
  degraded" banner — successful lanes are never discarded. The concatenation still passes
  `check_output`.
"""

from __future__ import annotations

import structlog

from agent import guardrails
from llm.base import LLMClient, Message, MessageRole
from llm.registry import ModelRole, ProviderNotConfiguredError, resolve_role_with_override
from orchestrator.models import LaneResult, SynthesisProvenance
from skills.activity_labels import skill_activity_label

logger = structlog.get_logger(__name__)

_DEFAULT_LOCALE = "zh-TW"


class NoSuccessfulLanesError(RuntimeError):
    """Raised when every lane failed — the only case where an orchestrated turn has nothing to
    synthesize. The orchestrator turns this into a structured 'all lanes unavailable' degraded
    answer, not an unhandled crash."""


def _synthesis_system_prompt(locale: str) -> str:
    """The merge-instruction system prompt, in `locale` — without this the model defaults to
    whatever language its own training favors for the instruction language (English), producing
    an answer in the wrong language regardless of what the user actually asked in (governance
    rule 8: respond in the user's own language). Also explicitly forbids naming internal skill
    ids — `_render_lane_block`/`_render_missing` already substitute a human label for `lane.skill`
    before this prompt ever sees it, but the instruction is restated here as a second line of
    defense against the model inventing its own attribution phrasing from context."""
    if locale == "zh-TW":
        return (
            "你是一個治理型、非交易導向台股/ETF分析代理人的整合（synthesis）代理人。"
            "你會收到數個獨立分析路徑（lane）的輸出，每個都標示著它涵蓋的分析主題。"
            "請將它們合併成一個連貫、分析性的答案，並全程使用繁體中文回覆。規則：\n"
            "- 這僅為資訊性市場觀察，絕非買進/賣出/持有建議、目標價或部位建議。\n"
            "- 若兩個分析路徑得出衝突的結論，兩者都要呈現並標明依據，絕不能悄悄選一個。\n"
            "- 若某個分析路徑缺失或降級，明確說明缺少哪個面向及原因，用一般使用者能理解的說法，"
            "不要出現技術性或內部實作細節。\n"
            "- 保留每個分析路徑輸出中的資料時效性說明。\n"
            "- 用每個分析主題的自然語言說法歸屬數據來源（如「籌碼分析」），絕不能出現內部代號、"
            "程式變數名稱或例外錯誤訊息。"
        )
    return (
        "You are the synthesis agent for a governed, non-trading Taiwan-stock/ETF analysis agent. "
        "You are given the outputs of several independent analysis lanes, each labelled by the "
        "topic it covers. Merge them into ONE coherent, analytical answer, entirely in English. "
        "Rules:\n"
        "- This is informational market context only — never a buy/sell/hold recommendation, price "
        "target, or position sizing.\n"
        "- If two lanes reach conflicting conclusions, surface BOTH with their provenance and note "
        "the divergence — never silently pick one.\n"
        "- If a lane is missing or degraded, explicitly state which dimension is unavailable and "
        "why, in plain language a general reader understands — never technical or internal "
        "implementation detail.\n"
        "- Preserve every currentness caveat from the lane outputs.\n"
        "- Attribute figures to their contributing topic in natural language (e.g. "
        '"institutional flow analysis") — never an internal skill id, code identifier, or raw '
        "exception message."
    )


_DEGRADED_BANNER = (
    "[synthesis degraded — the merge step was unavailable, so these are the raw per-lane "
    "findings concatenated; each was independently guardrail-checked]"
)

_MISSING_DIMENSION_PREFACE = "Unavailable dimensions (excluded from this analysis):"


def _lane_reason(lane: LaneResult) -> str:
    return lane.reason or lane.status


def _render_lane_block(lane: LaneResult, *, locale: str) -> str:
    # `skill_activity_label` (shared with the live SSE lane-narration path) substitutes a
    # human-readable topic phrase for the raw internal skill id (`reference-data-lookup` ->
    # "正在查詢參考資料") — the synthesis prompt, and therefore anything it might quote back into
    # the user-facing answer, never sees the id itself.
    label = skill_activity_label(lane.skill, locale=locale)
    return f"### {label} (confidence {lane.confidence:.2f})\n{lane.text.strip()}"


def _render_missing(missing: list[LaneResult], *, locale: str) -> str:
    lines = [
        f"- {skill_activity_label(lane.skill, locale=locale)}: {_lane_reason(lane)}"
        for lane in missing
    ]
    return _MISSING_DIMENSION_PREFACE + "\n" + "\n".join(lines)


def _structured_concatenation(
    successful: list[LaneResult], missing: list[LaneResult], *, locale: str
) -> str:
    parts = [_DEGRADED_BANNER, *[_render_lane_block(lane, locale=locale) for lane in successful]]
    if missing:
        parts.append(_render_missing(missing, locale=locale))
    return "\n\n".join(parts)


def _finalize(text: str) -> str:
    """Run the mandatory final `pre_output` guardrail on the merged answer, applying any
    redaction/caveat-append it returns."""
    verdict = guardrails.check_output(text)
    return verdict.redacted_text if verdict.redacted_text is not None else text


class Synthesizer:
    """Merges lane outputs into one governed answer. Uses the SYNTHESIS model role; on any
    failure of that call it falls back to the structured-concatenation banner."""

    def __init__(self, *, client: LLMClient | None = None) -> None:
        self._client = client

    def _resolve_client(self, model_override: str | None) -> LLMClient | None:
        if self._client is not None:
            return self._client
        try:
            # Honors the thread's `model_override` (same "provider/model" degrade-on-failure
            # contract `Orchestrator._resolve_agent_client` uses) — synthesis is the pass that
            # writes the final answer a fan-out turn's user actually reads, so their model pick
            # should apply here too, not just to the AGENT role.
            return resolve_role_with_override(ModelRole.SYNTHESIS, model_override)
        except ProviderNotConfiguredError:
            logger.warning("synthesis_provider_unconfigured_falling_back_to_concatenation")
            return None

    async def synthesize(
        self,
        lanes: tuple[LaneResult, ...],
        *,
        model_override: str | None = None,
        locale: str = _DEFAULT_LOCALE,
    ) -> tuple[str, SynthesisProvenance]:
        """Merge `lanes`. Returns the final guardrail-checked text plus its provenance. Raises
        `NoSuccessfulLanesError` if no lane produced usable content."""
        successful = [lane for lane in lanes if lane.succeeded]
        missing = [lane for lane in lanes if not lane.succeeded]
        if not successful:
            raise NoSuccessfulLanesError("no lane returned usable content")

        contributing = tuple(lane.skill for lane in successful)
        missing_skills = tuple(lane.skill for lane in missing)

        client = self._resolve_client(model_override)
        if client is None:
            text = _finalize(_structured_concatenation(successful, missing, locale=locale))
            return text, SynthesisProvenance(
                contributing_skills=contributing,
                missing_skills=missing_skills,
                degraded=True,
            )

        prompt = self._build_prompt(successful, missing, locale=locale)
        try:
            response = await client.chat(prompt)
            merged = response.text or ""
            if not merged.strip():
                raise ValueError("empty synthesis response")
            text = _finalize(merged)
            return text, SynthesisProvenance(
                contributing_skills=contributing,
                missing_skills=missing_skills,
                degraded=False,
            )
        except Exception as exc:
            # A failed synthesis call must degrade to the concatenation fallback — never discard
            # successful lanes or crash the turn.
            logger.warning("synthesis_call_failed_falling_back", error=str(exc))
            text = _finalize(_structured_concatenation(successful, missing, locale=locale))
            return text, SynthesisProvenance(
                contributing_skills=contributing,
                missing_skills=missing_skills,
                degraded=True,
            )

    def _build_prompt(
        self, successful: list[LaneResult], missing: list[LaneResult], *, locale: str
    ) -> list[Message]:
        lane_text = "\n\n".join(_render_lane_block(lane, locale=locale) for lane in successful)
        user_content = f"Lane outputs to merge:\n\n{lane_text}"
        if missing:
            user_content += "\n\n" + _render_missing(missing, locale=locale)
        return [
            Message(role=MessageRole.SYSTEM, content=_synthesis_system_prompt(locale)),
            Message(role=MessageRole.USER, content=user_content),
        ]


__all__ = ["NoSuccessfulLanesError", "Synthesizer"]
