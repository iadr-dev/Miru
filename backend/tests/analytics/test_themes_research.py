"""Concept-theme research pipeline + in-theme edge generation tests.

The failure-mode guard: these do not exercise the pipeline only with well-behaved mocked models.
The load-bearing test drives the REAL `research_concept_theme` with a stubbed researcher that
proposes a clearly-fabricated code ("9999") alongside real ones, and proves — by querying the REAL
`ThemeStore` after the pipeline runs — that the fabricated code never lands in a stored
`theme_memberships` row, while the real ones do and carry the correct reconciliation confidence. A
second test proves the edge generator rejects an LLM-proposed edge to a symbol outside the theme's
member list. No live network / no live LLM — the web-search gateway and every model client are
scripted stubs.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from analytics.themes.__main__ import run_operator_pipeline
from analytics.themes.models import EdgeSource, ThemeKind, ThemeMember, ThemeSource
from analytics.themes.research import (
    ConceptThemeSpec,
    generate_in_theme_edges,
    research_concept_theme,
    seed_supply_chain_edges,
)
from analytics.themes.store import SqlAlchemyThemeStore
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.sqlite_pragma import enable_sqlite_foreign_keys
from llm.base import LLMResponse, Message, StopReason, TokenUsage, ToolSpec
from schemas.enums import DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class _ScriptedLLM:
    """A minimal `LLMClient` that returns one scripted text reply per `chat()` call. Distinct from
    the runtime `StubLLMClient` (which scripts full `LLMResponse`s) — here each test just needs to
    hand the model a raw string and have it come back as `.text`."""

    provider_id = "stub"
    model_id = "stub-model"

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self._i = 0

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        reply = self._replies[self._i]
        self._i += 1
        return LLMResponse(
            text=reply,
            tool_calls=(),
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


class _RaisingLLM:
    """A model client whose `chat()` always raises — for the verifier fail-open test."""

    provider_id = "stub"
    model_id = "stub-model"

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        raise RuntimeError("verifier provider unreachable")


class _StubWebSearch:
    """A `WebSearchGateway`-shaped stub returning a fixed hit list — no network."""

    def __init__(self, hits: list[dict[str, object]] | None = None) -> None:
        self._hits = hits or []

    async def search(
        self, query: str, *, tls_policy: TLSPolicy = TLSPolicy.STRICT, count: int = 10
    ) -> SourceResult:
        return SourceResult(
            source_id="WEB",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.REFERENCE,
            fetched_at=datetime.now(UTC),
            currentness_caveat="stub web search — test only, cite the source.",
            payload={"hits": self._hits},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )


def _picks_json(*picks: tuple[str, str, str]) -> str:
    return json.dumps(
        [{"symbol": s, "sub_industry": si, "reason": r} for s, si, r in picks],
        ensure_ascii=False,
    )


async def _seed_universe(session_factory: async_sessionmaker, symbols: list[str]) -> None:
    """Populate `theme_memberships` with a real industry-chain snapshot — the stored universe the
    fabrication safeguard checks against. Mirrors what the industry-chain seed produces."""
    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        await store.upsert_theme(
            key="半導體",
            name_zh="半導體",
            kind=ThemeKind.INDUSTRY,
            source=ThemeSource.INDUSTRY_CHAIN,
        )
        await store.replace_theme_members(
            theme_key="半導體",
            source=ThemeSource.INDUSTRY_CHAIN,
            members=[
                ThemeMember(
                    symbol=sym,
                    market="TW",
                    sub_industry="晶圓代工",
                    confidence=1.0,
                    source=ThemeSource.INDUSTRY_CHAIN,
                )
                for sym in symbols
            ],
        )
        await session.commit()


@pytest.mark.governance
async def test_fabricated_symbol_never_persisted(
    session_factory: async_sessionmaker,
) -> None:
    """A researcher proposes a clearly-fake code ("9999") alongside real ones; after the full
    pipeline the fake is absent from the stored theme, the real ones present, and the agreed vs
    disputed confidence scoring is correct."""
    # The real universe: these codes exist in industry-chain memberships. "9999" does NOT.
    await _seed_universe(session_factory, ["2330", "2454", "3711"])

    # Researcher A: proposes 2330 (real), 2454 (real), 9999 (FAKE).
    researcher_a = _ScriptedLLM(
        [
            _picks_json(
                ("2330", "晶圓代工", "先進製程龍頭"),
                ("2454", "IC設計", "手機晶片"),
                ("9999", "封裝", "捏造的代號"),
            )
        ]
    )
    # Researcher B: proposes 2330 (agreed with A), 3711 (disputed, only B).
    researcher_b = _ScriptedLLM(
        [
            _picks_json(
                ("2330", "晶圓代工", "先進製程"),
                ("3711", "封測", "先進封裝"),
            )
        ]
    )
    # Verifier: keep both disputed real codes (2454 from A, 3711 from B).
    verifier = _ScriptedLLM([json.dumps(["2454", "3711"])])

    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        outcome = await research_concept_theme(
            ConceptThemeSpec(
                key="cowos-advanced-packaging",
                name_zh="CoWoS先進封裝",
                name_en="CoWoS",
                hint="先進封裝供應鏈",
            ),
            store=store,
            web_search=_StubWebSearch(),  # type: ignore[arg-type]
            researcher_a=researcher_a,  # type: ignore[arg-type]
            researcher_b=researcher_b,  # type: ignore[arg-type]
            verifier=verifier,  # type: ignore[arg-type]
            run_id="run-1",
        )
        await session.commit()

    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        members = await store.get_theme_members(theme_key="cowos-advanced-packaging")
        symbol_themes_2330 = await store.get_symbol_themes(symbol="2330", market="TW")
        symbol_themes_3711 = await store.get_symbol_themes(symbol="3711", market="TW")

    # The fabricated code is NOT persisted; the real ones are.
    assert "9999" not in members
    assert set(members) == {"2330", "2454", "3711"}
    assert outcome.dropped_fabricated == 1

    # Confidence scoring: 2330 agreed (0.9); 3711 disputed-then-verifier-kept (0.6).
    concept_2330 = next(
        st for st in symbol_themes_2330 if st.theme_key == "cowos-advanced-packaging"
    )
    concept_3711 = next(
        st for st in symbol_themes_3711 if st.theme_key == "cowos-advanced-packaging"
    )
    assert concept_2330.confidence == pytest.approx(0.9)
    assert concept_3711.confidence == pytest.approx(0.6)


@pytest.mark.governance
async def test_verifier_failure_fails_open_but_still_filters_universe(
    session_factory: async_sessionmaker,
) -> None:
    """If the verifier call raises, the pipeline fails open (keeps the disputed union) — but a
    fabricated disputed code is still dropped by the universe filter on that fail-open path."""
    await _seed_universe(session_factory, ["2330", "2454"])

    researcher_a = _ScriptedLLM([_picks_json(("2330", "晶圓代工", "龍頭"))])
    # B proposes a real disputed (2454) and a fake disputed (8888).
    researcher_b = _ScriptedLLM(
        [_picks_json(("2454", "IC設計", "只有B提"), ("8888", "封裝", "捏造"))]
    )
    verifier = _RaisingLLM()

    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        await research_concept_theme(
            ConceptThemeSpec(key="ai-server", name_zh="AI伺服器", name_en="AI", hint="AI"),
            store=store,
            web_search=_StubWebSearch(),  # type: ignore[arg-type]
            researcher_a=researcher_a,  # type: ignore[arg-type]
            researcher_b=researcher_b,  # type: ignore[arg-type]
            verifier=verifier,  # type: ignore[arg-type]
            run_id="run-2",
        )
        await session.commit()

    async with session_factory() as session:
        members = await SqlAlchemyThemeStore(session).get_theme_members(theme_key="ai-server")

    # Fail-open kept the real disputed 2454; the universe filter still dropped the fake 8888.
    assert set(members) == {"2330", "2454"}


async def test_edge_generation_rejects_out_of_theme_symbol(
    session_factory: async_sessionmaker,
) -> None:
    """The in-theme edge generator must drop any proposed edge whose endpoint is not a member of
    the theme, even if the model proposes it."""
    # Build a concept theme with 3 members directly (bypass the LLM research for this test).
    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        await store.upsert_theme(
            key="ai-server",
            name_zh="AI伺服器",
            kind=ThemeKind.CONCEPT,
            source=ThemeSource.CONCEPT_RESEARCH,
        )
        await store.replace_theme_members(
            theme_key="ai-server",
            source=ThemeSource.CONCEPT_RESEARCH,
            members=[
                ThemeMember(
                    symbol=s, market="TW", sub_industry="", source=ThemeSource.CONCEPT_RESEARCH
                )
                for s in ("2330", "2382", "3231")
            ],
        )
        await session.commit()

    # The extractor proposes one valid in-theme edge and one edge to "AAPL" (outside the members).
    extractor = _ScriptedLLM(
        [
            json.dumps(
                [
                    {"from": "2330", "to": "2382", "edge_type": "supplies", "confidence": "high"},
                    {"from": "2382", "to": "AAPL", "edge_type": "customer", "confidence": "high"},
                ]
            )
        ]
    )

    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        written = await generate_in_theme_edges(
            store=store,
            web_search=_StubWebSearch(),  # type: ignore[arg-type]
            extractor=extractor,  # type: ignore[arg-type]
            run_id="run-3",
            theme_keys=["ai-server"],
        )
        await session.commit()

    async with session_factory() as session:
        edges = await SqlAlchemyThemeStore(session).get_edges_from(from_symbol="2330")
        out_of_theme = await SqlAlchemyThemeStore(session).get_edges_from(from_symbol="2382")

    assert written == 1
    assert len(edges) == 1
    assert edges[0].to_symbol == "2382"
    # The out-of-theme edge to AAPL was rejected — 2382 has no outgoing edge stored.
    assert out_of_theme == []


async def test_operator_pipeline_end_to_end(
    session_factory: async_sessionmaker,
) -> None:
    """Drive the REAL operator orchestration (`run_operator_pipeline`, the exact function `main`
    calls) against stubbed collaborators — proving the whole path runs and writes real rows, not
    just the pieces in isolation."""
    await _seed_universe(session_factory, ["2330", "2454", "3711"])

    specs = [
        ConceptThemeSpec(
            key="cowos-advanced-packaging",
            name_zh="CoWoS先進封裝",
            name_en="CoWoS",
            hint="先進封裝供應鏈",
        )
    ]
    picks = _picks_json(
        ("2330", "晶圓代工", "龍頭"),
        ("2454", "IC設計", "晶片"),
        ("3711", "封測", "封裝"),
    )
    edge_reply = json.dumps(
        [{"from": "2330", "to": "3711", "edge_type": "supplies", "confidence": "medium"}]
    )

    await run_operator_pipeline(
        session_factory=session_factory,
        web_search=_StubWebSearch(),  # type: ignore[arg-type]
        researcher_a=_ScriptedLLM([picks]),  # type: ignore[arg-type]
        researcher_b=_ScriptedLLM([picks]),  # type: ignore[arg-type]
        verifier=_ScriptedLLM([json.dumps([])]),  # type: ignore[arg-type]
        extractor=_ScriptedLLM([edge_reply]),  # type: ignore[arg-type]
        specs=specs,
        run_id="e2e-run",
    )

    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        members = await store.get_theme_members(theme_key="cowos-advanced-packaging")
        seed_edges = await store.get_edges_from(from_symbol="2330")

    # All three researcher-agreed members landed (all real, so no drops).
    assert set(members) == {"2330", "2454", "3711"}
    # Both the bootstrap seed edges AND the in-theme extraction edge share the from_symbol 2330.
    to_symbols = {e.to_symbol for e in seed_edges}
    assert {"AAPL", "NVDA", "AMD"}.issubset(to_symbols)  # seed edges
    assert "3711" in to_symbols  # in-theme extracted edge


async def test_seed_supply_chain_edges_idempotent(
    session_factory: async_sessionmaker,
) -> None:
    """Loading the bootstrap edges twice replaces rather than duplicates."""
    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        first = await seed_supply_chain_edges(store=store, run_id="seed-1")
        await session.commit()
    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        second = await seed_supply_chain_edges(store=store, run_id="seed-2")
        await session.commit()

    assert first == second
    async with session_factory() as session:
        edges = await SqlAlchemyThemeStore(session).get_edges_from(from_symbol="2330")
    # 2330 supplies AAPL, NVDA, AMD, QCOM, AVGO in the seed — exactly the seeded set, no
    # duplication (idempotent reload).
    assert {e.to_symbol for e in edges} == {"AAPL", "NVDA", "AMD", "QCOM", "AVGO"}
    assert all(e.source is EdgeSource.SEED for e in edges)
