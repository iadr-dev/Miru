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

import json
import uuid
from dataclasses import dataclass, field
from typing import cast

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent import guardrails
from agent.answer_format import recover_textual_tool_call, strip_tldr
from agent.context_window import compact
from agent.hooks import (
    AnswerDeltaHook,
    CancelToken,
    PostToolHook,
    PreLLMHook,
    PreToolHook,
    SteeringSource,
    ThinkingHook,
    WebSearchHook,
)
from agent.journal import Journal
from agent.prompts import PromptLayers, assemble_system_prompt, default_prompt_layers
from config.settings import Settings, get_settings
from llm.base import (
    ImagePart,
    LLMClient,
    LLMEvent,
    LLMResponse,
    Message,
    MessageRole,
    StopReason,
    StreamDoneEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    TokenUsage,
    ToolCall,
    ToolCallEvent,
    ToolResultPart,
    ToolSpec,
    WebSearchResultEvent,
    WebSearchSource,
)
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


_MAX_VALIDATION_FEEDBACK_CHARS = 700


def _validation_feedback(tool_name: str, exc: ValidationError) -> str:
    """The tool-error text shown to the model for rejected arguments: each offending field with
    pydantic's own message (which names the valid options for an enum), and nothing the model
    sent — `include_input=False` keeps the rejected value out of the feedback."""
    problems: list[str] = []
    for error in exc.errors(include_url=False, include_input=False, include_context=False):
        where = ".".join(str(part) for part in error["loc"]) or "arguments"
        problems.append(f"{where}: {error['msg']}")
    text = (
        f"invalid arguments for {tool_name}: {'; '.join(problems)}. "
        "Fix the arguments and call the tool again."
    )
    return text[:_MAX_VALIDATION_FEEDBACK_CHARS]


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
class ToolCallRecord:
    """One dispatched tool call's redacted trace — tool name plus its already-summarized result
    (never raw arguments/payload, same "provenance not payload" rule `_summarize_result`/`Journal`
    already follow), kept on `TurnResult` so a lane's process trace can be persisted, not just
    streamed live over SSE and then discarded once the turn ends."""

    step_index: int
    tool_name: str
    summary: str


