"""Fixtures for the FastAPI HTTP surface: a fully-wired `create_app` over an in-memory SQLite
engine, a real tool registry (stub router, no network), a real built-in `SkillRegistry`, and an
`Orchestrator` driven by a stub `LLMClient` — no live provider, no upstream fetch, matching the
default-profile convention.

`client` is a Starlette `TestClient` over that app. `signed_in_user` inserts a real `users` row
and returns an `Authorization: Bearer` header for it, so the sign-in-required routes can be
exercised without running the whole OAuth flow (that flow is tested at its own seam)."""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from accounts.auth import issue_access_token
from api.app import create_app
from config.settings import Settings
from db import (
    models,  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
    rls,
)
from db.base import Base, ensure_schema
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
from tests.pg_support import app_role_url
from tests.tools.conftest import make_router
from tools import build_default_tool_registry
from tools.base import BaseTool, ToolContext
from tools.registry import ToolRegistry

_BUILT_INS = Path(__file__).resolve().parents[2] / "src" / "skills" / "built_ins"
_TEST_JWT_SECRET = "test-secret-not-a-real-key-0123456789abcdef"


class _StaticNoneClassifier(IntentClassifier):
    """Routes every message to the `none` framework — a direct reply with no tools/skills, so
    `/agent/query` produces deterministic text from the stub client without any router fetch."""

    async def _classify_inner(self, message: str, *, history: tuple[Message, ...] = ()) -> Routing:
        del message, history
        return Routing(framework="none", skills=(), reason="test static route")


