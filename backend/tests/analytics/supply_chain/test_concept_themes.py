"""Concept-theme loader, store and the mirror into the generic theme tables. Synthetic data."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from analytics.supply_chain.domain.concepts import ConceptPosition
from analytics.supply_chain.store.concept_protocol import ConceptCollisionError
from analytics.supply_chain.store.concept_store import SqlAlchemyConceptStore
from analytics.supply_chain.themes_io import loader
from analytics.supply_chain.themes_io.loader import (
    ConceptFileError,
    load_concept_themes,
    parse_concept_files,
)
from analytics.themes.models import ThemeKind, ThemeSource
from analytics.themes.store import SqlAlchemyThemeStore

ALPHA = """
key: alpha
name_zh: 甲題材
description: first
related: [乙題材, nothing-here]
members:
  - {symbol: "9001", name: One, position: upstream, origin: seeded}
  - {symbol: 9002, name: Two, position: downstream, origin: curated}
  - {symbol: ZZZA, name: Three, position: related, origin: proposed}
"""
BETA = """
key: beta
name_zh: 乙題材
related: [alpha]
members:
  - {symbol: "9001", position: midstream}
"""


def _write(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


@pytest.fixture
def concepts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(loader, "reference_path", tmp_path.joinpath)
    return tmp_path / "themes" / "concepts"


def test_parse_derives_the_market_and_accepts_numeric_symbols(concepts_dir: Path) -> None:
    _write(concepts_dir, "a.yaml", ALPHA)
    themes, files = parse_concept_files(concepts_dir)
    assert files == 1
    assert [(m.symbol, m.market) for m in themes[0].members] == [
        ("9001", "TW"),
        ("9002", "TW"),
        ("ZZZA", "US"),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "- not a mapping\n",
        "key: x\nname_zh: X\nmembers:\n  - {symbol: '9001', position: sideways}\n",
        "key: x\nname_zh: X\nmembers:\n  - {symbol: '??', position: upstream}\n",
        "key: x\nname_zh: X\nbogus: 1\n",
        "key: x\nname_zh: X\nmembers:\n  - {symbol: '9001', position: upstream}\n"
        "  - {symbol: '9001', position: related}\n",
        "key: [unterminated\n",
    ],
)
def test_a_bad_file_is_rejected_naming_the_file(concepts_dir: Path, text: str) -> None:
    _write(concepts_dir, "bad.yaml", text)
    with pytest.raises(ConceptFileError, match=r"bad\.yaml"):
        parse_concept_files(concepts_dir)


def test_duplicate_keys_across_files_are_rejected(concepts_dir: Path) -> None:
    _write(concepts_dir, "a.yaml", ALPHA)
    _write(concepts_dir, "b.yaml", ALPHA)
    with pytest.raises(ConceptFileError, match="duplicate"):
        parse_concept_files(concepts_dir)


async def test_load_writes_concept_tables_and_mirrors_into_the_theme_tables(
    concepts_dir: Path, session_factory: async_sessionmaker
) -> None:
    _write(concepts_dir, "a.yaml", ALPHA)
    _write(concepts_dir, "b.yaml", BETA)
    async with session_factory() as session:
        result = await load_concept_themes(SqlAlchemyConceptStore(session))
        await session.commit()
    assert (result.themes, result.members, result.created, result.deleted) == (2, 4, 2, 0)

    async with session_factory() as session:
        themes = SqlAlchemyThemeStore(session)
        mirrored = await themes.get_theme_by_key(key="alpha")
        assert mirrored is not None
        assert (mirrored.kind, mirrored.source) == (ThemeKind.CONCEPT, ThemeSource.CONCEPT_RESEARCH)
        details = {m.symbol: m for m in await themes.get_theme_member_details(theme_key="alpha")}
        assert {s: m.sub_industry for s, m in details.items()} == {
            "9001": "上游",
            "9002": "下游",
            "ZZZA": "相關公司",
        }
        assert {m.confidence for m in details.values()} == {0.6}
        assert details["ZZZA"].market == "US"


async def test_symbol_reverse_lookup_sees_the_concept_theme(
    concepts_dir: Path, session_factory: async_sessionmaker
) -> None:
    _write(concepts_dir, "a.yaml", ALPHA)
    _write(concepts_dir, "b.yaml", BETA)
    async with session_factory() as session:
        await load_concept_themes(SqlAlchemyConceptStore(session))
        await session.commit()
        found = await SqlAlchemyThemeStore(session).get_symbol_themes(symbol="9001", market="TW")
    assert [(t.theme_key, t.kind, t.sub_industry) for t in found] == [
        ("alpha", ThemeKind.CONCEPT, "上游"),
        ("beta", ThemeKind.CONCEPT, "中游"),
    ]


async def test_reload_is_idempotent_and_picks_up_edits_and_removals(
    concepts_dir: Path, session_factory: async_sessionmaker
) -> None:
    _write(concepts_dir, "a.yaml", ALPHA)
    _write(concepts_dir, "b.yaml", BETA)
    async with session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        await load_concept_themes(store)
        again = await load_concept_themes(store)
        await session.commit()
    assert (again.created, again.deleted) == (0, 0)

    _write(concepts_dir, "a.yaml", ALPHA.replace("position: upstream", "position: downstream"))
    (concepts_dir / "b.yaml").unlink()
    async with session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        result = await load_concept_themes(store)
        await session.commit()
        assert (result.themes, result.deleted) == (1, 1)
        themes = SqlAlchemyThemeStore(session)
        assert await themes.get_theme_by_key(key="beta") is None
        assert await themes.get_theme_members(theme_key="beta") == []
        assert (await store.counts()).concept_themes == 1
        assert (await store.concept_positions("alpha"))[
            ("9001", "TW")
        ] is ConceptPosition.DOWNSTREAM
        members = await themes.get_theme_member_details(theme_key="alpha")
        assert {m.sub_industry for m in members} == {"下游", "相關公司"}


async def test_missing_or_empty_directory_is_a_noop_that_keeps_existing_rows(
    concepts_dir: Path, session_factory: async_sessionmaker
) -> None:
    _write(concepts_dir, "a.yaml", ALPHA)
    async with session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        await load_concept_themes(store)
        await session.commit()
    (concepts_dir / "a.yaml").unlink()
    async with session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        result = await load_concept_themes(store)
        assert result.skipped
        assert (await store.counts()).concept_themes == 1


async def test_a_bad_file_writes_nothing(
    concepts_dir: Path, session_factory: async_sessionmaker
) -> None:
    _write(concepts_dir, "a.yaml", ALPHA)
    _write(concepts_dir, "z.yaml", "key: z\nname_zh: Z\nmembers: [{symbol: '9001'}]\n")
    async with session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        with pytest.raises(ConceptFileError):
            await load_concept_themes(store)
        assert (await store.counts()).concept_themes == 0


async def test_a_key_used_by_an_industry_theme_is_refused_without_touching_it(
    concepts_dir: Path, session_factory: async_sessionmaker
) -> None:
    async with session_factory() as session:
        await SqlAlchemyThemeStore(session).upsert_theme(
            key="alpha",
            name_zh="industry",
            kind=ThemeKind.INDUSTRY,
            source=ThemeSource.INDUSTRY_CHAIN,
        )
        await session.commit()
    _write(concepts_dir, "a.yaml", ALPHA)
    async with session_factory() as session:
        with pytest.raises(ConceptCollisionError):
            await load_concept_themes(SqlAlchemyConceptStore(session))
        theme = await SqlAlchemyThemeStore(session).get_theme_by_key(key="alpha")
        assert theme is not None
        assert theme.kind is ThemeKind.INDUSTRY


async def test_concept_info_resolves_related_names_to_keys(
    concepts_dir: Path, session_factory: async_sessionmaker
) -> None:
    _write(concepts_dir, "a.yaml", ALPHA)
    _write(concepts_dir, "b.yaml", BETA)
    async with session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        await load_concept_themes(store)
        info = await store.concept_info()
        assert info["alpha"].description == "first"
        assert info["alpha"].related_keys == ("beta",)  # by name; the unknown one is dropped
        assert info["beta"].related_keys == ("alpha",)  # by key
        assert list(await store.concept_info(["beta"])) == ["beta"]
