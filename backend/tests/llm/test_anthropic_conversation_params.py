"""Characterization of `_conversation_params`: the exact `system` text and `messages` array
(prompt-cache breakpoints, per-turn memory placement) for each branch."""

from __future__ import annotations

from llm.anthropic_client import _conversation_params
from llm.base import Message, MessageRole

_EPH = {"type": "ephemeral"}
_INLINE = "claude-sonnet-5-5"  # accepts a mid-conversation system message
_NO_INLINE = "claude-sonnet-4-6"


def _msg(role: MessageRole, content: str, *, per_turn: bool = False) -> Message:
    return Message(role=role, content=content, per_turn=per_turn)


def test_no_messages_means_no_system_and_no_params() -> None:
    assert _conversation_params([], _INLINE) == (None, [])


def test_system_only_has_text_but_no_params_and_no_breakpoint() -> None:
    messages = [_msg(MessageRole.SYSTEM, "a"), _msg(MessageRole.SYSTEM, "b")]

    assert _conversation_params(messages, _INLINE) == ("a\n\nb", [])


def test_empty_system_messages_are_dropped() -> None:
    messages = [_msg(MessageRole.SYSTEM, ""), _msg(MessageRole.USER, "q")]

    system, params = _conversation_params(messages, _INLINE)

    assert system is None
    assert params == [
        {"role": "user", "content": [{"type": "text", "text": "q", "cache_control": _EPH}]}
    ]


def test_without_a_user_message_the_per_turn_block_stays_in_system() -> None:
    messages = [
        _msg(MessageRole.SYSTEM, "stable"),
        _msg(MessageRole.SYSTEM, "memory", per_turn=True),
        _msg(MessageRole.ASSISTANT, "hello"),
    ]

    system, params = _conversation_params(messages, _INLINE)

    assert system == "stable\n\nmemory"
    assert params == [
        {"role": "assistant", "content": [{"type": "text", "text": "hello", "cache_control": _EPH}]}
    ]


def test_per_turn_block_after_the_last_message_inline_has_a_single_breakpoint() -> None:
    messages = [
        _msg(MessageRole.SYSTEM, "stable"),
        _msg(MessageRole.SYSTEM, "m1", per_turn=True),
        _msg(MessageRole.SYSTEM, "m2", per_turn=True),
        _msg(MessageRole.USER, "q"),
    ]

    system, params = _conversation_params(messages, _INLINE)

    assert system == "stable"
    assert params == [
        {"role": "user", "content": [{"type": "text", "text": "q", "cache_control": _EPH}]},
        {"role": "system", "content": "m1\n\nm2"},
    ]


def test_per_turn_block_trails_the_question_when_the_role_is_rejected() -> None:
    messages = [
        _msg(MessageRole.SYSTEM, "m", per_turn=True),
        _msg(MessageRole.USER, "q"),
    ]

    system, params = _conversation_params(messages, _NO_INLINE)

    assert system is None
    assert params == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "q", "cache_control": _EPH},
                {"type": "text", "text": "m"},
            ],
        }
    ]


def test_a_later_message_gets_its_own_breakpoint_when_memory_is_present() -> None:
    messages = [
        _msg(MessageRole.USER, "q1"),
        _msg(MessageRole.ASSISTANT, "a1"),
        _msg(MessageRole.SYSTEM, "m", per_turn=True),
        _msg(MessageRole.USER, "q2"),
        _msg(MessageRole.ASSISTANT, "a2"),
    ]

    system, params = _conversation_params(messages, _INLINE)

    assert system is None
    assert params == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": [{"type": "text", "text": "q2", "cache_control": _EPH}]},
        {"role": "system", "content": "m"},
        {"role": "assistant", "content": [{"type": "text", "text": "a2", "cache_control": _EPH}]},
    ]


def test_the_same_layout_without_the_system_role_appends_the_memory_to_the_question() -> None:
    messages = [
        _msg(MessageRole.USER, "q1"),
        _msg(MessageRole.SYSTEM, "m", per_turn=True),
        _msg(MessageRole.ASSISTANT, "a1"),
    ]

    system, params = _conversation_params(messages, _NO_INLINE)

    assert system is None
    assert params == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "q1", "cache_control": _EPH},
                {"type": "text", "text": "m"},
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "a1", "cache_control": _EPH}]},
    ]


def test_without_per_turn_memory_only_the_last_message_is_a_breakpoint() -> None:
    messages = [
        _msg(MessageRole.SYSTEM, "s"),
        _msg(MessageRole.USER, "q1"),
        _msg(MessageRole.ASSISTANT, "a1"),
        _msg(MessageRole.USER, "q2"),
    ]

    system, params = _conversation_params(messages, _INLINE)

    assert system == "s"
    assert params == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": [{"type": "text", "text": "q2", "cache_control": _EPH}]},
    ]
