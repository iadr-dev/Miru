"""Connect the research ports to the real LLM client, the web gateway and the governance guard."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

import structlog
from pydantic import BaseModel, ValidationError

from dataaccess.web_search import WebSearchGateway
from governance.textscan import check_output
from llm.base import LLMClient, Message, MessageRole, TokenUsage
from research.ports import FetchedPage, SearchHit
from schemas.enums import TLSPolicy
from schemas.source import HealthStatus, SourceResult

logger = structlog.get_logger(__name__)

UsageSink = Callable[[TokenUsage], None]


def _json_object(text: str) -> str:
    """The outermost {...} in a model reply, tolerating prose or a code fence around it."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the reply")
    return text[start : end + 1]


class LLMResearchModel:
    """`ResearchModel` over any `LLMClient`: ask for one JSON object, validate it, retry once."""

    def __init__(self, client: LLMClient, *, on_usage: UsageSink | None = None) -> None:
        self._client = client
        self._on_usage = on_usage

    async def generate[T: BaseModel](self, *, system: str, user: str, schema: type[T]) -> T:
        contract = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        messages = [
            Message(
                role=MessageRole.SYSTEM,
                content=f"{system}\n\nReply with ONE JSON object and nothing else, matching this "
                f"JSON schema:\n{contract}",
            ),
            Message(role=MessageRole.USER, content=user),
        ]
        last_error: Exception | None = None
        for _ in range(2):
            response = await self._client.chat(messages)
            if self._on_usage is not None:
                self._on_usage(
                    response.usage.model_copy(
                        update={
                            "provider": self._client.provider_id,
                            "model": self._client.model_id,
                        }
                    )
                )
            try:
                return schema.model_validate_json(_json_object(response.text or ""))
            except (ValueError, ValidationError) as exc:
                last_error = exc
                logger.warning("research_model_bad_json", schema=schema.__name__)
                messages = [
                    *messages,
                    Message(role=MessageRole.ASSISTANT, content=response.text or ""),
                    Message(
                        role=MessageRole.USER,
                        content="That was not valid. Reply again with only the JSON object.",
                    ),
                ]
        raise ValueError(f"model did not return valid {schema.__name__}") from last_error


def _ok(result: SourceResult) -> bool:
    return result.health.status is HealthStatus.OK


class GatewayWeb:
    """`WebPort` over the web-search gateway (the only place that touches the network)."""

    def __init__(self, gateway: WebSearchGateway, *, tls_policy: TLSPolicy) -> None:
        self._gateway = gateway
        self._tls = tls_policy

    async def search(self, query: str, *, count: int) -> list[SearchHit]:
        result = await self._gateway.search(query, tls_policy=self._tls, count=count)
        raw: object = result.payload.get("hits", []) if _ok(result) else []
        hits: list[SearchHit] = []
        if isinstance(raw, list):
            for entry in cast("list[object]", raw):
                if isinstance(entry, dict):
                    item = cast("dict[str, object]", entry)
                    published = item.get("published")
                    hits.append(
                        SearchHit(
                            title=str(item.get("title", "")),
                            url=str(item.get("url", "")),
                            snippet=str(item.get("snippet", "")),
                            published=published if isinstance(published, str) else None,
                        )
                    )
        return hits

    async def fetch(self, url: str) -> FetchedPage | None:
        result = await self._gateway.fetch_page(url, tls_policy=self._tls)
        if not _ok(result):
            return None
        payload = result.payload
        return FetchedPage(
            url=str(payload.get("url", url)),
            title=str(payload.get("title", "")),
            text=str(payload.get("text", "")),
        )


def governance_guard(text: str) -> str:
    """Redact trading language from any text a report shows."""
    verdict = check_output(text, has_market_data=False)
    return verdict.redacted_text if verdict.redacted_text is not None else text


__all__ = ["GatewayWeb", "LLMResearchModel", "governance_guard"]
