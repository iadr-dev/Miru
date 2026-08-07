"""The Step loop: `step_start -> transform_context -> convert_to_llm -> llm.chat ->
hooks(pre_tool/dispatch/post_tool) -> hooks(pre_output) -> step_end`, repeating until a final
caveated answer.

Vocabulary: a **Turn** is one user request -> one agent response cycle (`AgentLoop.run_turn`);
a **Step** is one LLM round plus the tool calls/results it triggers — the inner unit this
module iterates. `AgentLoop` composes:

- `agent.context_window` for `transform_context` (compaction over the internal `Message`
  layer, applied before every LLM call once the running transcript is built).
- `agent.prompts.assemble_system_prompt` for the fixed governance-first system prompt.
- `llm.base.LLMClient` for `convert_to_llm`/`llm.chat` (the adapter itself owns the
  provider-payload conversion; this loop only ever deals in the normalized `Message`/
  `LLMResponse` types).
- `agent.guardrails.check_tool_call`/`check_output` as the mandatory, non-skippable
  `pre_tool`/`pre_output` hooks.
- `tools.base.dispatch_tool` for every tool execution — this loop never calls a `Tool.run()`
  directly, so the `sponsor` entitlement gate inside `dispatch_tool` always fires alongside
  the (separate) governance hook.
- `agent.journal.Journal` for the per-Step audit trail, wired into `ToolContext.journal`.
- `agent.hooks.PostToolHook`/`PreLLMHook`/`CancelToken`/`SteeringSource` — the registrable,
  non-veto extension seams a higher layer can plug into without this module ever importing that
  layer (see `agent/hooks.py`'s docstring for why these are separate from the mandatory
  governance hooks).

What this phase deliberately does not build (later phases' job, stubbed/skipped cleanly, not
half-built): orchestrator intent classification (`run_turn` is invoked directly with a fixed
tool set, not selected by a classifier), conversation/thread persistence (the caller owns
`messages: list[Message]` in memory only), SSE streaming (the loop returns a completed
`TurnResult`, it does not emit events).

Mid-run steering: a new user message *can* now join an in-flight Turn, but only at a clean Step
boundary, never mid-Step — the same "redirect, don't cancel" boundary discipline `CancelToken`
already uses. `run_turn`'s per-Step loop polls the optional `steering_source` at `step_start`
(before `transform_context`), folds any queued message(s) in as new user-role turns, and moves
on; a Step already dispatching tool calls is never interrupted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agent import guardrails
from agent.context_window import compact
from agent.hooks import CancelToken, PostToolHook, PreLLMHook, SteeringSource
from agent.journal import Journal
from agent.prompts import PromptLayers, assemble_system_prompt, default_prompt_layers
from config.settings import Settings, get_settings
from llm.base import LLMClient, LLMResponse, Message, MessageRole, ToolResultPart, ToolSpec
from tools.base import MaskedTeaser, Tool, ToolContext, dispatch_tool

logger = structlog.get_logger(__name__)

_CARD_CAVEAT_KEY = "non_trading_caveat"


class RichCard(BaseModel):
    """A structured UI card derived from one card-opted-in tool's typed output — the frontend
    renders it as a chart/table/gauge/comparison rather than prose.

    `data` is the tool result's JSON dump after governance scanning, carrying the standard
    non-trading caveat so a card is held to the same non-advice discipline as prose text. It
    never carries a raw source payload or a masked (`sponsor`, unentitled) result — see how it
    is built in `AgentLoop._dispatch_one`, strictly after the entitlement gate has already run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    card_type: str = Field(min_length=1)
    data: dict[str, object] = Field(default_factory=dict)


def _build_card(card_type: str, result: BaseModel) -> RichCard:
    """Render a card-opted tool's typed output into a governed `RichCard`.

    The result is dumped to a JSON-safe dict, every text-bearing value is scanned for trading
    language and redacted in place, and the whole card is stamped with the non-trading caveat —
    the structured analogue of the prose `check_output` pass, since a card has no single prose
    string to append a caveat sentence to.
    """
    dumped = result.model_dump(mode="json")
    cleaned, _ = guardrails.scan_structured_payload(dumped)
    data: dict[str, object] = (
        dict(cast("dict[str, object]", cleaned))
        if isinstance(cleaned, dict)
        else {"value": cleaned}
    )
    data[_CARD_CAVEAT_KEY] = guardrails.NON_TRADING_CAVEAT
    return RichCard(card_type=card_type, data=data)


