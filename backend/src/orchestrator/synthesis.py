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

logger = structlog.get_logger(__name__)


class NoSuccessfulLanesError(RuntimeError):
    """Raised when every lane failed — the only case where an orchestrated turn has nothing to
    synthesize. The orchestrator turns this into a structured 'all lanes unavailable' degraded
    answer, not an unhandled crash."""


_SYNTHESIS_SYSTEM_PROMPT = (
    "You are the synthesis agent for a governed, non-trading Taiwan-stock/ETF analysis agent. "
    "You are given the outputs of several independent analysis lanes, each labelled by its "
    "skill. Merge them into ONE coherent, analytical answer. Rules:\n"
    "- This is informational market context only — never a buy/sell/hold recommendation, price "
    "target, or position sizing.\n"
    "- If two lanes reach conflicting conclusions, surface BOTH with their provenance and note "
    "the divergence — never silently pick one.\n"
    "- If a lane is missing or degraded, explicitly state which dimension is unavailable and "
    "why.\n"
    "- Preserve every currentness caveat from the lane outputs.\n"
    "- Attribute figures to their contributing skill/lane where it aids the reader."
)

_DEGRADED_BANNER = (
    "[synthesis degraded — the merge step was unavailable, so these are the raw per-lane "
    "findings concatenated; each was independently guardrail-checked]"
)

_MISSING_DIMENSION_PREFACE = "Unavailable dimensions (excluded from this analysis):"


def _lane_reason(lane: LaneResult) -> str:
    return lane.reason or lane.status


def _render_lane_block(lane: LaneResult) -> str:
    return f"### Lane: {lane.skill} (confidence {lane.confidence:.2f})\n{lane.text.strip()}"


def _render_missing(missing: list[LaneResult]) -> str:
    lines = [f"- {lane.skill}: {_lane_reason(lane)}" for lane in missing]
    return _MISSING_DIMENSION_PREFACE + "\n" + "\n".join(lines)


def _structured_concatenation(successful: list[LaneResult], missing: list[LaneResult]) -> str:
    parts = [_DEGRADED_BANNER, *[_render_lane_block(lane) for lane in successful]]
    if missing:
        parts.append(_render_missing(missing))
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
        self, lanes: tuple[LaneResult, ...], *, model_override: str | None = None
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
            text = _finalize(_structured_concatenation(successful, missing))
            return text, SynthesisProvenance(
                contributing_skills=contributing,
                missing_skills=missing_skills,
                degraded=True,
            )

        prompt = self._build_prompt(successful, missing)
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
            text = _finalize(_structured_concatenation(successful, missing))
            return text, SynthesisProvenance(
                contributing_skills=contributing,
                missing_skills=missing_skills,
                degraded=True,
            )

    def _build_prompt(
        self, successful: list[LaneResult], missing: list[LaneResult]
    ) -> list[Message]:
        lane_text = "\n\n".join(_render_lane_block(lane) for lane in successful)
        user_content = f"Lane outputs to merge:\n\n{lane_text}"
        if missing:
            user_content += "\n\n" + _render_missing(missing)
        return [
            Message(role=MessageRole.SYSTEM, content=_SYNTHESIS_SYSTEM_PROMPT),
            Message(role=MessageRole.USER, content=user_content),
        ]


__all__ = ["NoSuccessfulLanesError", "Synthesizer"]