@dataclass(frozen=True)
class TurnResult:
    """The completed Turn: the final caveated text, every Step along the way, any
    structured rich cards built from card-opted-in tool results during the Turn, every
    native-web-search source consulted (accumulated across however many `WebSearchResultEvent`s
    the Turn's Steps streamed) — the same source list `EventBusWebSearchHook` already forwards
    live over SSE, kept here too so the caller (`api.turn_driver._persist_answer`) has something
    to persist once the turn ends; without this the sources only ever existed for the duration
    of the stream, per the bug this field exists to close — and, likewise, every dispatched
    `ToolCallRecord` plus the concatenated genuinely-streamed `thinking_text` for the whole Turn,
    so a fanned-out lane's process trace can be persisted the same way."""

    final_text: str
    steps: tuple[StepResult, ...]
    journal: Journal
    rich_cards: tuple[RichCard, ...] = ()
    web_search_sources: tuple[WebSearchSource, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    thinking_text: str = ""


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
    payload values — for the journal audit trail (`ToolCallRecord`/`Journal.record_tool_call`),
    which by design never carries values (governance §7's audit log is provenance-only). This is
    NOT what the model sees as the tool result; see `_render_result_for_model` for that."""
    field_names = sorted(type(result).model_fields.keys())
    return f"{type(result).__name__}({', '.join(field_names)})"


# A tool result rendered for the model is still bounded — a handful of Sponsor datasets return
# hundreds of rows per call, and an unbounded dump would blow the context-window budget on one
# tool call. This is generous enough for every skill's own row caps (seen empirically: none
# request more than a few hundred rows per call) while still bounding worst case.
_MAX_MODEL_RESULT_CHARS = 20_000


def _render_result_for_model(result: BaseModel) -> str:
    """Render a tool result's actual field VALUES for the model's own tool-result message —
    the same governance-scanned JSON dump `_build_card` already builds for the user-facing
    `RichCard` (`guardrails.scan_structured_payload`), reused here so the model narrates the same
    governed data the card renders, instead of `_summarize_result`'s bare field-name list.

    Without this, the model only ever sees e.g. `"BlockTradeResult(rows, health)"` — the type and
    field names, never a single value — and has no way to accurately describe what it fetched: it
    either guesses or claims the data is missing, while the separately-built `RichCard` (built
    from the same `result` object) renders the real rows to the user. That contradiction — model
    says "no data available", card shows real rows — is exactly the failure this fixes."""
    dumped = result.model_dump(mode="json")
    cleaned, _ = guardrails.scan_structured_payload(dumped)
    text = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
    if len(text) > _MAX_MODEL_RESULT_CHARS:
        text = f"{text[:_MAX_MODEL_RESULT_CHARS]}...<truncated, {len(text)} chars total>"
    return text


@dataclass
class _StepStream:
    """Mutable per-Step state `_stream_step` folds the LLM event stream into."""

    text_parts: list[str] = field(default_factory=list[str])
    tool_calls: list[ToolCall] = field(default_factory=list[ToolCall])
    stop_reason: StopReason = StopReason.END_TURN
    usage: TokenUsage = field(
        default_factory=lambda: TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)
    )
    answer_buffer: str = ""


class AgentLoop:
    """Drives one Turn's Step loop for the `single` execution mode (no orchestrator, no
    sub-agent fan-out — those are later phases' concern)."""

    def __init__(  # noqa: PLR0913 — every constructor arg configures a distinct
        # independently-testable runtime knob (client, tool set, budget knobs, prompt layers,
        # the registrable non-veto hook lists, and the cooperative cancel/steering seams);
        # collapsing them into one options object would obscure each seam's default
        self,
        llm: LLMClient,
        tools: list[Tool],
        *,
        context_window: int | None = None,
        context_reserve: int | None = None,
        max_steps: int | None = None,
        prompt_layers: PromptLayers | None = None,
        settings: Settings | None = None,
        pre_tool_hooks: list[PreToolHook] | None = None,
        post_tool_hooks: list[PostToolHook] | None = None,
        pre_llm_hooks: list[PreLLMHook] | None = None,
        thinking_hooks: list[ThinkingHook] | None = None,
        answer_delta_hooks: list[AnswerDeltaHook] | None = None,
        web_search_hooks: list[WebSearchHook] | None = None,
        hide_cards_for: frozenset[str] = frozenset(),
        steering_source: SteeringSource | None = None,
        cancel_token: CancelToken | None = None,
        native_web_search: bool = False,
    ) -> None:
        settings = settings or get_settings()
        self._llm = llm
        self._tools = tools
        self._tool_specs: list[ToolSpec] = [tool.to_tool_spec() for tool in tools]
        self._context_window = (
            context_window if context_window is not None else settings.context_window_tokens
        )
        self._context_reserve = (
            context_reserve if context_reserve is not None else settings.context_reserve_tokens
        )
        self._max_steps = max_steps if max_steps is not None else settings.agent_max_steps
        self._prompt_layers = prompt_layers or default_prompt_layers()
        self._pre_tool_hooks: list[PreToolHook] = pre_tool_hooks or []
        self._post_tool_hooks: list[PostToolHook] = post_tool_hooks or []
        self._pre_llm_hooks: list[PreLLMHook] = pre_llm_hooks or []
        self._thinking_hooks: list[ThinkingHook] = thinking_hooks or []
        self._answer_delta_hooks: list[AnswerDeltaHook] = answer_delta_hooks or []
        self._web_search_hooks: list[WebSearchHook] = web_search_hooks or []
        self._hide_cards_for = hide_cards_for
        self._steering_source = steering_source
        self._cancel_token = cancel_token
        self._native_web_search = native_web_search

    @property
    def cancel_token(self) -> CancelToken | None:
        """The cooperative cancellation flag polled at every Step boundary. Settable after
        construction so a caller (the interaction-layer turn lifecycle) can attach the live
        turn's token once it exists, rather than requiring it up front."""
        return self._cancel_token

    @cancel_token.setter
    def cancel_token(self, token: CancelToken | None) -> None:
        self._cancel_token = token

    async def run_turn(
        self,
        user_message: str,
        *,
        tool_context: ToolContext,
        images: tuple[ImagePart, ...] = (),
        history: tuple[Message, ...] = (),
    ) -> TurnResult:
        """Run Steps until the model returns a final (non-tool-call) response, the step
        budget is exhausted, or a proposed tool call is vetoed by governance. `images` attaches
        to the initial user message only (a user can attach an image alongside their own text
        in the same turn, per `Message`'s own docstring) — never re-sent on later Steps.

        `history` is prior turns from the same thread (oldest first), spliced in between the
        system prompt and the current turn's user message — without it, a follow-up like "what
        about its dividend history?" has no way to resolve "its" to a symbol mentioned 2 turns
        ago, since each Turn otherwise starts from a blank slate. It flows through the same
        `_transform_context`/`compact` seam as every other message on this Step loop (called
        below, before the first LLM call), so a long thread's history is compacted under the
        loop's existing context-window budget rather than needing a separate cap."""
        journal = Journal()
        messages: list[Message] = [
            Message(role=MessageRole.SYSTEM, content=assemble_system_prompt(self._prompt_layers)),
            *history,
            Message(role=MessageRole.USER, content=user_message, images=images),
        ]
        step_context = tool_context.model_copy(update={"journal": journal})

        steps: list[StepResult] = []
        rich_cards: list[RichCard] = []
        web_search_sources: list[WebSearchSource] = []
        tool_calls: list[ToolCallRecord] = []
        thinking_parts: list[str] = []
        for step_index in range(self._max_steps):
            if self._cancel_token is not None and self._cancel_token.is_cancelled():
                raise TurnCancelledError(
                    f"turn cancelled before step {step_index} (cancel_token flipped)"
                )
            messages = self._inject_steering(messages)
            messages = await self._transform_context(messages)
            messages = await self._run_pre_llm_hooks(messages)
            response = await self._stream_step(
                messages, web_search_sources=web_search_sources, thinking_parts=thinking_parts
            )

            if not response.tool_calls:
                final_text = self._finalize_output(
                    response.text or "", journal=journal, step_index=step_index
                )
                steps.append(StepResult(step_index=step_index, response=response))
                return TurnResult(
                    final_text=final_text,
                    steps=tuple(steps),
                    journal=journal,
                    rich_cards=tuple(rich_cards),
                    web_search_sources=tuple(web_search_sources),
                    tool_calls=tuple(tool_calls),
                    thinking_text="".join(thinking_parts),
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
                tool_calls.append(
                    ToolCallRecord(
                        step_index=step_index, tool_name=call.name, summary=result_part.content
                    )
                )
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

    async def _run_thinking_hooks(self, text: str) -> None:
        for hook in self._thinking_hooks:
            await hook(text=text)

    async def _run_answer_delta_hooks(self, text: str) -> None:
        for hook in self._answer_delta_hooks:
            await hook(text=text)

    async def _run_pre_tool_hooks(self, *, step_index: int, tool_name: str) -> None:
        for hook in self._pre_tool_hooks:
            await hook(step_index=step_index, tool_name=tool_name)

    async def _run_web_search_hooks(self, event: WebSearchResultEvent) -> None:
        for hook in self._web_search_hooks:
            await hook(sources=event.sources)

    async def _stream_step(
        self,
        messages: list[Message],
        *,
        web_search_sources: list[WebSearchSource],
        thinking_parts: list[str],
    ) -> LLMResponse:
        """Drive one Step via `llm.stream()` rather than `chat()` — the live agent-turn path,
        so a genuinely reasoning-capable model's `ThinkingDeltaEvent`s can be forwarded to the
        `thinking` hooks (and, through them, the SSE `thinking.delta` frame) as they arrive, not
        buffered until the Step completes.

        `TextDeltaEvent`s are buffered sentence-by-sentence (`guardrails.
        split_sentences_streaming` — the incremental, nothing-discarded counterpart to
        `check_output`'s own one-shot `split_sentences`; using the strip-based one here would
        silently eat word-boundary whitespace on the still-growing tail) as they arrive: each
        COMPLETE chunk is run through `guardrails.scan_sentence` and forwarded live to the
        `answer_delta` hooks the instant it closes — the withheld trailing fragment (not yet
        terminated, so it might still grow) is carried into the next delta rather than emitted
        early. This is what makes the final answer genuinely stream token-by-token to the user
        (the SSE `message.delta` frame) while still guaranteeing no unscanned text is ever
        shown: the governance scan runs per-chunk as chunks close, not once on the whole
        buffered answer at the end. The whole-answer `check_output` pass in `_finalize_output`
        still runs too, unconditionally — this streaming path only ADDS a live preview, it does
        not replace the authoritative final scan (which also owns the caveat-append step, a
        whole-answer concern with no per-chunk equivalent).

        This streams for every Step, not only a Step confirmed to end without tool calls — by
        the time a `TextDeltaEvent` arrives there is no way to know yet whether this Step will
        also propose a tool call (some models emit a short preamble before deciding to call a
        tool). A Step that turns out to carry both text and tool calls means that preamble
        streamed live but is not the Turn's `final_text` (a pre-existing behavior — see
        `run_turn`: only a Step with zero tool_calls resolves the Turn); this is an accepted,
        bounded cost (matches how other coding-agent products stream this same case), not a
        governance gap, since the streamed text was scanned exactly like any other answer text.

        Every thinking chunk is passed through `guardrails.check_output` before it reaches a
        `thinking` hook: it is model-generated text like any other, and the non-trading scan
        that gates the final answer is not narrowly scoped to "the last message" — a
        buy/sell/hold signal leaking mid-reasoning is exactly the kind of output that hook
        exists to catch, so exempting thinking text would be a governance gap, not a
        performance optimization worth making.

        `web_search_sources`/`thinking_parts` are the caller's (`run_turn`'s) turn-scoped
        accumulators — every `WebSearchResultEvent`/redacted thinking chunk this Step streams
        appends here, in addition to firing the observe-only `web_search`/`thinking` hooks, so
        `TurnResult.web_search_sources`/`thinking_text` reflect the whole Turn (not just each
        hook's live SSE echo, which nothing durable ever re-reads once the turn ends)."""
        acc = _StepStream()
        async for event in self._llm.stream(
            messages, tools=self._tool_specs, native_web_search=self._native_web_search
        ):
            await self._consume_event(
                acc, event, web_search_sources=web_search_sources, thinking_parts=thinking_parts
            )
        if (
            not acc.tool_calls
            and (call := self._recovered_text_call("".join(acc.text_parts))) is not None
        ):
            # The model wrote its tool call out as text: run it as the call it was meant to be and
            # drop the raw JSON from the reply.
            acc.tool_calls.append(call)
            acc.text_parts.clear()
            acc.answer_buffer = ""
            acc.stop_reason = StopReason.TOOL_USE
        # The stream ended with a trailing fragment still held back (either a genuinely final
        # sentence with no more text coming, or a short answer with no terminator at all, e.g.
        # "好的") — flush it now rather than losing it, since no further delta will ever close it.
        trailing = guardrails.scan_sentence(acc.answer_buffer)
        if trailing:
            await self._run_answer_delta_hooks(trailing)
        return LLMResponse(
            text="".join(acc.text_parts) or None,
            tool_calls=tuple(acc.tool_calls),
            stop_reason=acc.stop_reason,
            usage=acc.usage.model_copy(
                update={"provider": self._llm.provider_id, "model": self._llm.model_id}
            ),
        )

    async def _consume_event(
        self,
        acc: _StepStream,
        event: LLMEvent,
        *,
        web_search_sources: list[WebSearchSource],
        thinking_parts: list[str],
    ) -> None:
        if isinstance(event, TextDeltaEvent):
            await self._on_text_delta(acc, event)
        elif isinstance(event, ThinkingDeltaEvent):
            await self._on_thinking_delta(event, thinking_parts)
        elif isinstance(event, ToolCallEvent):
            acc.tool_calls.append(event.tool_call)
        elif isinstance(event, WebSearchResultEvent):
            web_search_sources.extend(event.sources)
            await self._run_web_search_hooks(event)
        elif isinstance(event, StreamDoneEvent):
            acc.stop_reason = event.stop_reason
            acc.usage = event.usage
        # `ToolCallDeltaEvent` — the incrementally-assembled tool-call fragment every
        # streaming client also emits before its final `ToolCallEvent` — carries nothing
        # this Step needs; the client itself already assembles the complete `ToolCallEvent`
        # this loop consumes above, so the fragment is intentionally a no-op here.

    async def _on_text_delta(self, acc: _StepStream, event: TextDeltaEvent) -> None:
        acc.text_parts.append(event.text)
        acc.answer_buffer += event.text
        chunks = guardrails.split_sentences_streaming(acc.answer_buffer)
        if len(chunks) > 1:
            *complete, acc.answer_buffer = chunks
            for chunk in complete:
                safe_chunk = guardrails.scan_sentence(chunk)
                if safe_chunk:
                    await self._run_answer_delta_hooks(safe_chunk)

    async def _on_thinking_delta(
        self, event: ThinkingDeltaEvent, thinking_parts: list[str]
    ) -> None:
        verdict = guardrails.check_output(event.text, has_market_data=False)
        safe_text = event.text if verdict.redacted_text is None else verdict.redacted_text
        if safe_text:
            thinking_parts.append(safe_text)
            await self._run_thinking_hooks(safe_text)

    def _recovered_text_call(self, text: str) -> ToolCall | None:
        recovered = recover_textual_tool_call(text, {tool.name for tool in self._tools})
        if recovered is None:
            return None
        name, arguments = recovered
        return ToolCall(id=f"recovered-{uuid.uuid4().hex[:12]}", name=name, arguments=arguments)

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
        await self._run_pre_tool_hooks(step_index=step_index, tool_name=tool_name)
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

        try:
            args = tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            # A model that mistypes one argument (a wrong enum value, a missing field) must be told
            # and given the chance to retry — not have its whole turn killed by an exception. The
            # feedback names the field and the valid options but never echoes the rejected value.
            feedback = _validation_feedback(tool_name, exc)
            journal.record_tool_call(
                step_index=step_index,
                tool_name=tool_name,
                arguments_keys=argument_keys,
                result_summary="invalid arguments",
                guardrail_allowed=True,
                guardrail_reason=None,
            )
            await self._run_post_tool_hooks(
                step_index=step_index,
                tool_name=tool_name,
                argument_keys=argument_keys,
                result_summary="invalid arguments",
                guardrail_allowed=True,
                guardrail_reason=None,
            )
            return ToolResultPart(tool_call_id=tool_call_id, content=feedback, is_error=True), None
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
        # The journal/hooks get the field-name-only `summary` (provenance, not payload — an
        # audit trail never carries values); the model gets the real governed values via
        # `_render_result_for_model` so it can accurately narrate what it fetched instead of
        # guessing blind. A masked result has no values to render (`MaskedTeaser` is
        # payload-free by construction), so it keeps the summary form either way.
        model_content = summary if masked else _render_result_for_model(result)
        return ToolResultPart(tool_call_id=tool_call_id, content=model_content), card

    def _card_for(self, tool: Tool, result: BaseModel) -> RichCard | None:
        """Build a governed `RichCard` for a card-opted-in tool's dispatched result — `None`
        for the vast majority of tools that haven't opted in (`Tool.card_type` stays `None`)."""
        card_type = getattr(tool, "card_type", None)
        if card_type is None or tool.name in self._hide_cards_for:
            return None
        return _build_card(card_type, result)

    def _finalize_output(self, text: str, *, journal: Journal, step_index: int) -> str:
        """The mandatory `pre_output` hook — runs on every final answer with no
        config path to skip it."""
        text = strip_tldr(text)
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