@dataclass(frozen=True)
class StepResult:
    """One completed Step's outcome — kept for callers that want the per-Step trail, not
    just the final answer."""

    step_index: int
    response: LLMResponse
    tool_messages: tuple[Message, ...] = ()


@dataclass(frozen=True)
class TurnResult:
    """The completed Turn: the final caveated text, every Step along the way, and any
    structured rich cards built from card-opted-in tool results during the Turn."""

    final_text: str
    steps: tuple[StepResult, ...]
    journal: Journal
    rich_cards: tuple[RichCard, ...] = ()


class StepBudgetExceededError(RuntimeError):
    """Raised when a Turn exhausts its step budget without reaching a final answer — a
    bounded loop, never an unattended one, per the no-autonomy-over-the-network rule."""


class TurnCancelledError(RuntimeError):
    """Raised when `cancel_token.is_cancelled()` is true at a Step boundary. The Step that was
    already in flight (its tool calls) is allowed to finish first — this is only checked
    between Steps, never mid-dispatch."""


def _tool_by_name(tools: list[Tool], name: str) -> Tool | None:
    for tool in tools:
        if tool.name == name:
            return tool
    return None


def _summarize_result(result: BaseModel) -> str:
    """Reduce any tool result to its type name plus top-level field names — never raw
    payload values — for the journal and for what gets reported back to the model as a tool
    result. A `MaskedTeaser` is already payload-free by construction."""
    field_names = sorted(type(result).model_fields.keys())
    return f"{type(result).__name__}({', '.join(field_names)})"


