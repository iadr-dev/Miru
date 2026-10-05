"""`POST /agent/query` with `model` persists it as the thread override before the turn runs."""

from __future__ import annotations

from api.routers.agent import AgentQueryRequest


def test_agent_query_request_accepts_optional_model() -> None:
    assert AgentQueryRequest(message="hi").model is None
    assert AgentQueryRequest(message="hi", model="openrouter/openrouter/auto").model == (
        "openrouter/openrouter/auto"
    )
