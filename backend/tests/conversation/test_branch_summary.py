"""`summarize_branch` — structured decision+cause summarization of an abandoned branch, via a
cheap-role `LLMClient`, tested against a `StubLLMClient` script."""

from __future__ import annotations

from datetime import UTC, datetime

from conversation.branch_summary import summarize_branch
from conversation.models import Message as ConversationMessage
from conversation.models import MessageBlockType, TextBlock
from tests.llm.stubs import StubLLMClient, make_text_response


def _text_message(text: str) -> ConversationMessage:
    return ConversationMessage(
        id="m1",
        turn_id="t1",
        user_id="u1",
        parent_id=None,
        seq=0,
        type=MessageBlockType.TEXT,
        content=TextBlock(text=text),
        created_at=datetime.now(UTC),
    )


async def test_summarize_branch_parses_structured_json_response() -> None:
    llm = StubLLMClient(
        script=[
            make_text_response(
                '{"approach": "checked 20-day institutional flow", "outcome": "failed", '
                '"cause": "foreign net-sell dominated, no clear signal", '
                '"key_figures": ["2330", "-3.2B"]}'
            )
        ]
    )
    messages = [_text_message("checking institutional flow for 2330")]

    summary = await summarize_branch(messages, llm, source_ref="turn-1")

    assert summary.approach == "checked 20-day institutional flow"
    assert summary.outcome == "failed"
    assert summary.cause == "foreign net-sell dominated, no clear signal"
    assert summary.key_figures == ("2330", "-3.2B")
    assert summary.source_ref == "turn-1"


async def test_summarize_branch_falls_back_gracefully_on_unparseable_response() -> None:
    llm = StubLLMClient(script=[make_text_response("not json at all")])
    messages = [_text_message("some branch content")]

    summary = await summarize_branch(messages, llm)

    assert summary.outcome == "tried"
    assert summary.approach
    assert summary.cause


async def test_summarize_branch_rejects_invalid_outcome_value() -> None:
    llm = StubLLMClient(
        script=[
            make_text_response(
                '{"approach": "x", "outcome": "you should buy", "cause": "y", "key_figures": []}'
            )
        ]
    )
    messages = [_text_message("branch content")]

    summary = await summarize_branch(messages, llm)

    assert summary.outcome == "tried"  # invalid enum value falls back, never passed through


async def test_summarize_branch_sends_the_branch_transcript_to_the_llm() -> None:
    llm = StubLLMClient(script=[make_text_response("{}")])
    messages = [_text_message("unique-marker-content")]

    await summarize_branch(messages, llm)

    sent_messages, _tools = llm.calls[0]
    assert any("unique-marker-content" in (m.content or "") for m in sent_messages)