class AgentLoop:
    """Drives one Turn's Step loop for the `single` execution mode (no orchestrator, no
    sub-agent fan-out — those are later phases' concern)."""

    def __init__(  # noqa: PLR0913 — every constructor arg configures a distinct,
        # independently-testable runtime knob (client, tool set, three budget knobs, prompt
        # layers); collapsing them into one options object would obscure each seam's default
        self,
        llm: LLMClient,
        tools: list[Tool],
        *,
        context_window: int | None = None,
        context_reserve: int | None = None,
        max_steps: int | None = None,
        prompt_layers: PromptLayers | None = None,
        settings: Settings | None = None,
        post_tool_hooks: list[PostToolHook] | None = None,
        pre_llm_hooks: list[PreLLMHook] | None = None,
        cancel_token: CancelToken | None = None,
        steering_source: SteeringSource | None = None,
    ) -> None:
        settings = settings or get_settings()
        self._llm = llm
        self._tools = tools
        self._tool_specs: list[ToolSpec] = [tool.to_tool_spec() for tool in tools]
        self._context_window = (
            context_window if context_window is not None else settings.aitw_context_window_tokens
        )
        self._context_reserve = (
            context_reserve if context_reserve is not None else settings.aitw_context_reserve_tokens
        )
        self._max_steps = max_steps if max_steps is not None else settings.aitw_agent_max_steps
        self._prompt_layers = prompt_layers or default_prompt_layers()
        # Dependency-inverted extension seams (see `agent.hooks`) — registered from outside
        # `agent/` (application-wiring time), never imported here beyond the Protocol shapes.
        self._post_tool_hooks = post_tool_hooks or []
        self._pre_llm_hooks = pre_llm_hooks or []
        self._cancel_token = cancel_token
        self._steering_source = steering_source

    @property
    def cancel_token(self) -> CancelToken | None:
        return self._cancel_token

    @cancel_token.setter
    def cancel_token(self, token: CancelToken | None) -> None:
        """Lets a caller that constructs one `AgentLoop` per turn-lifecycle wiring (rather
        than per turn) hand it a fresh per-turn `CancelToken` after construction — see
        `conversation.turn_lifecycle.TurnLifecycle.start`."""
        self._cancel_token = token

    @property
    def steering_source(self) -> SteeringSource | None:
        return self._steering_source

    @steering_source.setter
    def steering_source(self, source: SteeringSource | None) -> None:
        """Mirrors the `cancel_token` setter: a caller that builds one `AgentLoop` ahead of the
        turn's own live handle (the turn registry entry doesn't exist until the turn is
        registered) attaches the real queue right after construction."""
        self._steering_source = source

    async def run_turn(self, user_message: str, *, tool_context: ToolContext) -> TurnResult:
        """Run Steps until the model returns a final (non-tool-call) response, the step
        budget is exhausted, a proposed tool call is vetoed by governance, or the caller's
        `cancel_token` is flipped between Steps."""
        journal = Journal()
        messages: list[Message] = [
            Message(role=MessageRole.SYSTEM, content=assemble_system_prompt(self._prompt_layers)),
            Message(role=MessageRole.USER, content=user_message),
        ]
        step_context = tool_context.model_copy(update={"journal": journal})

        steps: list[StepResult] = []
        rich_cards: list[RichCard] = []
        for step_index in range(self._max_steps):
            if self._cancel_token is not None and self._cancel_token.is_cancelled():
                raise TurnCancelledError(f"turn cancelled before step {step_index}")
            messages = self._inject_steering(messages)
            messages = await self._transform_context(messages)
            messages = await self._run_pre_llm_hooks(messages)
            response = await self._llm.chat(messages, tools=self._tool_specs)

            if not response.tool_calls:
                final_text = await self._finalize_output(
                    response.text or "", journal=journal, step_index=step_index
                )
                steps.append(StepResult(step_index=step_index, response=response))
                return TurnResult(
                    final_text=final_text,
                    steps=tuple(steps),
                    journal=journal,
                    rich_cards=tuple(rich_cards),
                )

            assistant_tool_calls = tuple(response.tool_calls)
            messages.append(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=response.text,
                    tool_calls=assistant_tool_calls,
                )
            )

            tool_result_messages: list[Message] = []
            for call in assistant_tool_calls:
                result_part, card = await self._dispatch_one(
                    call.id,
                    call.name,
                    call.arguments,
                    ctx=step_context,
                    journal=journal,
                    step_index=step_index,
                )
                if card is not None:
                    rich_cards.append(card)
                tool_result_messages.append(
                    Message(role=MessageRole.TOOL, tool_results=(result_part,))
                )
            messages.extend(tool_result_messages)
            steps.append(
                StepResult(
                    step_index=step_index,
                    response=response,
                    tool_messages=tuple(tool_result_messages),
                )
            )

        raise StepBudgetExceededError(
            f"turn exceeded its step budget ({self._max_steps}) without a final answer"
        )

    def _inject_steering(self, messages: list[Message]) -> list[Message]:
        """The `step_start` steering-injection point: drain whatever the caller's live handle
        queued via `POST .../steer` since the last Step and fold it into the active conversation
        as new user-role message(s) — never merged into the system prompt, so a steering message
        is exactly as untrusted-content-is-data as any other user turn (governance's `pre_output`
        scan still runs on whatever answer follows).

        Only ever called at a Step boundary (here, before that Step's `transform_context`), so a
        Step already in flight is never redirected mid-dispatch — the same boundary discipline
        `cancel_token` uses. A drained-but-empty queue (no `steering_source`, or nothing queued)
        is a no-op; `drain()` clears what it returns, so a message is folded in exactly once even
        if this turn runs many more Steps afterward."""
        if self._steering_source is None:
            return messages
        pending = self._steering_source.drain()
        if not pending:
            return messages
        return [
            *messages,
            *(Message(role=MessageRole.USER, content=text) for text in pending),
        ]

    async def _transform_context(self, messages: list[Message]) -> list[Message]:
        """Compaction over the internal `Message` layer — the runtime's `transform_context`
        seam. Memory recall and branch summaries are later-phase registered handlers on this
        same seam; none are registered yet, so this only compacts."""
        return await compact(
            messages,
            self._llm,
            window=self._context_window,
            reserve=self._context_reserve,
        )

    async def _run_pre_llm_hooks(self, messages: list[Message]) -> list[Message]:
        """Run every registered `pre_llm` hook in order, e.g. memory-block injection. Hooks
        never veto; each returns the (possibly extended) message list for the next hook."""
        for hook in self._pre_llm_hooks:
            messages = await hook(messages=messages)
        return messages

    async def _run_post_tool_hooks(  # noqa: PLR0913 — mirrors `PostToolHook.__call__`'s shape
        self,
        *,
        step_index: int,
        tool_name: str,
        argument_keys: tuple[str, ...],
        result_summary: str,
        guardrail_allowed: bool,
        guardrail_reason: str | None,
    ) -> None:
        for hook in self._post_tool_hooks:
            await hook(
                step_index=step_index,
                tool_name=tool_name,
                argument_keys=argument_keys,
                result_summary=result_summary,
                guardrail_allowed=guardrail_allowed,
                guardrail_reason=guardrail_reason,
            )

    async def _dispatch_one(  # noqa: PLR0913 — internal helper threading the per-call
        # identifiers plus the journal/step context through one dispatch; splitting it up
        # would just move the same arguments into a throwaway container
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, object],
        *,
        ctx: ToolContext,
        journal: Journal,
        step_index: int,
    ) -> tuple[ToolResultPart, RichCard | None]:
        argument_keys = tuple(sorted(arguments.keys()))
        pre_tool_verdict = guardrails.check_tool_call(tool_name, arguments)
        if not pre_tool_verdict.allowed:
            journal.record_tool_call(
                step_index=step_index,
                tool_name=tool_name,
                arguments_keys=argument_keys,
                result_summary="blocked before dispatch",
                guardrail_allowed=False,
                guardrail_reason=pre_tool_verdict.reason,
            )
            await self._run_post_tool_hooks(
                step_index=step_index,
                tool_name=tool_name,
                argument_keys=argument_keys,
                result_summary="blocked before dispatch",
                guardrail_allowed=False,
                guardrail_reason=pre_tool_verdict.reason,
            )
            return (
                ToolResultPart(
                    tool_call_id=tool_call_id,
                    content=f"blocked: {pre_tool_verdict.reason}",
                    is_error=True,
                ),
                None,
            )

        tool = _tool_by_name(self._tools, tool_name)
        if tool is None:
            journal.record_tool_call(
                step_index=step_index,
                tool_name=tool_name,
                arguments_keys=argument_keys,
                result_summary="unknown tool",
                guardrail_allowed=True,
                guardrail_reason=None,
            )
            await self._run_post_tool_hooks(
                step_index=step_index,
                tool_name=tool_name,
                argument_keys=argument_keys,
                result_summary="unknown tool",
                guardrail_allowed=True,
                guardrail_reason=None,
            )
            return (
                ToolResultPart(
                    tool_call_id=tool_call_id, content=f"unknown tool: {tool_name}", is_error=True
                ),
                None,
            )

        args = tool.input_model.model_validate(arguments)
        result = await dispatch_tool(tool, args, ctx)
        summary = _summarize_result(result)
        masked = isinstance(result, MaskedTeaser)
        guardrail_reason = "masked_teaser" if masked else None
        # A card is built strictly here, on the *dispatched* result — after `dispatch_tool`'s
        # entitlement gate has already substituted a `MaskedTeaser` for an unentitled `sponsor`
        # call. A masked result never becomes a card (the teaser carries the upgrade CTA, not
        # the underlying data), so an unentitled caller can never receive real sponsor data in a
        # card, only in the separately-evented locked-teaser shape.
        card = self._card_for(tool, result) if not masked else None
        journal.record_tool_call(
            step_index=step_index,
            tool_name=tool_name,
            arguments_keys=argument_keys,
            result_summary=summary,
            guardrail_allowed=True,
            guardrail_reason=guardrail_reason,
        )
        await self._run_post_tool_hooks(
            step_index=step_index,
            tool_name=tool_name,
            argument_keys=argument_keys,
            result_summary=summary,
            guardrail_allowed=True,
            guardrail_reason=guardrail_reason,
        )
        return ToolResultPart(tool_call_id=tool_call_id, content=summary), card

    @staticmethod
    def _card_for(tool: Tool, result: BaseModel) -> RichCard | None:
        """Build a governed `RichCard` for a card-opted-in tool, or `None` for a tool that
        hasn't opted in. `card_type` defaults to `None` on every tool, so this is a no-op
        unless a tool explicitly declares a card shape."""
        card_type = getattr(tool, "card_type", None)
        if not card_type:
            return None
        return _build_card(card_type, result)

    async def _finalize_output(self, text: str, *, journal: Journal, step_index: int) -> str:
        """The mandatory `pre_output` hook — runs on every final answer with no
        config path to skip it."""
        verdict = guardrails.check_output(text)
        journal.record_output_guardrail(
            step_index=step_index, allowed=verdict.allowed, reason=verdict.reason
        )
        return verdict.redacted_text if verdict.redacted_text is not None else text


__all__ = [
    "AgentLoop",
    "RichCard",
    "StepBudgetExceededError",
    "StepResult",
    "TurnCancelledError",
    "TurnResult",
]
