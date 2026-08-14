"""`locale` field defaults on the request models that thread it into the orchestrator's
governance system prompt — pure Pydantic model tests, no HTTP/DB/live server needed."""

from __future__ import annotations

from api.routers.agent import AgentQueryRequest
from api.routers.threads_actions import EditTurnRequest, RegenerateRequest


def test_agent_query_request_locale_defaults_to_zh_tw() -> None:
    body = AgentQueryRequest(message="hello")

    assert body.locale == "zh-TW"


def test_agent_query_request_locale_accepts_explicit_value() -> None:
    body = AgentQueryRequest(message="hello", locale="en")

    assert body.locale == "en"


def test_regenerate_request_locale_defaults_to_zh_tw() -> None:
    assert RegenerateRequest().locale == "zh-TW"


def test_edit_turn_request_locale_defaults_to_zh_tw() -> None:
    body = EditTurnRequest(message="hello")

    assert body.locale == "zh-TW"
