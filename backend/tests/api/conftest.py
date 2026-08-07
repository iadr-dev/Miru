"""Fixtures for the FastAPI HTTP surface: a fully-wired `create_app` over an in-memory SQLite
engine, a real tool registry (stub router, no network), a real built-in `SkillRegistry`, and an
`Orchestrator` driven by a stub `LLMClient` — no live provider, no upstream fetch, matching the
default-profile convention.

`client` is a Starlette `TestClient` over that app. `signed_in_user` inserts a real `users` row
and returns an `Authorization: Bearer` header for it, so the sign-in-required routes can be
exercised without running the whole OAuth flow (that flow is tested at its own seam)."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from accounts.auth import issue_access_token
from api.app import create_app
from config.settings import Settings
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.models import UserRow
from db.sqlite_pragma import enable_sqlite_foreign_keys
from llm.base import (
    LLMResponse,
    Message,
    ModelCapabilities,
    StopReason,
    StreamDoneEvent,
    TextDeltaEvent,
    TokenUsage,
    ToolCallEvent,
    ToolSpec,
)
from orchestrator.classifier import IntentClassifier
from orchestrator.models import Routing
from orchestrator.orchestrator import Orchestrator
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from skills.registry import SkillRegistry
from tests.tools.conftest import make_router
from tools import build_default_tool_registry
from tools.base import BaseTool, ToolContext
from tools.registry import ToolRegistry

_BUILT_INS = Path(__file__).resolve().parents[2] / "src" / "skills" / "built_ins"
_TEST_JWT_SECRET = "test-secret-not-a-real-key-0123456789abcdef"


class _StaticNoneClassifier(IntentClassifier):
    """Routes every message to the `none` framework — a direct reply with no tools/skills, so
    `/agent/query` produces deterministic text from the stub client without any router fetch."""

    async def _classify_inner(self, message: str) -> Routing:
        del message
        return Routing(framework="none", skills=(), reason="test static route")


class _StubAgentClient:
    """A stub `LLMClient` that always returns one fixed answer — deterministic, no network."""

    provider_id = "stub"
    model_id = "stub-model"
    capabilities = ModelCapabilities()

    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        del messages, tools
        return LLMResponse(
            text=self._answer,
            tool_calls=(),
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
        )

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[TextDeltaEvent | ToolCallEvent | StreamDoneEvent]:
        response = await self.chat(messages, tools)
        if response.text:
            yield TextDeltaEvent(text=response.text)
        yield StreamDoneEvent(stop_reason=response.stop_reason, usage=response.usage)

    async def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content or "") for m in messages) // 4 or 1


def _build_registry():
    symbol = Symbol(id="2330", market=Market.TW)
    result = SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="stub caveat — recorded fixture, not live",
        payload={
            "bar": {
                "symbol": "2330",
                "market": "TW",
                "trade_date": "2024-01-02",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 12345,
            }
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, _ = make_router(symbol, dict.fromkeys(Capability, result))
    return build_default_tool_registry(router)


@pytest.fixture
def settings() -> Settings:
    # Provider creds forced to None so the OAuth-unconfigured path is deterministic regardless of
    # whatever a developer's local .env happens to hold.
    return Settings(
        jwt_secret_key=_TEST_JWT_SECRET,
        aitw_env="development",
        google_client_id=None,
        google_client_secret=None,
        google_redirect_uri=None,
    )


@pytest.fixture
def agent_answer() -> str:
    return "台積電近期表現穩定。以上為資訊整理，非投資建議。"


def build_test_app(  # noqa: PLR0913 — one create_app collaborator per param, all keyword-only
    settings: Settings,
    *,
    agent_answer: str,
    tool_registry: ToolRegistry | None = None,
    rate_limiter: object | None = None,
    quote_feed: object | None = None,
    data_update_broker: object | None = None,
    agent_client: object | None = None,
    classifier: IntentClassifier | None = None,
):
    """Assemble a fully-wired FastAPI app over a fresh temp-file SQLite. The `TestClient` wrapper
    (`build_test_client`) and the async-streaming tests both build on this."""
    # A temp-file SQLite (not :memory:) so the schema created here is visible to the connections
    # the request-handling opens — an in-memory DB is per-connection.
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # noqa: SIM115 — closed below
    tmp.close()
    db_path = Path(tmp.name)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    enable_sqlite_foreign_keys(engine)

    async def _create_all() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    # Run schema-creation on its own event loop in a worker thread, so this builder is safe to
    # call from a synchronous test (via `asyncio.run`) OR from inside an already-running loop (the
    # async streaming tests) — `asyncio.run` cannot nest inside a running loop.
    def _run_in_thread() -> None:
        asyncio.run(_create_all())

    thread = threading.Thread(target=_run_in_thread)
    thread.start()
    thread.join()

    app_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    enable_sqlite_foreign_keys(app_engine)
    session_factory = async_sessionmaker(app_engine, expire_on_commit=False)
    skill_registry = SkillRegistry()
    skill_registry.load([_BUILT_INS])
    registry = tool_registry or _build_registry()
    tool_lookup = {tool.name: tool for tool in registry.all_tools()}

    resolved_agent_client = agent_client or _StubAgentClient(agent_answer)
    orchestrator = Orchestrator(
        registry=skill_registry,
        tool_lookup=tool_lookup,
        classifier=classifier or _StaticNoneClassifier(skill_registry),
        agent_client=resolved_agent_client,  # type: ignore[arg-type]  # stub satisfies the seam
        subagent_client=resolved_agent_client,  # type: ignore[arg-type]  # stub satisfies the seam
        settings=settings,
    )
    return create_app(
        session_factory=session_factory,
        tool_registry=registry,
        skill_registry=skill_registry,
        orchestrator=orchestrator,
        settings=settings,
        rate_limiter=rate_limiter,  # type: ignore[arg-type]  # test double satisfies the seam
        quote_feed=quote_feed,  # type: ignore[arg-type]  # test double satisfies the seam
        data_update_broker=data_update_broker,  # type: ignore[arg-type]  # test double satisfies the seam
    )


def build_test_client(  # noqa: PLR0913 — passthrough to build_test_app, all keyword-only
    settings: Settings,
    *,
    agent_answer: str,
    tool_registry: ToolRegistry | None = None,
    rate_limiter: object | None = None,
    quote_feed: object | None = None,
    data_update_broker: object | None = None,
    agent_client: object | None = None,
    classifier: IntentClassifier | None = None,
) -> TestClient:
    """Assemble a fully-wired `TestClient`. A test may pass its own `tool_registry` (e.g. one
    holding a sponsor-classed tool) to exercise the entitlement gate over the HTTP boundary, or
    its own `agent_client`/`classifier` to script a tool-calling, single-framework turn."""
    return TestClient(
        build_test_app(
            settings,
            agent_answer=agent_answer,
            tool_registry=tool_registry,
            rate_limiter=rate_limiter,
            quote_feed=quote_feed,
            data_update_broker=data_update_broker,
            agent_client=agent_client,
            classifier=classifier,
        )
    )


@pytest.fixture
def client(settings: Settings, agent_answer: str) -> Iterator[TestClient]:
    with build_test_client(settings, agent_answer=agent_answer) as test_client:
        yield test_client


async def _insert_user(session_factory, *, tier: Tier) -> str:
    now = datetime.now(UTC)
    user_id = str(uuid.uuid4())
    async with session_factory() as session:
        session.add(
            UserRow(
                id=user_id,
                email=f"{user_id}@example.test",
                display_name="Test User",
                tier=tier.value,
                personality_id="steady",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    return user_id


def sign_in(client: TestClient, settings: Settings, *, tier: Tier) -> tuple[dict[str, str], str]:
    """Insert a real `users` row at `tier` and return an `Authorization` header + the user id."""
    state = client.app.state.app_state
    user_id = asyncio.run(_insert_user(state.session_factory, tier=tier))
    token = issue_access_token(user_id=user_id, settings=settings)
    return {"Authorization": f"Bearer {token}"}, user_id


@pytest.fixture
def signed_in_user(client: TestClient, settings: Settings):
    return sign_in(client, settings, tier=Tier.FREE)


def build_sponsor_registry() -> ToolRegistry:
    """A registry whose `get_price_quote` is sponsor-classed — lets an HTTP test prove the
    quote route returns a masked teaser for a Free caller and real data for a Premium one, i.e.
    that the sponsor gate holds over the HTTP boundary exactly as at the tool-dispatch seam."""

    class _Input(BaseModel):
        id: str
        market: Market

    class _Output(BaseModel):
        close: float

    class _SponsorQuoteTool(BaseTool):
        name = "get_price_quote"
        description = "Sponsor-classed quote (test)."
        input_model: type[BaseModel] = _Input
        output_model: type[BaseModel] = _Output
        data_class = DataClass.SPONSOR

        async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
            del args, ctx
            return _Output(close=101.0)

    registry = ToolRegistry()
    registry.register(_SponsorQuoteTool(), immediate=False)
    return registry
