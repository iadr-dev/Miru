"""Entity catalog / mention loader and store. Synthetic data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from sqlalchemy.ext.asyncio import async_sessionmaker

from analytics.supply_chain.domain.concepts import EntityType
from analytics.supply_chain.entities_io import loader
from analytics.supply_chain.entities_io.loader import (
    EntityFileError,
    load_entities,
    parse_entity_files,
)
from analytics.supply_chain.store.concept_store import SqlAlchemyConceptStore

CATALOG = [
    {"id": "Alpha Parts", "type": "tw_company", "mentions": 4, "reports": 2, "symbol": "9001"},
    {"id": "Gamma Corp", "type": "intl_company", "mentions": 2, "reports": 1, "symbol": "ZZZA"},
    {"id": "Widget Tech", "type": "technology", "mentions": 9, "reports": 3, "aliases": ["WT"]},
    {"id": "Widget App", "type": "application", "mentions": 3, "reports": 2},
]


def _mention(symbol: str, entity: str, count: int = 1, section: str = "business") -> dict:
    return {"symbol": symbol, "entity": entity, "section": section, "role": None, "count": count}


MENTIONS = [
    _mention("9001", "Widget Tech", 3),
    _mention("9001", "Widget Tech", 2, "chain"),
    _mention("9002", "Widget Tech", 1),
    {**_mention("9002", "Gamma Corp", 2, "counterparty"), "role": "customer"},
]


def _write(directory: Path, catalog: object = CATALOG, mentions: list[dict] | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "catalog.yaml").write_text(yaml.safe_dump(catalog), encoding="utf-8")
    lines = [json.dumps(m) for m in (MENTIONS if mentions is None else mentions)]
    (directory / "mentions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def entities_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(loader, "reference_path", tmp_path.joinpath)
    return tmp_path / "entities"


def test_parse_derives_markets_and_keeps_aliases(entities_dir: Path) -> None:
    _write(entities_dir)
    parsed = parse_entity_files(entities_dir)
    by_id = {e.id: e for e in parsed.entities}
    assert (by_id["Alpha Parts"].market, by_id["Gamma Corp"].market) == ("TW", "US")
    assert by_id["Widget Tech"].aliases == ("WT",)
    assert by_id["Widget Tech"].type is EntityType.TECHNOLOGY
    assert len(parsed.mentions) == 4


def test_unknown_entities_and_symbols_are_dropped_and_counted(entities_dir: Path) -> None:
    _write(
        entities_dir,
        mentions=[
            *MENTIONS,
            _mention("9001", "Not In Catalog"),
            _mention("zz-9", "Widget Tech"),
            _mention("9001", "Widget Tech", 7),  # repeated key keeps the first row
        ],
    )
    parsed = parse_entity_files(entities_dir)
    assert (len(parsed.mentions), parsed.dropped, parsed.duplicates) == (4, 2, 1)
    assert {m.count for m in parsed.mentions if m.symbol == "9001"} == {3, 2}


@pytest.mark.parametrize(
    "catalog",
    [
        {"not": "a list"},
        [{"id": "A", "type": "spaceship"}],
        [{"id": "A", "type": "product", "symbol": "??"}],
        [{"id": "A", "type": "product", "extra": 1}],
        [{"id": "A", "type": "product"}, {"id": "A", "type": "material"}],
    ],
)
def test_a_bad_catalog_row_is_rejected(entities_dir: Path, catalog: object) -> None:
    _write(entities_dir, catalog=catalog)
    with pytest.raises(EntityFileError, match=r"catalog\.yaml"):
        parse_entity_files(entities_dir)


@pytest.mark.parametrize(
    "line",
    [
        "{not json",
        '{"symbol": "9001"}',
        '{"symbol":"9001","entity":"A","section":"nope","role":null,"count":1}',
    ],
)
def test_a_bad_mention_line_is_rejected_with_its_number(entities_dir: Path, line: str) -> None:
    _write(entities_dir)
    with (entities_dir / "mentions.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    with pytest.raises(EntityFileError, match=r"mentions\.jsonl line 5"):
        parse_entity_files(entities_dir)


async def test_load_then_reload_is_idempotent(
    entities_dir: Path, session_factory: async_sessionmaker
) -> None:
    _write(entities_dir)
    async with session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        first = await load_entities(store)
        second = await load_entities(store)
        await session.commit()
        counts = await store.counts()
    assert (first.entities, first.mentions, first.created_mentions) == (4, 4, 4)
    assert (second.created_mentions, second.deleted_mentions, second.deleted_entities) == (0, 0, 0)
    assert (counts.entities, counts.entity_mentions) == (4, 4)
    assert counts.entities_by_type["technology"] == 1


async def test_reload_applies_edits_and_deletes_what_the_files_dropped(
    entities_dir: Path, session_factory: async_sessionmaker
) -> None:
    _write(entities_dir)
    async with session_factory() as session:
        await load_entities(SqlAlchemyConceptStore(session))
        await session.commit()
    _write(
        entities_dir,
        catalog=CATALOG[:3],  # "Widget App" removed
        mentions=[_mention("9001", "Widget Tech", 8), _mention("9002", "Widget Tech", 1)],
    )
    async with session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        result = await load_entities(store)
        await session.commit()
        assert (result.deleted_entities, result.deleted_mentions) == (1, 2)
        assert await store.entity_totals("9001") == {"Widget Tech": 8}
        assert "Widget App" not in await store.get_entities(["Widget App", "Widget Tech"])
        assert (await store.counts()).entity_mentions == 2


async def test_dropping_an_entity_removes_its_mentions(
    entities_dir: Path, session_factory: async_sessionmaker
) -> None:
    _write(entities_dir)
    async with session_factory() as session:
        await load_entities(SqlAlchemyConceptStore(session))
        await session.commit()
    _write(entities_dir, catalog=CATALOG[1:], mentions=[_mention("9002", "Gamma Corp", 2)])
    async with session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        await load_entities(store)
        await session.commit()
        assert await store.entity_totals("9001") == {}


async def test_missing_catalog_or_empty_catalog_is_a_noop(
    entities_dir: Path, session_factory: async_sessionmaker
) -> None:
    async with session_factory() as session:
        assert (await load_entities(SqlAlchemyConceptStore(session))).skipped
    _write(entities_dir)
    async with session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        await load_entities(store)
        await session.commit()
    (entities_dir / "catalog.yaml").write_text("", encoding="utf-8")
    async with session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        assert (await load_entities(store)).skipped
        assert (await store.counts()).entities == 4


async def test_a_bad_row_writes_nothing(
    entities_dir: Path, session_factory: async_sessionmaker
) -> None:
    _write(entities_dir)
    with (entities_dir / "mentions.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{oops\n")
    async with session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        with pytest.raises(EntityFileError):
            await load_entities(store)
        assert (await store.counts()).entities == 0


async def test_a_missing_mentions_file_loads_the_catalog_alone(
    entities_dir: Path, session_factory: async_sessionmaker
) -> None:
    _write(entities_dir)
    (entities_dir / "mentions.jsonl").unlink()
    async with session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        result = await load_entities(store)
        assert (result.entities, result.mentions) == (4, 0)