class _StubAgentClient:
    """A stub `LLMClient` that always returns one fixed answer — deterministic, no network."""

    provider_id = "stub"
    model_id = "stub-model"
    capabilities = ModelCapabilities()

    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> LLMResponse:
        del messages, tools, native_web_search
        return LLMResponse(
            text=self._answer,
            tool_calls=(),
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> AsyncIterator[TextDeltaEvent | ToolCallEvent | StreamDoneEvent]:
        response = await self.chat(messages, tools, native_web_search=native_web_search)
        if response.text:
            yield TextDeltaEvent(text=response.text)
        for call in response.tool_calls:
            yield ToolCallEvent(tool_call=call)
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


@pytest.fixture(autouse=True)
def _no_live_title_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thread titling resolves the configured classifier model from the environment, so without
    this every first turn in these tests made real, paid model calls (the classifier
    model, then Anthropic) with whatever keys the developer's `.env` holds. Here a title is
    always the
    generator's own deterministic fallback (the first line of the message)."""
    monkeypatch.setattr("conversation.title_generator._resolve_client", lambda _client: None)


@pytest.fixture
def settings() -> Settings:
    # Provider creds forced to None, and voice providers forced to the deterministic `stub`
    # backend, so this fixture's behavior never depends on whatever a developer's local .env
    # happens to hold (e.g. a real `VOICE_STT_PROVIDER=faster_whisper` would otherwise try to
    # decode this suite's fake base64 audio and fail with a real codec error).
    return Settings(
        jwt_secret_key=_TEST_JWT_SECRET,
        env="development",
        google_client_id=None,
        google_client_secret=None,
        google_redirect_uri=None,
        voice_stt_provider="stub",
        voice_tts_provider="stub",
        secret_encryption_keys=None,
    )


@pytest.fixture
def agent_answer() -> str:
    return "台積電近期表現穩定。以上為資訊整理，非投資建議。"


def build_test_app(  # noqa: PLR0913 — one create_app collaborator per parameter, all keyword-only
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
    # Opt-in: run the same API suite against a real Postgres with row-level security enforced
    # (RLS_TEST_POSTGRES_URL=postgresql+asyncpg://..., ROW_LEVEL_SECURITY=true). SQLite otherwise.
    admin_pg_url = os.environ.get("RLS_TEST_POSTGRES_URL")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # noqa: SIM115 — closed below
    tmp.close()
    db_path = Path(tmp.name)
    database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

    async def _create_all() -> None:
        nonlocal database_url
        if admin_pg_url:
            # RLS does not apply to superusers, so the app connects as an ordinary role that owns
            # its tables; the admin connection only clears the previous app's schema.
            admin = create_async_engine(admin_pg_url)
            async with admin.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await admin.dispose()
            database_url = await app_role_url(admin_pg_url)
            engine = create_async_engine(database_url)
            await ensure_schema(engine)  # tables + (when ROW_LEVEL_SECURITY=true) the policies
        else:
            engine = create_async_engine(database_url)
            enable_sqlite_foreign_keys(engine)
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

    app_engine = create_async_engine(database_url)
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
    app = create_app(
        session_factory=session_factory,
        tool_registry=registry,
        skill_registry=skill_registry,
        orchestrator=orchestrator,
        settings=settings,
        rate_limiter=rate_limiter,  # type: ignore[arg-type]  # test double satisfies the seam
        quote_feed=quote_feed,  # type: ignore[arg-type]  # test double satisfies the seam
        data_update_broker=data_update_broker,  # type: ignore[arg-type]  # test double satisfies the seam
    )

    # `app_engine` is this test app's own long-lived engine — never disposed by `create_app`
    # itself (production apps live for the process lifetime). Left undisposed, its aiosqlite
    # connection's worker thread and its `asyncio.Future`s stay bound to whichever event loop
    # was running when the connection was opened — TestClient's own `anyio` blocking-portal
    # loop, which stops existing the moment that `with TestClient(...) as client:` block exits.
    # Hooking disposal into FastAPI's own ASGI shutdown event runs it on THAT SAME loop, before
    # the loop stops — the only safe place; disposing later (e.g. from the test's own thread,
    # after `TestClient.__exit__` has already torn the loop down) would hand aiosqlite's
    # cross-thread future-completion callback a dead loop, which is the exact native access-
    # violation crash this fixes (confirmed via a suite-wide run whose crash trace pointed at a
    # LATER test's `sign_in`/`asyncio.run` opening a fresh loop while an earlier test's
    # undisposed engine's worker thread was still alive on the same OS thread).
    @app.on_event("shutdown")
    async def _dispose_test_engine() -> None:  # pyright: ignore[reportUnusedFunction]
        await app_engine.dispose()

    return app


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
    its own `agent_client`/`classifier` to script a tool-calling, single-framework turn.

    Builds the app synchronously (no event loop needed) — `TestClient.__enter__` is what starts
    the one loop this app will ever run on, and its ASGI `startup` event (see `build_test_app`)
    is what creates the schema on THAT loop."""
    app = build_test_app(
        settings,
        agent_answer=agent_answer,
        tool_registry=tool_registry,
        rate_limiter=rate_limiter,
        quote_feed=quote_feed,
        data_update_broker=data_update_broker,
        agent_client=agent_client,
        classifier=classifier,
    )
    return TestClient(app)


@pytest.fixture
def client(settings: Settings, agent_answer: str) -> Iterator[TestClient]:
    with build_test_client(settings, agent_answer=agent_answer) as test_client:
        yield test_client


def client_with_symbol_store(
    settings: Settings,
    agent_answer: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """The shared `_client_with_store` shape every Parquet-roster-reading route test rebuilds
    per-file (`test_symbol_batch_routes.py`, `test_price_momentum_route.py`,
    `test_stock_heatmap_route.py`, and others) — a real tool registry over a fresh, per-test
    `PARQUET_ROOT`, so a test can `store.write_partition(...)` its own fixture rows and hit the
    route for real rather than stubbing the tool layer. Factored out here (rather than left
    duplicated at every call site) once the SonarCloud duplication gate flagged it."""
    monkeypatch.setenv("PARQUET_ROOT", str(tmp_path))
    test_client = build_test_client(settings, agent_answer=agent_answer)
    stub_router, _ = make_router(Symbol(id="2330", market=Market.TW), {})
    registry = build_default_tool_registry(stub_router)
    object.__setattr__(test_client.app.state.app_state, "tool_registry", registry)
    return test_client


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


def observe_db[T](client: TestClient, work: Callable[[], Awaitable[T]]) -> T:
    """Run `work` against the app's database as an observer: on the app's own event loop (an
    asyncpg connection is bound to the loop that opened it, so `asyncio.run` would hand it to a
    different one) and across users (a test inspecting rows is not one user, so row-level security,
    when the suite runs against Postgres with it on, must not filter its view)."""
    assert client.portal is not None

    async def _run() -> T:
        with rls.system_access():
            return await work()

    return client.portal.call(_run)


def sign_in(client: TestClient, settings: Settings, *, tier: Tier) -> tuple[dict[str, str], str]:
    """Insert a real `users` row at `tier` and return an `Authorization` header + the user id.

    Runs `_insert_user` via `client.portal.call(...)` — the SAME loop the `TestClient`'s own ASGI
    app and its `app_engine` connections already live on — rather than `asyncio.run(...)`, which
    would spin a second, throwaway loop concurrently with that portal's loop. Two live event
    loops in one process, each with its own aiosqlite worker thread, is exactly the situation
    that produced a native access-violation crash on Windows (see `build_test_app`'s own
    docstring for the full story); routing through the portal keeps this to one loop, period."""
    state = client.app.state.app_state
    assert client.portal is not None

    async def _do_insert() -> str:
        return await _insert_user(state.session_factory, tier=tier)

    user_id = client.portal.call(_do_insert)
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
